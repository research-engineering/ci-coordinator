import asyncio

import pytest
from ci_economics.archive_factories import archived_job, archived_statistics
from sqlalchemy import func, select

from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_attempts,
    ci_history_jobs,
)
from ci_coordinator.persistence._schema_ci_history_control import ci_history_gaps
from ci_coordinator.persistence.ci_history_state_store import (
    load_history_dataset,
    load_history_scan,
)
from ci_coordinator.persistence.connection import create_postgres_engine

from ._history_support import history_command, history_page, history_store

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("initial_job_count", [0, 1])
def test_partial_import_refines_without_resetting_clocks_or_double_charging(
    runtime_postgres_database_url: str, initial_job_count: int
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = history_store(engine)
        command = history_command()
        complete = archived_statistics(jobs=(archived_job(1), archived_job(2)), provider_total=2)
        partial = archived_statistics(
            jobs=complete.jobs[:initial_job_count], population="partial", provider_total=2
        )
        try:
            assert isinstance(await adapter.configure_history(command), HistoryConfigured)
            first_import = None
            for cycle, statistics in enumerate((partial, complete)):
                page_claim = await adapter.claim_history(worker_id="a" * 64)
                assert page_claim is not None
                assert (
                    await adapter.record_history_page(page_claim[1], history_page(page_claim[1]))
                    == "applied"
                )
                attempt_claim = await adapter.claim_history(worker_id="a" * 64)
                assert attempt_claim is not None
                assert (
                    await adapter.record_history_statistics(attempt_claim[1], statistics)
                    == "applied"
                )
                async with engine.connect() as connection:
                    dataset = await load_history_dataset(connection, command.scope)
                    scan = await load_history_scan(connection, command.scope)
                    assert dataset is not None and scan is not None
                    row = (await connection.execute(select(ci_history_attempts))).mappings().one()
                    job_ids = tuple(
                        (
                            await connection.scalars(
                                select(ci_history_jobs.c.provider_job_id).order_by(
                                    ci_history_jobs.c.provider_job_id
                                )
                            )
                        ).all()
                    )
                    assert job_ids == tuple(job.provider_job_id for job in statistics.jobs)
                    assert dataset.usage.attempts == 1
                    assert dataset.usage.jobs == len(job_ids)
                    assert dataset.usage.gaps == 1
                    gap_bytes = await connection.scalar(
                        select(func.sum(func.octet_length(ci_history_gaps.c.gap_canonical)))
                    )
                    assert dataset.usage.canonical_bytes == row["statistics_bytes"] + gap_bytes
                    assert row["statistics_digest"] == statistics.statistics_digest
                    assert row["detail_first_imported_at"] is None
                    if cycle == 0:
                        first_import = row["first_imported_at"]
                        assert scan.last_outcome == "attempt_recorded"
                    else:
                        assert row["first_imported_at"] == first_import
                        assert scan.last_outcome == "refined"
                if cycle == 0:
                    rescan = ConfigureHistory.model_validate(
                        {
                            **command.model_dump(),
                            "expectedRevision": 1,
                            "initialCreatedFrom": None,
                            "rescan": True,
                            "operationId": "refine-partial-history",
                        }
                    )
                    assert isinstance(await adapter.configure_history(rescan), HistoryConfigured)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("inconsistent_timing", [False, True])
def test_historical_import_and_rescan_preserve_one_contribution_and_its_first_import(
    runtime_postgres_database_url: str,
    inconsistent_timing: bool,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = history_store(engine)
        command = history_command()
        statistics = archived_statistics()
        if inconsistent_timing:
            original_job = statistics.jobs[0]
            job = original_job.model_copy(update={"created_at": original_job.completed_at})
            statistics = type(statistics).model_validate(
                statistics.model_copy(update={"jobs": (job,)})
            )
            assert statistics.jobs[0].timing_quality == "inconsistent"
        try:
            assert isinstance(await adapter.configure_history(command), HistoryConfigured)
            first_import = None
            first_usage = None
            for cycle in range(2):
                page_claim = await adapter.claim_history(worker_id="a" * 64)
                assert page_claim is not None and page_claim[1].state.checkpoint.pending is None
                assert (
                    await adapter.record_history_page(page_claim[1], history_page(page_claim[1]))
                    == "applied"
                )
                attempt_claim = await adapter.claim_history(worker_id="a" * 64)
                assert attempt_claim is not None
                assert (
                    await adapter.record_history_statistics(attempt_claim[1], statistics)
                    == "applied"
                )
                assert (
                    await adapter.record_history_statistics(attempt_claim[1], statistics)
                    == "claim_lost"
                )
                async with engine.connect() as connection:
                    dataset = await load_history_dataset(connection, command.scope)
                    scan = await load_history_scan(connection, command.scope)
                    assert dataset is not None and scan is not None
                    row = (await connection.execute(select(ci_history_attempts))).mappings().one()
                    assert (
                        row["detail_state"] == "not_imported"
                        and row["detail_first_imported_at"] is None
                    )
                    assert dataset.usage.attempts == dataset.usage.jobs == 1
                    assert (
                        await connection.scalar(select(func.count()).select_from(ci_history_jobs))
                        == 1
                    )
                    assert scan.last_outcome == ("attempt_recorded" if cycle == 0 else "replayed")
                    stored_job = (
                        (await connection.execute(select(ci_history_jobs))).mappings().one()
                    )
                    assert stored_job["created_at"] == statistics.jobs[0].created_at
                    assert stored_job["started_at"] == statistics.jobs[0].started_at
                    if cycle == 0:
                        first_import, first_usage = row["first_imported_at"], dataset.usage
                    else:
                        assert (
                            row["first_imported_at"] == first_import
                            and dataset.usage == first_usage
                        )
                if cycle == 0:
                    rescan = ConfigureHistory.model_validate(
                        {
                            **command.model_dump(),
                            "expectedRevision": 1,
                            "initialCreatedFrom": None,
                            "rescan": True,
                            "operationId": "rescan-history",
                        }
                    )
                    assert isinstance(await adapter.configure_history(rescan), HistoryConfigured)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_byte_capacity_blocks_atomic_contribution_until_a_quota_change_resumes_the_checkpoint(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = history_store(engine)
        command = history_command()
        low_quota = ConfigureHistory.model_validate(
            {
                **command.model_dump(),
                "configuration": {
                    **command.configuration.model_dump(),
                    "quota": {"attempts": 1, "jobs": 1, "gaps": 1, "canonicalBytes": 1},
                },
            }
        )
        try:
            assert isinstance(await adapter.configure_history(low_quota), HistoryConfigured)
            page_claim = await adapter.claim_history(worker_id="a" * 64)
            assert page_claim is not None
            assert (
                await adapter.record_history_page(page_claim[1], history_page(page_claim[1]))
                == "applied"
            )
            attempt_claim = await adapter.claim_history(worker_id="a" * 64)
            assert attempt_claim is not None
            assert (
                await adapter.record_history_statistics(attempt_claim[1], archived_statistics())
                == "capacity_reached"
            )
            async with engine.connect() as connection:
                dataset = await load_history_dataset(connection, command.scope)
                scan = await load_history_scan(connection, command.scope)
                assert dataset is not None and scan is not None
                assert (
                    dataset.usage.attempts
                    == dataset.usage.jobs
                    == dataset.usage.canonical_bytes
                    == 0
                )
                assert scan.checkpoint == attempt_claim[1].state.checkpoint and scan.lease is None
                for table in (ci_history_attempts, ci_history_jobs):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
            raised = ConfigureHistory.model_validate(
                {
                    **command.model_dump(),
                    "expectedRevision": 1,
                    "initialCreatedFrom": None,
                    "operationId": "raise-quota",
                }
            )
            assert isinstance(await adapter.configure_history(raised), HistoryConfigured)
            resumed = await adapter.claim_history(worker_id="a" * 64)
            assert (
                resumed is not None
                and resumed[1].state.checkpoint == attempt_claim[1].state.checkpoint
            )
            assert (
                await adapter.record_history_statistics(resumed[1], archived_statistics())
                == "applied"
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
