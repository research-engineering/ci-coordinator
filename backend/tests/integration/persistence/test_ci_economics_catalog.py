from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from ci_economics.report_factories import measurement_report
from sqlalchemy import Select, func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BinaryExpression, ClauseElement, ColumnElement
from sqlalchemy.sql.functions import Function
from tests.integration.persistence._ci_economics_support import (
    database_now,
    provider_source,
    store,
    subject,
    terminalize_reconciliation,
)

from ci_coordinator.ci_economics import initial_collection_state, load_bundled_ci_economics_profile
from ci_coordinator.ci_economics.reports import MeasurementReportOrigin
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_economics_collection_codec import encode_collection_record
from ci_coordinator.persistence.ci_economics_repository import _PostgresCiEconomicsRepository
from ci_coordinator.persistence.ci_measurement_report_repository import (
    _PostgresCiMeasurementReportRepository,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
)

pytestmark = pytest.mark.persistence


def test_catalog_retention_predicates_exclude_exact_database_expiry(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            source = provider_source(304, await database_now(engine))
            capture = AsyncMock(spec=AsyncConnection)
            result = Mock()
            result.mappings.return_value.all.return_value = []
            capture.execute.return_value = result
            sources = _PostgresCiEconomicsRepository(capture, Mock(), Mock())
            reports = _PostgresCiMeasurementReportRepository(
                capture, Mock(), Mock(), budget_policies=Mock(), budget_signals=Mock()
            )
            await sources.list_provider_sources(source.attempt.scope, after_cursor=None, limit=2)
            await reports.list_measurement_reports(
                source.attempt.scope, source.source_id, after_cursor=None, limit=2
            )
            calls = capture.execute.await_args_list
            assert len(calls) == 2
            source_expiry = ci_workflow_attempt_collections.c.evidence_retain_until
            report_expiry = ci_job_measurement_reports.c.retain_until
            async with engine.connect() as connection:
                for call, columns in zip(
                    calls, ((source_expiry,), (source_expiry, report_expiry)), strict=True
                ):
                    statement = call.args[0]
                    assert isinstance(statement, Select)
                    for column in columns:
                        predicates = [
                            node
                            for node in visitors.iterate(statement)
                            if isinstance(node, BinaryExpression)
                            and node.left.compare(column)
                            and isinstance(node.right, Function)
                            and node.right.name == "statement_timestamp"
                        ]
                        assert len(predicates) == 1
                        for offset, expected in ((-1, False), (0, False), (1, True)):

                            def substitute(
                                element: ClauseElement,
                                column: ClauseElement = column,
                                offset: int = offset,
                                **_kw: object,
                            ) -> ClauseElement | None:
                                if element.compare(column):
                                    return func.statement_timestamp() + timedelta(
                                        microseconds=offset
                                    )
                                return None

                            predicate = cast(
                                ColumnElement[bool],
                                visitors.replacement_traverse(predicates[0], {}, substitute),
                            )
                            assert await connection.scalar(select(predicate)) is expected
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_source_catalog_has_scoped_bounded_current_keyset_pages(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        repository = store(engine)
        try:
            now = await database_now(engine)
            source = provider_source(300, now)
            scope = source.attempt.scope
            assert (
                await repository.list_provider_sources(scope, after_cursor=None, limit=2)
            ).items == ()
            older = provider_source(299, now)
            rerun = replace(source, attempt=replace(source.attempt, run_attempt=2))
            for candidate in (source, older, rerun):
                assert await repository.register_provider_source(candidate) == "registered"
            for other_scope in (RepositoryScope(102, 202), RepositoryScope(101, 203)):
                foreign = replace(source, attempt=replace(source.attempt, scope=other_scope))
                assert await repository.register_provider_source(foreign) == "registered"
            await terminalize_reconciliation(engine, subject(500), now)
            assert await repository.register_eligible(limit=10) == 1
            historical = provider_source(1001, now - timedelta(days=91))
            policy = load_bundled_ci_economics_profile().collection_policy
            state = initial_collection_state(
                historical.source_id, historical.run_created_at, historical.run_created_at, policy
            )
            async with engine.begin() as connection:
                await connection.execute(
                    insert(ci_workflow_attempt_collections).values(
                        encode_collection_record(state, historical)
                    )
                )
            first = await repository.list_provider_sources(scope, after_cursor=None, limit=2)
            assert tuple(row.source for row in first.items) == (rerun, source)
            assert first.next_cursor == "300.1"
            assert all(row.state.status == "pending" for row in first.items)
            newest = provider_source(301, now)
            assert await repository.register_provider_source(newest) == "registered"
            second = await repository.list_provider_sources(
                scope, after_cursor=first.next_cursor, limit=2
            )
            assert tuple(row.source for row in second.items) == (older,)
            assert second.next_cursor is None
            exhausted = await repository.list_provider_sources(scope, after_cursor="299.1", limit=2)
            assert exhausted.items == () and exhausted.next_cursor is None
            refreshed = await repository.list_provider_sources(scope, after_cursor=None, limit=100)
            assert tuple(row.source for row in refreshed.items) == (newest, rerun, source, older)
            assert refreshed.next_cursor is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_report_catalog_distinguishes_empty_missing_and_scoped_paged_metadata(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        repository = store(engine)
        try:
            source = provider_source(303, await database_now(engine))
            scope = source.attempt.scope
            assert (
                await repository.list_measurement_reports(
                    scope, source.source_id, after_cursor=None, limit=2
                )
                is None
            )
            assert await repository.register_provider_source(source) == "registered"
            empty = await repository.list_measurement_reports(
                scope, source.source_id, after_cursor=None, limit=2
            )
            assert empty is not None and empty.source == source
            assert empty.items == () and empty.next_cursor is None
            prototype = measurement_report(attempt=source.attempt)
            reports = tuple(
                sorted(
                    (replace(prototype, sample_key=f"sample-{index}") for index in range(3)),
                    key=lambda report: report.report_id,
                )
            )
            origin = MeasurementReportOrigin("c" * 64, "d" * 64)
            for report in reversed(reports):
                assert (
                    await repository.record_measurement_report(source, report, origin) == "recorded"
                )
            first = await repository.list_measurement_reports(
                scope, source.source_id, after_cursor=None, limit=2
            )
            assert first is not None and first.source == source
            assert tuple(row.report_id for row in first.items) == tuple(
                report.report_id for report in reports[:2]
            )
            assert tuple(row.report_digest for row in first.items) == tuple(
                report.report_digest for report in reports[:2]
            )
            assert first.next_cursor == reports[1].report_id
            for pointer in first.items:
                detail = await repository.load_measurement_report(scope, pointer.report_id)
                assert detail is not None
                assert pointer.received_at == detail.received_at
                assert pointer.retain_until == detail.retain_until
            second = await repository.list_measurement_reports(
                scope, source.source_id, after_cursor=first.next_cursor, limit=2
            )
            assert second is not None and second.source == source
            assert tuple(row.report_id for row in second.items) == (reports[2].report_id,)
            assert second.next_cursor is None
            exhausted = await repository.list_measurement_reports(
                scope, source.source_id, after_cursor=reports[2].report_id, limit=2
            )
            assert exhausted is not None and exhausted.source == source
            assert exhausted.items == () and exhausted.next_cursor is None
            for other_scope in (RepositoryScope(102, 202), RepositoryScope(101, 203)):
                assert (
                    await repository.list_measurement_reports(
                        other_scope, source.source_id, after_cursor=None, limit=2
                    )
                    is None
                )
            assert (
                await repository.list_measurement_reports(
                    scope, "0" * 64, after_cursor=None, limit=2
                )
                is None
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
