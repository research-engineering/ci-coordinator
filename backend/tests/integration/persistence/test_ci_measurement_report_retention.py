from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

import pytest
from ci_economics.report_factories import measurement_report
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration.persistence._ci_economics_support import database_now, provider_source, store
from tests.integration.persistence._temporal_lock_support import (
    wait_for_blocked_operation,
    wait_for_database_deadline,
)

from ci_coordinator.ci_economics import CollectionState
from ci_coordinator.ci_economics.ports import MeasurementReportWriteResult
from ci_coordinator.ci_economics.reports import MeasurementReportOrigin
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_economics_collection_codec import (
    decode_collection_source,
    decode_collection_state,
)
from ci_coordinator.persistence.ci_measurement_report_codec import decode_measurement_report
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.schema import (
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
)

pytestmark = pytest.mark.persistence
_ORIGIN = MeasurementReportOrigin("c" * 64, "d" * 64)


def test_reports_expire_atomically_with_source_and_cannot_resurrect_after_purge(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            source = provider_source(303, await database_now(engine))
            report = measurement_report(attempt=source.attempt)
            repository = store(engine)
            assert await repository.register_provider_source(source) == "registered"
            assert await repository.record_measurement_report(source, report, _ORIGIN) == "recorded"
            catalog = await repository.list_measurement_reports(
                source.attempt.scope, source.source_id, after_cursor=None, limit=20
            )
            assert catalog is not None and len(catalog.items) == 1
            assert catalog.items[0].report_id == report.report_id
            assert (
                await repository.load_measurement_report(source.attempt.scope, report.report_id)
                is not None
            )
            claim = await repository.claim_next(worker_id="1" * 64)
            assert claim is not None and claim.source == source
            assert await repository.reject_claim(claim) == "applied"
            await _project_report_source_epoch(admin, source.source_id)
            old_source = replace(source, run_created_at=source.run_created_at - timedelta(days=92))
            async with admin.connect() as connection:
                row = dict(
                    (await connection.execute(select(ci_workflow_attempt_collections)))
                    .mappings()
                    .one()
                )
                state = decode_collection_state(row)
                decoded_source = decode_collection_source(row)
                raw_report = dict(
                    (await connection.execute(select(ci_job_measurement_reports))).mappings().one()
                )
                retained = decode_measurement_report(raw_report, old_source)
                assert decoded_source == old_source and state.status == "terminal_unavailable"
                assert retained.retain_until == state.evidence_retain_until
                assert retained.received_at >= old_source.run_created_at
                assert state.tombstone_retain_until < await database_now(engine)
                assert await connection.scalar(text("SHOW session_replication_role")) == "origin"
            assert (
                await repository.load_measurement_report(source.attempt.scope, report.report_id)
                is None
            )
            assert (
                await repository.record_measurement_report(old_source, report, _ORIGIN)
                == "outside_retention"
            )
            assert (
                await repository.list_measurement_reports(
                    source.attempt.scope, source.source_id, after_cursor=None, limit=20
                )
                is None
            )
            assert (
                await repository.list_provider_sources(
                    source.attempt.scope, after_cursor=None, limit=20
                )
            ).items == ()
            assert await repository.purge_tombstones(limit=10) == 0
            with pytest.raises(
                PersistenceInvariantViolation, match="CI economics evidence expiry failed"
            ) as rejected:
                async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                    expiry = transaction.ci_economics_collection

                    async def reject_transition(
                        previous: CollectionState, successor: CollectionState
                    ) -> bool:
                        assert (
                            previous.status == "terminal_unavailable"
                            and successor.status == "expired"
                        )
                        assert (
                            await expiry._connection.scalar(
                                select(func.count()).select_from(ci_job_measurement_reports)
                            )
                            == 0
                        )
                        return False

                    monkeypatch.setattr(expiry, "_write_expiry_transition", reject_transition)
                    await expiry.expire_evidence(limit=10)
            assert isinstance(rejected.value.__cause__, PersistenceInvariantViolation)
            assert str(rejected.value.__cause__) == "collection expiry CAS failed under row lock"
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_job_measurement_reports)
                    )
                    == 1
                )
                assert (
                    await connection.scalar(select(ci_workflow_attempt_collections.c.status))
                    == "terminal_unavailable"
                )
            assert await repository.expire_evidence(limit=10) == 1
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_job_measurement_reports)
                    )
                    == 0
                )
                assert (
                    await connection.scalar(select(ci_workflow_attempt_collections.c.status))
                    == "expired"
                )
            assert (
                await repository.record_measurement_report(old_source, report, _ORIGIN)
                == "outside_retention"
            )
            assert await repository.purge_tombstones(limit=10) == 1
            assert (
                await repository.record_measurement_report(old_source, report, _ORIGIN)
                == "source_unavailable"
            )
            assert await repository.register_provider_source(old_source) == "outside_source_window"
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_attempt_collections)
                    )
                    == 0
                )
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("order", ["before_expiry", "replay_first", "cleanup_first"])
def test_replay_uses_post_lock_time_and_serializes_with_retention_cleanup(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    order: Literal["before_expiry", "replay_first", "cleanup_first"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            source = provider_source(303, await database_now(engine))
            report = measurement_report(attempt=source.attempt)
            repository = store(engine)
            assert await repository.register_provider_source(source) == "registered"
            assert await repository.record_measurement_report(source, report, _ORIGIN) == "recorded"
            claim = await repository.claim_next(worker_id="1" * 64)
            assert claim is not None and claim.source == source
            assert await repository.reject_claim(claim) == "applied"
            expiry = await database_now(admin) + timedelta(seconds=4)
            age = source.run_created_at + timedelta(days=90) - expiry
            await _project_report_source_epoch(admin, source.source_id, age=age)
            source = replace(source, run_created_at=source.run_created_at - age)
            async with admin.connect() as connection:
                before = dict(
                    (await connection.execute(select(ci_job_measurement_reports))).mappings().one()
                )
            assert before["retain_until"] == expiry
            waiter: asyncio.Future[tuple[int, datetime]] = (
                asyncio.get_running_loop().create_future()
            )

            async def replay() -> MeasurementReportWriteResult:
                async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                    writer = transaction.ci_measurement_reports
                    pid, began_at = (
                        await writer._connection.execute(
                            select(func.pg_backend_pid(), func.statement_timestamp())
                        )
                    ).one()
                    assert type(pid) is int and type(began_at) is datetime
                    waiter.set_result((pid, began_at))
                    return await writer.record_measurement_report(source, report, _ORIGIN)

            async with (
                asyncio.timeout(20),
                asyncio.TaskGroup() as group,
                PostgresCiEconomicsUnitOfWork(engine) as first,
            ):
                connection = first.ci_measurement_reports._connection
                row = (
                    (
                        await connection.execute(
                            select(ci_workflow_attempt_collections).with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                assert row["subject_id"] == source.source_id
                holder = await connection.scalar(select(func.pg_backend_pid()))
                assert type(holder) is int
                waiting = group.create_task(replay())
                pid, began_at = await waiter
                await wait_for_blocked_operation(admin, holder, waiting, waiter_pid=pid)
                assert began_at < await database_now(admin) < expiry
                if order != "before_expiry":
                    await wait_for_database_deadline(admin, expiry)
                    assert await repository.expire_evidence(limit=1) == 0
                    if order == "cleanup_first":
                        assert await first.ci_economics_collection.expire_evidence(limit=1) == 1
                await first.commit()
            assert waiting.result() == (
                "replayed" if order == "before_expiry" else "outside_retention"
            )
            async with admin.connect() as connection:
                rows = (await connection.execute(select(ci_job_measurement_reports))).mappings()
                assert [dict(row) for row in rows] == ([] if order == "cleanup_first" else [before])
            if order == "replay_first":
                assert await repository.expire_evidence(limit=1) == 1
            if order != "before_expiry":
                async with admin.connect() as connection:
                    assert (
                        await connection.scalar(
                            select(func.count()).select_from(ci_job_measurement_reports)
                        )
                        == 0
                    )
                    assert (
                        await connection.scalar(select(ci_workflow_attempt_collections.c.status))
                        == "expired"
                    )
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


async def _project_report_source_epoch(
    admin: AsyncEngine, source_id: str, *, age: timedelta = timedelta(days=92)
) -> None:
    collection_clocks = (
        "source_created_at",
        "deadline_at",
        "evidence_retain_until",
        "tombstone_retain_until",
        "next_attempt_at",
        "lease_acquired_at",
        "lease_expires_at",
        "completed_at",
        "expired_at",
        "created_at",
        "updated_at",
    )
    async with admin.begin() as connection:
        await connection.execute(text("SET LOCAL session_replication_role = replica"))
        changed_source = await connection.execute(
            update(ci_workflow_attempt_collections)
            .where(ci_workflow_attempt_collections.c.subject_id == source_id)
            .values(
                {name: ci_workflow_attempt_collections.c[name] - age for name in collection_clocks}
            )
        )
        changed_reports = await connection.execute(
            update(ci_job_measurement_reports)
            .where(ci_job_measurement_reports.c.subject_id == source_id)
            .values(
                received_at=ci_job_measurement_reports.c.received_at - age,
                retain_until=ci_job_measurement_reports.c.retain_until - age,
            )
        )
        assert changed_source.rowcount == changed_reports.rowcount == 1
