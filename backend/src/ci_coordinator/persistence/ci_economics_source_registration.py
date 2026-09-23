from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.collection import CollectionPolicy, initial_collection_state
from ci_coordinator.ci_economics.sources import (
    MAX_PROVIDER_SOURCE_BATCH_SIZE,
    MAX_PROVIDER_SOURCES_PER_REPOSITORY,
    ProviderRunCollectionSource,
    ProviderSourceRegistrationResult,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_economics_collection_codec import (
    decode_collection_source,
    encode_collection_record,
)
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.schema import ci_workflow_attempt_collections as collections


async def register_provider_sources(
    connection: AsyncConnection,
    scope: RepositoryScope,
    sources: tuple[ProviderRunCollectionSource, ...],
    policy: CollectionPolicy,
) -> tuple[ProviderSourceRegistrationResult, ...]:
    if type(scope) is not RepositoryScope or type(sources) is not tuple:
        raise TypeError("source batch requires exact scope and tuple")
    if len(sources) > MAX_PROVIDER_SOURCE_BATCH_SIZE:
        raise ValueError("provider source batch exceeds its bound")
    if any(type(source) is not ProviderRunCollectionSource for source in sources):
        raise TypeError("source batch requires exact provider sources")
    if any(source.attempt.scope != scope for source in sources):
        raise ValueError("source batch crosses repository scope")
    if not sources:
        return ()

    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:domain, 0))"),
        {"domain": f"ci-economics-source-quota/v1:{scope.installation_id}:{scope.repository_id}"},
    )
    now = await connection.scalar(select(func.statement_timestamp()))
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise PersistenceInvariantViolation("provider source database time is unavailable")
    window = timedelta(seconds=policy.collection_window_seconds)
    eligible = tuple(
        index
        for index, source in enumerate(sources)
        if source.run_created_at <= now < source.run_created_at + window
    )
    outcomes: list[ProviderSourceRegistrationResult] = ["outside_source_window"] * len(sources)
    if not eligible:
        return tuple(outcomes)

    selected = tuple(sources[index] for index in eligible)
    existing, attempts = await _existing_sources(connection, scope, selected)
    remaining: int | None = None
    records: list[dict[str, object]] = []
    for index in sorted(eligible, key=lambda index: _source_order(sources[index])):
        source = sources[index]
        key = (source.attempt.workflow_run_id, source.attempt.run_attempt)
        matches = {value for value in (source.source_id, attempts.get(key)) if value in existing}
        if matches:
            outcomes[index] = (
                "replayed"
                if len(matches) == 1 and existing[next(iter(matches))] == source
                else "source_conflict"
            )
            continue
        if remaining is None:
            remaining = await _remaining_slots(connection, scope)
        if remaining == 0:
            outcomes[index] = "capacity_reached"
            continue
        state = initial_collection_state(source.source_id, source.run_created_at, now, policy)
        records.append(encode_collection_record(state, source))
        existing[source.source_id] = source
        attempts[key] = source.source_id
        remaining -= 1
        outcomes[index] = "registered"

    if records:
        await connection.execute(postgres_insert(collections), records)
    return tuple(outcomes)


async def _existing_sources(
    connection: AsyncConnection,
    scope: RepositoryScope,
    sources: tuple[ProviderRunCollectionSource, ...],
) -> tuple[dict[str, ProviderRunCollectionSource | None], dict[tuple[int, int], str]]:
    rows = (
        (
            await connection.execute(
                select(collections)
                .where(
                    or_(
                        collections.c.subject_id.in_([source.source_id for source in sources]),
                        and_(
                            collections.c.source_kind == "provider_run",
                            collections.c.installation_id == scope.installation_id,
                            collections.c.repository_id == scope.repository_id,
                            tuple_(collections.c.workflow_run_id, collections.c.run_attempt).in_(
                                [
                                    (source.attempt.workflow_run_id, source.attempt.run_attempt)
                                    for source in sources
                                ]
                            ),
                        ),
                    )
                )
                .limit(2 * len(sources) + 1)
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > 2 * len(sources):
        raise PersistenceInvariantViolation("provider source identity exceeds its cardinality")
    by_id: dict[str, ProviderRunCollectionSource | None] = {}
    by_attempt: dict[tuple[int, int], str] = {}
    for row in rows:
        source_id = row["subject_id"]
        if type(source_id) is not str or source_id in by_id:
            raise PersistenceInvariantViolation("provider source row identity is invalid")
        if row["source_kind"] == "reconciliation":
            by_id[source_id] = None
            continue
        source = decode_collection_source(dict(row))
        if type(source) is not ProviderRunCollectionSource:
            raise PersistenceInvariantViolation("provider source kind is invalid")
        by_id[source_id] = source
        if source.attempt.scope == scope:
            key = (source.attempt.workflow_run_id, source.attempt.run_attempt)
            if key in by_attempt:
                raise PersistenceInvariantViolation("provider attempt identity is duplicated")
            by_attempt[key] = source_id
    return by_id, by_attempt


async def _remaining_slots(connection: AsyncConnection, scope: RepositoryScope) -> int:
    population = (
        select(collections.c.subject_id)
        .where(
            collections.c.source_kind == "provider_run",
            collections.c.installation_id == scope.installation_id,
            collections.c.repository_id == scope.repository_id,
        )
        .limit(MAX_PROVIDER_SOURCES_PER_REPOSITORY)
        .subquery()
    )
    count = await connection.scalar(select(func.count()).select_from(population))
    if type(count) is not int or not 0 <= count <= MAX_PROVIDER_SOURCES_PER_REPOSITORY:
        raise PersistenceInvariantViolation("provider source population is unavailable")
    return MAX_PROVIDER_SOURCES_PER_REPOSITORY - count


def _source_order(source: ProviderRunCollectionSource) -> tuple[int, int, str, datetime, str, str]:
    return (
        source.attempt.workflow_run_id,
        source.attempt.run_attempt,
        source.attempt.head_sha,
        source.run_created_at,
        source.provider_api_version,
        source.source_evidence_digest,
    )
