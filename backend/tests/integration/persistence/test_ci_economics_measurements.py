from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal

import pytest
from sqlalchemy import delete, text, update
from tests.integration.persistence._ci_economics_support import (
    database_now,
    snapshot,
    store,
    subject,
)

from ci_coordinator.ci_economics import CiEconomicsStoreUnavailable
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshot_jobs,
    ci_workflow_attempt_snapshots,
)

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize(
    "mutation", ["digest", "job_count", "missing_jobs", "missing_source", "source_head"]
)
def test_retained_read_rejects_inconsistent_database_evidence(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    mutation: Literal["digest", "job_count", "missing_jobs", "missing_source", "source_head"],
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            now = await database_now(runtime)
            evidence = snapshot(subject(410), now)
            source = ProviderRunCollectionSource(evidence.attempt, now, "2026-03-10", "a" * 64)
            evidence = replace(evidence, subject_id=source.source_id)
            repository = store(runtime)
            assert await repository.register_provider_source(source) == "registered"
            claim = await repository.claim_next(worker_id="1" * 64)
            assert claim is not None
            assert await repository.record_snapshot(claim, evidence) == "captured"
            assert await repository.load_measurements(evidence.attempt) is not None
            async with admin.begin() as connection:
                await connection.execute(text("SET LOCAL session_replication_role = replica"))
                if mutation in {"digest", "job_count"}:
                    values: dict[str, object] = (
                        {"snapshot_digest": "b" * 64} if mutation == "digest" else {"job_count": 2}
                    )
                    statement = (
                        update(ci_workflow_attempt_snapshots)
                        .where(ci_workflow_attempt_snapshots.c.subject_id == source.source_id)
                        .values(values)
                    )
                    await connection.execute(statement)
                elif mutation == "missing_jobs":
                    await connection.execute(
                        delete(ci_workflow_attempt_snapshot_jobs).where(
                            ci_workflow_attempt_snapshot_jobs.c.subject_id == source.source_id
                        )
                    )
                elif mutation == "missing_source":
                    await connection.execute(
                        delete(ci_workflow_attempt_collections).where(
                            ci_workflow_attempt_collections.c.subject_id == source.source_id
                        )
                    )
                else:
                    assert mutation == "source_head"
                    await connection.execute(
                        update(ci_workflow_attempt_collections)
                        .where(ci_workflow_attempt_collections.c.subject_id == source.source_id)
                        .values(head_sha="b" * 40)
                    )
            with pytest.raises(CiEconomicsStoreUnavailable):
                await repository.load_measurements(evidence.attempt)
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())
