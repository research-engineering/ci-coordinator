from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.report_factories import measurement_report
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError
from tests.integration.persistence._ci_economics_support import database_now, provider_source, store
from tests.integration.persistence._temporal_lock_support import wait_for_blocked_operation

from ci_coordinator.ci_economics.ports import MeasurementReportWriteResult
from ci_coordinator.ci_economics.reports import (
    MAX_REPORTS_PER_ATTEMPT,
    MeasurementReportOrigin,
    StoredMeasurementReport,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_measurement_report_codec import encode_measurement_report
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
    reconciliation_subjects,
)

pytestmark = pytest.mark.persistence
_ORIGIN = MeasurementReportOrigin("c" * 64, "d" * 64)


def test_report_replay_preserves_first_origin_and_has_no_reconciliation_dependency(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            source = provider_source(303, await database_now(engine))
            report = measurement_report(attempt=source.attempt)
            repository = store(engine)
            assert (
                await repository.record_measurement_report(source, report, _ORIGIN)
                == "source_unavailable"
            )
            assert await repository.register_provider_source(source) == "registered"
            assert await repository.record_measurement_report(source, report, _ORIGIN) == "recorded"
            first = await repository.load_measurement_report(source.attempt.scope, report.report_id)
            assert first is not None
            assert first.report == report and first.source == source and first.origin == _ORIGIN
            assert first.received_at >= source.run_created_at
            assert first.retain_until == source.run_created_at + timedelta(days=90)
            assert (
                await repository.record_measurement_report(
                    source, report, MeasurementReportOrigin("e" * 64, "f" * 64)
                )
                == "replayed"
            )
            assert (
                await repository.record_measurement_report(
                    source, replace(report, command_exit_code=1), _ORIGIN
                )
                == "report_conflict"
            )
            assert (
                await repository.record_measurement_report(
                    replace(source, source_evidence_digest="f" * 64), report, _ORIGIN
                )
                == "source_unavailable"
            )
            assert (
                await repository.load_measurement_report(source.attempt.scope, report.report_id)
                == first
            )
            for scope in (RepositoryScope(102, 202), RepositoryScope(101, 203)):
                assert await repository.load_measurement_report(scope, report.report_id) is None
            assert await repository.load_measurement_report(source.attempt.scope, "0" * 64) is None
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(reconciliation_subjects)
                    )
                    == 0
                )
                assert (
                    await connection.scalar(select(ci_workflow_attempt_collections.c.status))
                    == "pending"
                )
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_job_measurement_reports)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_last_report_slot_serializes_and_replay_does_not_consume_capacity(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            now = await database_now(engine)
            source = provider_source(303, now)
            repository = store(engine)
            assert await repository.register_provider_source(source) == "registered"
            prototype = measurement_report(attempt=source.attempt)
            reports = tuple(
                replace(prototype, sample_key=f"sample-{index}")
                for index in range(MAX_REPORTS_PER_ATTEMPT + 1)
            )
            async with engine.begin() as connection:
                await connection.execute(
                    insert(ci_job_measurement_reports),
                    [
                        encode_measurement_report(
                            StoredMeasurementReport(
                                report, source, _ORIGIN, now, now + timedelta(days=90)
                            )
                        )
                        for report in reports[: MAX_REPORTS_PER_ATTEMPT - 1]
                    ],
                )
            waiter_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def write_waiter() -> MeasurementReportWriteResult:
                async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                    writer = transaction.ci_measurement_reports
                    pid = await writer._connection.scalar(select(func.pg_backend_pid()))
                    assert type(pid) is int
                    waiter_pid.set_result(pid)
                    result = await writer.record_measurement_report(source, reports[-1], _ORIGIN)
                    if result == "recorded":
                        await transaction.commit()
                    return result

            async with (
                asyncio.timeout(20),
                asyncio.TaskGroup() as group,
                PostgresCiEconomicsUnitOfWork(engine) as first,
            ):
                writer = first.ci_measurement_reports
                assert (
                    await writer.record_measurement_report(source, reports[-2], _ORIGIN)
                    == "recorded"
                )
                holder = await writer._connection.scalar(select(func.pg_backend_pid()))
                assert type(holder) is int
                waiting = group.create_task(write_waiter())
                await wait_for_blocked_operation(
                    admin, holder, waiting, waiter_pid=await waiter_pid
                )
                await first.commit()
            assert waiting.result() == "capacity_reached"
            assert (
                await repository.record_measurement_report(source, reports[0], _ORIGIN)
                == "replayed"
            )
            assert (
                await repository.record_measurement_report(
                    source, replace(reports[0], command_exit_code=1), _ORIGIN
                )
                == "report_conflict"
            )
            assert (
                await repository.load_measurement_report(
                    source.attempt.scope, reports[-1].report_id
                )
                is None
            )
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_job_measurement_reports)
                    )
                    == MAX_REPORTS_PER_ATTEMPT
                )
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_runtime_role_cannot_update_or_prematurely_delete_report(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            source = provider_source(303, await database_now(engine))
            report = measurement_report(attempt=source.attempt)
            repository = store(engine)
            assert await repository.register_provider_source(source) == "registered"
            assert await repository.record_measurement_report(source, report, _ORIGIN) == "recorded"
            first = await repository.load_measurement_report(source.attempt.scope, report.report_id)
            async with engine.begin() as connection:
                assert first is not None
                for command in (
                    update(ci_job_measurement_reports).values(report_digest="e" * 64),
                    delete(ci_job_measurement_reports),
                ):
                    with pytest.raises(DBAPIError):
                        async with connection.begin_nested():
                            await connection.execute(command)
            assert (
                await repository.load_measurement_report(source.attempt.scope, report.report_id)
                == first
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject_id", "e" * 64),
        ("source_kind", "reconciliation"),
        ("report_id", "A" * 64),
        ("report_digest", "e" * 63),
        ("producer_claim_hash", "E" * 64),
        ("provider_binding_digest", "e" * 63),
        ("payload_canonical", b""),
        ("payload_canonical", b"x" * 131_073),
    ],
    ids=(
        "wrong-source",
        "wrong-source-kind",
        "uppercase-report-id",
        "short-report-digest",
        "uppercase-producer-hash",
        "short-provider-digest",
        "empty-payload",
        "oversized-payload",
    ),
)
def test_report_sql_admission_rejects_invalid_row_operands(
    runtime_postgres_database_url: str,
    field: str,
    value: object,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            source = provider_source(303, now)
            assert await store(engine).register_provider_source(source) == "registered"
            report = measurement_report(attempt=source.attempt)
            valid = encode_measurement_report(
                StoredMeasurementReport(report, source, _ORIGIN, now, now + timedelta(days=90))
            )
            async with engine.begin() as connection:
                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(
                            insert(ci_job_measurement_reports).values({**valid, field: value})
                        )
                await connection.execute(insert(ci_job_measurement_reports).values(valid))
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_job_measurement_reports)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
