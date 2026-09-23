import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, archived_statistics
from sqlalchemy import event, func, insert, select, update
from sqlalchemy.engine import RowMapping

from ci_coordinator.ci_economics.archive_detail import (
    ArchivedAttemptDetail,
    ArchivedJobDetail,
    ArchiveStepDetail,
    encode_archive_detail,
)
from ci_coordinator.ci_economics.archive_encoding import (
    MAX_HISTORY_JOB_BYTES,
    MAX_HISTORY_STATISTICS_BYTES,
)
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptHeader
from ci_coordinator.ci_economics.history_gap import HistoryRecheckGap
from ci_coordinator.ci_economics.history_read import (
    HistoryReadCursor,
    HistoryReadPage,
    HistoryReadQuery,
    HistoryReadRejected,
)
from ci_coordinator.ci_economics.history_read_cursor import history_query_digest
from ci_coordinator.persistence import ci_history_read_store
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_attempts as attempts,
)
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_details as details
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_jobs as jobs
from ci_coordinator.persistence.ci_history_gap_store import store_history_gap
from ci_coordinator.persistence.ci_history_read_adapters import TransactionalHistoryReadStore
from ci_coordinator.persistence.ci_history_read_codec import read_history_job
from ci_coordinator.persistence.ci_history_read_store import read_history
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
    lock_history_scope,
    write_history_dataset,
)
from ci_coordinator.persistence.ci_history_statistics_store import store_history_statistics
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresHistoryUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine

from .test_history_detail_cleanup import _seed_details

pytestmark = pytest.mark.persistence


def _query(**changes: object) -> HistoryReadQuery:
    return HistoryReadQuery.model_validate(
        {
            "installationId": 101,
            "repositoryId": 202,
            "generation": 1,
            "kind": "records",
            "limit": 1,
            **changes,
        }
    )


def _cursor(page: HistoryReadPage) -> HistoryReadCursor:
    assert page.next_key is not None
    return HistoryReadCursor(
        queryDigest=history_query_digest(page.query, "operator"),
        configurationRevision=page.configuration_revision,
        dataRevision=page.data_revision,
        observedAt=page.observed_at,
        key=page.next_key,
    )


def test_keysets_share_one_snapshot_without_loading_jobs_or_details(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        statements: list[str] = []

        def observe(_connection: object, _cursor: object, statement: str, *_args: object) -> None:
            statements.append(statement)

        try:
            dataset = await _seed_details(admin, runtime)
            async with runtime.begin() as connection:
                event.listen(runtime.sync_engine, "before_cursor_execute", observe)
                try:
                    first = await read_history(connection, _query(), None)
                finally:
                    event.remove(runtime.sync_engine, "before_cursor_execute", observe)
                assert isinstance(first, HistoryReadPage)
                assert len(statements) == 1 and "LIMIT" in statements[0]
                assert all(
                    token not in statements[0]
                    for token in ("job_canonical", "detail_canonical", "FOR UPDATE")
                )
                assert first.query.scope == dataset.scope
                assert first.records[0].header.attempt.workflow_run_id == 303
                assert first.records[0].detail.state == "expired"
                assert first.records[0].detail.content == "expired"
                assert first.records[0].upstream_availability == "not_checked"
                assert first.records[0].job_count == 1 and not first.jobs
                second = await read_history(connection, _query(), _cursor(first))
                assert isinstance(second, HistoryReadPage)
                assert second.records[0].header.attempt.workflow_run_id == 304
                last = await read_history(connection, _query(), _cursor(second))
                assert isinstance(last, HistoryReadPage)
                assert last.records[0].header.attempt.workflow_run_id == 305
                assert last.next_key is None
                for changes in (
                    {"repositoryId": 203},
                    {"installationId": 202, "repositoryId": 101},
                ):
                    assert await read_history(
                        connection, _query(**changes), None
                    ) == HistoryReadRejected("not_found")
                empty = await read_history(connection, _query(workflowId=405), None)
                assert isinstance(empty, HistoryReadPage) and empty.records == ()
                matching = await read_history(connection, _query(jobName="Lint", limit=50), None)
                assert isinstance(matching, HistoryReadPage) and len(matching.records) == 3
                absent = await read_history(
                    connection, _query(jobName="not-retained", limit=50), None
                )
                assert isinstance(absent, HistoryReadPage) and absent.records == ()
                empty = await read_history(
                    connection, _query(createdFrom="2021-01-01T00:00:00Z"), None
                )
                assert isinstance(empty, HistoryReadPage) and empty.records == ()
                assert len((await connection.execute(select(jobs))).all()) == 3
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_job_name_filter_selects_only_its_parent_without_join_multiplication(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            dataset = await _seed_details(admin, runtime)
            source = archived_statistics()
            matching_jobs = tuple(
                source.jobs[0].model_copy(update={"name": "Ruff", "provider_job_id": identifier})
                for identifier in (701, 702)
            )
            source = source.model_copy(
                update={
                    "attempt": source.attempt.model_copy(update={"workflow_run_id": 306}),
                    "jobs": matching_jobs,
                    "provider_job_total": 2,
                }
            )
            async with admin.begin() as connection:
                await lock_history_scope(connection, dataset.scope)
                now = await history_database_time(connection)
                successor, outcome = await store_history_statistics(
                    connection, dataset, source, now=now
                )
                assert outcome == "recorded"
                assert successor.usage.jobs == dataset.usage.jobs + 2
            async with runtime.begin() as connection:
                for name, run_ids in (("Ruff", [306]), ("Lint", [303, 304, 305])):
                    page = await read_history(connection, _query(jobName=name, limit=50), None)
                    assert isinstance(page, HistoryReadPage)
                    assert [row.header.attempt.workflow_run_id for row in page.records] == run_ids
                    assert page.next_key is None
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["generation", "configuration", "data", "expiry", "future"])
def test_stale_cursor_never_continues_a_different_epoch(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    change: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            original = await _seed_details(admin, runtime)
            async with runtime.begin() as connection:
                first = await read_history(connection, _query(), None)
            assert isinstance(first, HistoryReadPage)
            cursor = _cursor(first)
            if change in {"expiry", "future"}:
                cursor = cursor.model_copy(
                    update={
                        "observed_at": first.observed_at
                        + timedelta(minutes=-21 if change == "expiry" else 1)
                    }
                )
            else:
                async with admin.begin() as connection:
                    successor = (
                        replace(original, generation=2)
                        if change == "generation"
                        else replace(original, configuration_revision=2)
                        if change == "configuration"
                        else replace(original, data_revision=original.data_revision + 1)
                    )
                    await lock_history_scope(connection, original.scope)
                    await write_history_dataset(
                        connection,
                        original,
                        successor,
                    )
            async with runtime.begin() as connection:
                assert await read_history(connection, _query(), cursor) == HistoryReadRejected(
                    "stale_cursor"
                )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("due", [False, True])
def test_job_and_detail_reads_survive_source_absence_without_disclosing_unknown_payload(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    due: bool,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _seed_details(admin, runtime, due=due)
            store = TransactionalHistoryReadStore(lambda: PostgresHistoryUnitOfWork(runtime))
            result = await store.read_history(
                _query(kind="jobs", workflowRunId=303, runAttempt=1), None
            )
            assert isinstance(result, HistoryReadPage)
            assert result.jobs[0].name == "Lint" and result.jobs[0].timing is not None
            assert result.records[0].header.attempt.repository_id == 202
            result = await store.read_history(
                _query(kind="detail", workflowRunId=303, runAttempt=1), None
            )
            assert isinstance(result, HistoryReadPage) and result.detail is not None
            assert result.detail.state == ("expired" if due else "retained")
            assert result.detail.content == ("expired" if due else "unavailable_format")
            assert "fixture" not in result.model_dump_json()
            assert await store.read_history(
                _query(kind="jobs", workflowRunId=999, runAttempt=1), None
            ) == HistoryReadRejected("not_found")
            filtered = await store.read_history(
                _query(kind="jobs", workflowRunId=303, runAttempt=1, jobName="Other"), None
            )
            assert (
                isinstance(filtered, HistoryReadPage)
                and not filtered.jobs
                and len(filtered.records) == 1
            )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("due", [False, True])
def test_detail_read_admits_valid_payload_and_suppresses_expired_payload(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    due: bool,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _seed_details(admin, runtime, due=due)
            async with admin.begin() as connection:
                parent = (
                    (
                        await connection.execute(
                            select(attempts).where(
                                attempts.c.workflow_run_id == 303,
                                attempts.c.run_attempt == 1,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                header = ArchivedAttemptHeader.model_validate_json(parent["header_canonical"])
                job_rows = (
                    (
                        await connection.execute(
                            select(jobs)
                            .where(
                                jobs.c.workflow_run_id == 303,
                                jobs.c.run_attempt == 1,
                            )
                            .order_by(jobs.c.provider_job_id)
                        )
                    )
                    .mappings()
                    .all()
                )
                archived_jobs = tuple(
                    read_history_job({column.name: row[column.name] for column in jobs.c})
                    for row in job_rows
                )
                payload = ArchivedAttemptDetail(
                    schemaVersion="ci-economics-archive-detail/v1",
                    attempt=header.attempt,
                    jobs=tuple(
                        ArchivedJobDetail(
                            providerJobId=job.provider_job_id,
                            steps=(
                                ArchiveStepDetail(
                                    number=1,
                                    status="completed",
                                    conclusion=job.conclusion,
                                    startedAt=job.started_at,
                                    completedAt=job.completed_at,
                                ),
                            ),
                        )
                        for job in archived_jobs
                    ),
                )
                await connection.execute(
                    update(details)
                    .where(
                        details.c.workflow_run_id == 303,
                        details.c.run_attempt == 1,
                    )
                    .values(detail_canonical=encode_archive_detail(payload))
                )
            async with runtime.begin() as connection:
                result = await read_history(
                    connection,
                    _query(kind="detail", workflowRunId=303, runAttempt=1),
                    None,
                )
                assert isinstance(result, HistoryReadPage)
                assert result.detail_payload == (None if due else payload)
                assert result.detail is not None
                assert result.detail.content == ("expired" if due else "unavailable_format")
                if not due:
                    scope = result.query.scope
                    await lock_history_scope(connection, scope)
                    dataset = await load_history_dataset(connection, scope, locked=True)
                    assert dataset is not None
                    statistics = archived_statistics()
                    conflicting = statistics.model_copy(
                        update={
                            "jobs": (
                                statistics.jobs[0].model_copy(update={"conclusion": "failure"}),
                            )
                        }
                    )
                    changed, outcome = await store_history_statistics(
                        connection,
                        dataset,
                        conflicting,
                        now=await history_database_time(connection),
                    )
                    assert outcome == "conflict"
                    assert changed.usage == dataset.usage
                    conflicted = await read_history(connection, result.query, None)
                    assert isinstance(conflicted, HistoryReadPage)
                    assert conflicted.records[0].has_conflict
                    assert conflicted.records[0].header == result.records[0].header
                    assert conflicted.detail == result.detail
                    assert conflicted.detail_payload is None
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_detail_job_byte_overflow_is_filtered_before_driver_arrays(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _seed_details(admin, runtime, due=False)
            statistics = archived_statistics()
            payload = ArchivedAttemptDetail(
                schemaVersion="ci-economics-archive-detail/v1",
                attempt=statistics.attempt,
                jobs=(ArchivedJobDetail(providerJobId=1, steps=()),),
            )
            key = {
                "installation_id": 101,
                "repository_id": 202,
                "generation": 1,
                "workflow_run_id": 303,
                "run_attempt": 1,
            }
            query = _query(kind="detail", workflowRunId=303, runAttempt=1)
            async with admin.begin() as connection:
                await connection.execute(
                    update(details)
                    .where(*(details.c[name] == value for name, value in key.items()))
                    .values(detail_canonical=encode_archive_detail(payload))
                )
            async with runtime.begin() as connection:
                baseline = await read_history(connection, query, None)
                assert isinstance(baseline, HistoryReadPage)
                assert baseline.detail_payload == payload
            extra_jobs = MAX_HISTORY_STATISTICS_BYTES // MAX_HISTORY_JOB_BYTES + 1
            async with admin.begin() as connection:
                await connection.execute(
                    insert(jobs).from_select(
                        [column.name for column in jobs.c],
                        select(
                            *(
                                func.generate_series(2, extra_jobs + 1)
                                if column.name == "provider_job_id"
                                else func.convert_to(
                                    func.repeat("x", MAX_HISTORY_JOB_BYTES), "UTF8"
                                )
                                if column.name == "job_canonical"
                                else column
                                for column in jobs.c
                            )
                        ).where(
                            *(jobs.c[name] == value for name, value in key.items()),
                            jobs.c.provider_job_id == 1,
                        ),
                    )
                )
            async with runtime.begin() as connection:
                count, minimum, maximum, total = (
                    await connection.execute(
                        select(
                            func.count(),
                            func.min(func.octet_length(jobs.c.job_canonical)),
                            func.max(func.octet_length(jobs.c.job_canonical)),
                            func.sum(func.octet_length(jobs.c.job_canonical)),
                        ).where(*(jobs.c[name] == value for name, value in key.items()))
                    )
                ).one()
                assert count == extra_jobs + 1 < 2000
                assert 1 <= minimum <= maximum == MAX_HISTORY_JOB_BYTES
                assert total > MAX_HISTORY_STATISTICS_BYTES
                observed = False
                decode_rows = ci_history_read_store._detail_job_rows

                def inspect_driver_row(row: RowMapping) -> tuple[dict[str, object], ...]:
                    nonlocal observed
                    observed = True
                    assert row["detail_detail_canonical"] == encode_archive_detail(payload)
                    for column in jobs.c:
                        assert row[f"detail_jobs_{column.name}"] is None
                    return decode_rows(row)

                with monkeypatch.context() as patch:
                    patch.setattr(ci_history_read_store, "_detail_job_rows", inspect_driver_row)
                    result = await read_history(connection, query, None)
                assert observed
                assert isinstance(result, HistoryReadPage)
                assert result.detail_payload is None
                assert result.detail is not None and result.detail.state == "retained"
                assert result.records[0] == baseline.records[0]
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_gap_pages_preserve_observation_without_claiming_current_deletion(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            original = await _seed_details(admin, runtime)
            async with runtime.begin() as connection:
                await lock_history_scope(connection, original.scope)
                dataset = await load_history_dataset(connection, original.scope, locked=True)
                assert dataset is not None
                now = await history_database_time(connection)
                for run in (303, 304):
                    gap = HistoryRecheckGap(
                        schemaVersion="ci-economics-history-recheck-gap/v1",
                        installationId=101,
                        repositoryId=202,
                        generation=1,
                        configurationRevision=1,
                        workflowRunId=run,
                        workflowId=404,
                        runAttempt=1,
                        runCreatedAt=ARCHIVE_TIME,
                        reason="provider_not_found",
                    )
                    dataset, outcome = await store_history_gap(connection, dataset, gap, now=now)
                    assert outcome == "recorded"
                first = await read_history(connection, _query(kind="gaps"), None)
                assert isinstance(first, HistoryReadPage) and len(first.gaps) == 1
                second = await read_history(connection, _query(kind="gaps"), _cursor(first))
                assert isinstance(second, HistoryReadPage) and len(second.gaps) == 1
                assert second.next_key is None and second.gaps[0].gap_id != first.gaps[0].gap_id
                assert {first.gaps[0].workflow_run_id, second.gaps[0].workflow_run_id} == {303, 304}
                assert first.gaps[0].reason == "provider_not_found"
                for view in (first.gaps[0], second.gaps[0]):
                    assert view.retry_supported
                    assert view.retained is not None
                    assert view.retained.header.attempt.workflow_run_id == view.workflow_run_id
                    assert view.resolution == "retained_complete"
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())
