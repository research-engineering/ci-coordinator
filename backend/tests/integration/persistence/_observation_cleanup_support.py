import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from time import perf_counter_ns

import psycopg
from psycopg import sql
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.ci_economics import ProviderAttemptSnapshot
from ci_coordinator.ci_economics.reports import JobMeasurementReport, MeasurementReportOrigin
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_economics_repository import _snapshot_job_row
from ci_coordinator.persistence.schema import (
    ci_workflow_attempt_snapshot_jobs as jobs,
)
from ci_coordinator.persistence.schema import (
    ci_workflow_attempt_snapshots as snapshots,
)


async def seed_cleanup_attempt(
    runtime: AsyncEngine,
    source: ProviderRunCollectionSource,
    evidence: ProviderAttemptSnapshot,
    report: JobMeasurementReport,
) -> None:
    async with PostgresCiEconomicsUnitOfWork(runtime) as transaction:
        collection = transaction.ci_economics_collection
        assert await collection.register_provider_source(source) == "registered"
        claim = await collection.claim_next(worker_id="1" * 64)
        assert claim is not None and claim.source == source
        assert await collection.record_snapshot(claim, evidence) == "captured"
        assert (
            await transaction.ci_measurement_reports.record_measurement_report(
                source, report, MeasurementReportOrigin("c" * 64, "d" * 64)
            )
            == "recorded"
        )
        await transaction.commit()


def _ascii_fixture_json(value: object) -> bytes:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    assert encoded.isascii()
    return encoded


async def expand_cleanup_snapshot(
    admin: AsyncEngine,
    prototype: ProviderAttemptSnapshot,
    count: int,
    *,
    after_copy_row: Callable[[], None] | None = None,
) -> tuple[str, int, int]:
    construction_started = perf_counter_ns()
    assert len(prototype.jobs) == 1 and 1 < count <= 2000
    job = prototype.jobs[0]
    assert 1 <= job.provider_job_id <= count
    if after_copy_row is not None and count - 1 < 128:
        raise ValueError("fault injection requires a substantial COPY population")
    base = _snapshot_job_row(prototype.subject_id, job)
    mapping = job.canonical_mapping()
    assert _ascii_fixture_json(mapping) == base["job_canonical_json"]
    rows: list[dict[str, object]] = []
    mappings: list[dict[str, object]] = []
    for identifier in range(1, count + 1):
        facts = {**mapping, "providerJobId": identifier}
        body = _ascii_fixture_json(facts)
        digest = hashlib.sha256(body).hexdigest()
        row = {
            **base,
            "provider_job_id": identifier,
            "semantic_hash": digest,
            "job_canonical_json": body,
        }
        if identifier in {1, count // 2, count}:
            admitted = replace(job, provider_job_id=identifier, semantic_hash=digest)
            assert row == _snapshot_job_row(prototype.subject_id, admitted)
        mappings.append(facts)
        if identifier != job.provider_job_id:
            rows.append(row)
    snapshot_digest = hashlib.sha256(
        _ascii_fixture_json({"attempt": prototype.attempt.canonical_mapping(), "jobs": mappings})
    ).hexdigest()
    construction_elapsed = perf_counter_ns() - construction_started
    transaction_started = perf_counter_ns()
    async with admin.begin() as connection:
        await connection.execute(text("SET LOCAL session_replication_role = replica"))
        changed = await connection.scalar(
            update(snapshots)
            .where(
                snapshots.c.subject_id == prototype.subject_id,
                snapshots.c.job_count == 1,
                snapshots.c.snapshot_digest == prototype.snapshot_digest,
            )
            .values(job_count=count, snapshot_digest=snapshot_digest)
            .returning(snapshots.c.subject_id)
        )
        assert changed == prototype.subject_id
        await connection.execute(text("SET LOCAL session_replication_role = origin"))
        assert await connection.scalar(text("SHOW session_replication_role")) == "origin"
        raw = await connection.get_raw_connection()
        driver = raw.driver_connection
        assert isinstance(driver, psycopg.AsyncConnection)
        columns = tuple(base)
        table = sql.Identifier(jobs.schema, jobs.name) if jobs.schema else sql.Identifier(jobs.name)
        statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
            table, sql.SQL(", ").join(sql.Identifier(name) for name in columns)
        )
        async with driver.cursor() as cursor, cursor.copy(statement) as copy:
            for row in rows:
                await copy.write_row(tuple(row[name] for name in columns))
                if after_copy_row is not None:
                    after_copy_row()
    return snapshot_digest, construction_elapsed, perf_counter_ns() - transaction_started
