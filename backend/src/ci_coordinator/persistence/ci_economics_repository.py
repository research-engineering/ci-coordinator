"""PostgreSQL persistence for exact CI economics evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import and_, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics import (
    MAX_JOBS_PER_ATTEMPT,
    AttemptEconomics,
    AttemptIdentity,
    AttemptSnapshot,
    AttemptSummary,
    AttemptSummaryPage,
    CiEconomicsEvidenceConflict,
    CiEconomicsStoreUnavailable,
    JobTiming,
    ProviderAttemptSnapshot,
    RunnerIdentity,
    WorkflowJobFact,
    decode_attempt_cursor,
    derive_attempt_economics,
)
from ci_coordinator.ci_economics.catalog import (
    ProviderSourcePage,
    RecordedProviderSource,
    decode_source_cursor,
    require_catalog_page,
)
from ci_coordinator.ci_economics.measurement import derive_attempt_measurements
from ci_coordinator.ci_economics.model import PlannedRoute, WorkflowConclusion
from ci_coordinator.ci_economics.read_models import (
    IndependentAttemptSnapshot,
    RecordedAttemptEconomics,
)
from ci_coordinator.ci_economics.sources import (
    CollectionSource,
    ProviderRunCollectionSource,
    ReconciliationCollectionSource,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
)
from ci_coordinator.persistence.ci_economics_collection_codec import (
    decode_collection_source,
    decode_collection_state,
)
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.schema import (
    ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshot_jobs,
    ci_workflow_attempt_snapshots,
    ci_workflow_observations,
)
from ci_coordinator.persistence.workflow_observation_lock import (
    MAX_OBSERVATION_LOCK_BATCH,
    try_lock_workflow_observations,
)
from ci_coordinator.reconciliation import ReconciliationContract

_MAX_JOB_CANONICAL_BYTES = 8_192


class _PostgresCiEconomicsRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def list_provider_sources(
        self, scope: RepositoryScope, *, after_cursor: str | None, limit: int
    ) -> ProviderSourcePage:
        self._ensure_active()
        require_catalog_page(scope, limit)
        cursor = None if after_cursor is None else decode_source_cursor(after_cursor)
        source = ci_workflow_attempt_collections.c
        statement = select(ci_workflow_attempt_collections).where(
            source.installation_id == scope.installation_id,
            source.repository_id == scope.repository_id,
            source.source_kind == "provider_run",
            source.status != "expired",
            source.evidence_retain_until > func.statement_timestamp(),
        )
        if cursor is not None:
            statement = statement.where(tuple_(source.workflow_run_id, source.run_attempt) < cursor)
        try:
            rows = (
                (
                    await self._connection.execute(
                        statement.order_by(
                            source.workflow_run_id.desc(), source.run_attempt.desc()
                        ).limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
            items: list[RecordedProviderSource] = []
            for row in rows[:limit]:
                values = dict(row)
                provenance = decode_collection_source(values)
                if type(provenance) is not ProviderRunCollectionSource:
                    raise ValueError("source catalog returned another source kind")
                items.append(RecordedProviderSource(provenance, decode_collection_state(values)))
            next_cursor = items[-1].cursor if len(rows) > limit else None
            return ProviderSourcePage(scope, tuple(items), next_cursor)
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable("provider source catalog is unavailable") from error

    async def record_snapshot(
        self,
        snapshot: ProviderAttemptSnapshot,
        source: CollectionSource,
        *,
        retain_until: datetime,
    ) -> Literal["captured", "replayed"]:
        self._ensure_active()
        if type(snapshot) is not ProviderAttemptSnapshot:
            raise TypeError("CI economics store requires an exact provider snapshot")
        if type(source) not in {ReconciliationCollectionSource, ProviderRunCollectionSource}:
            raise TypeError("CI economics store requires an exact collection source")
        if snapshot.subject_id != source.source_id or snapshot.attempt != source.attempt:
            raise ValueError("CI economics snapshot belongs to another collection source")
        retain_until = _aware_utc(retain_until, "retain_until")
        try:
            header = _provider_snapshot_header(snapshot, source, retain_until)
            inserted = await self._connection.scalar(
                postgres_insert(ci_workflow_attempt_snapshots)
                .values(**header)
                .on_conflict_do_nothing()
                .returning(ci_workflow_attempt_snapshots.c.subject_id)
            )
            if inserted == snapshot.subject_id:
                await self._connection.execute(
                    postgres_insert(ci_workflow_attempt_snapshot_jobs),
                    [_snapshot_job_row(snapshot.subject_id, job) for job in snapshot.jobs],
                )
                return "captured"
            existing = (
                (
                    await self._connection.execute(
                        select(ci_workflow_attempt_snapshots).where(
                            ci_workflow_attempt_snapshots.c.subject_id == snapshot.subject_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                existing is not None
                and all(existing[key] == value for key, value in header.items())
                and await self._load_snapshot_jobs(dict(existing)) == snapshot.jobs
            ):
                return "replayed"
            self._mark_rollback_required()
            raise CiEconomicsEvidenceConflict("terminal attempt snapshot conflicts with history")
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CiEconomicsEvidenceConflict:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise CiEconomicsStoreUnavailable("CI attempt snapshot is unavailable") from error

    async def list_attempts(
        self,
        scope: RepositoryScope,
        *,
        after_cursor: str | None,
        limit: int,
    ) -> AttemptSummaryPage:
        self._ensure_active()
        if type(scope) is not RepositoryScope:
            raise TypeError("CI economics query requires an exact repository scope")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("CI economics page limit is invalid")
        cursor = None if after_cursor is None else decode_attempt_cursor(after_cursor)
        statement = select(ci_workflow_attempt_snapshots).where(
            ci_workflow_attempt_snapshots.c.installation_id == scope.installation_id,
            ci_workflow_attempt_snapshots.c.repository_id == scope.repository_id,
            ci_workflow_attempt_snapshots.c.source_kind == "reconciliation",
        )
        if cursor is not None:
            recorded_at, subject_id = cursor
            statement = statement.where(
                or_(
                    ci_workflow_attempt_snapshots.c.recorded_at < recorded_at,
                    and_(
                        ci_workflow_attempt_snapshots.c.recorded_at == recorded_at,
                        ci_workflow_attempt_snapshots.c.subject_id < subject_id,
                    ),
                )
            )
        try:
            rows = tuple(
                (
                    await self._connection.execute(
                        statement.order_by(
                            ci_workflow_attempt_snapshots.c.recorded_at.desc(),
                            ci_workflow_attempt_snapshots.c.subject_id.desc(),
                        ).limit(limit + 1)
                    )
                ).mappings()
            )
            items = tuple(_summary_from_header(dict(row)) for row in rows[:limit])
            return AttemptSummaryPage(
                items=items,
                next_cursor=items[-1].cursor if len(rows) > limit else None,
            )
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable("CI attempt summaries are unavailable") from error

    async def load_attempt(self, attempt: AttemptIdentity) -> AttemptEconomics | None:
        self._ensure_active()
        if type(attempt) is not AttemptIdentity:
            raise TypeError("CI economics query requires an exact attempt identity")
        try:
            header = (
                (
                    await self._connection.execute(
                        select(ci_workflow_attempt_snapshots).where(
                            ci_workflow_attempt_snapshots.c.source_kind == "reconciliation",
                            ci_workflow_attempt_snapshots.c.installation_id
                            == attempt.scope.installation_id,
                            ci_workflow_attempt_snapshots.c.repository_id
                            == attempt.scope.repository_id,
                            ci_workflow_attempt_snapshots.c.workflow_run_id
                            == attempt.workflow_run_id,
                            ci_workflow_attempt_snapshots.c.run_attempt == attempt.run_attempt,
                            ci_workflow_attempt_snapshots.c.head_sha == attempt.head_sha,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if header is None:
                return None
            snapshot = await self._load_snapshot(_text(header["subject_id"], "subject_id"))
            if snapshot is None:
                raise CiEconomicsStoreUnavailable("CI attempt snapshot disappeared")
            observations = await self._list_workflow_job_observations(
                attempt,
                limit=len(snapshot.jobs) + 1,
            )
            return derive_attempt_economics(snapshot, observations)
        except asyncio.CancelledError:
            raise
        except CiEconomicsStoreUnavailable:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable("CI attempt economics are unavailable") from error

    async def load_measurements(self, attempt: AttemptIdentity) -> RecordedAttemptEconomics | None:
        self._ensure_active()
        if type(attempt) is not AttemptIdentity:
            raise TypeError("CI measurement read requires an exact attempt identity")
        try:
            row = (
                (
                    await self._connection.execute(
                        select(ci_workflow_attempt_snapshots).where(
                            ci_workflow_attempt_snapshots.c.installation_id
                            == attempt.scope.installation_id,
                            ci_workflow_attempt_snapshots.c.repository_id
                            == attempt.scope.repository_id,
                            ci_workflow_attempt_snapshots.c.workflow_run_id
                            == attempt.workflow_run_id,
                            ci_workflow_attempt_snapshots.c.run_attempt == attempt.run_attempt,
                            ci_workflow_attempt_snapshots.c.head_sha == attempt.head_sha,
                            ci_workflow_attempt_snapshots.c.retain_until
                            > func.statement_timestamp(),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            header = dict(row)
            subject_id = _text(header["subject_id"], "subject_id")
            if header["source_kind"] == "reconciliation":
                legacy = await self._load_snapshot(subject_id)
                if legacy is None:
                    raise CiEconomicsStoreUnavailable(
                        "retained CI measurement snapshot disappeared"
                    )
                snapshot: AttemptSnapshot | IndependentAttemptSnapshot = legacy
                evidence: AttemptSnapshot | ProviderAttemptSnapshot = legacy
            elif header["source_kind"] == "provider_run":
                source_row = (
                    (
                        await self._connection.execute(
                            select(ci_workflow_attempt_collections).where(
                                ci_workflow_attempt_collections.c.subject_id == subject_id
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if source_row is None or (
                    header["contract_hash"] is not None
                    or header["planned_route"] != "unknown"
                    or source_row["evidence_retain_until"] != header["retain_until"]
                ):
                    raise CiEconomicsStoreUnavailable("retained provider source binding is invalid")
                source = decode_collection_source(dict(source_row))
                if type(source) is not ProviderRunCollectionSource:
                    raise CiEconomicsStoreUnavailable("retained provider source kind changed")
                evidence = ProviderAttemptSnapshot(
                    subject_id,
                    _attempt_from_header(header),
                    _text(header["snapshot_digest"], "snapshot_digest"),
                    await self._load_snapshot_jobs(header),
                )
                snapshot = IndependentAttemptSnapshot(
                    source,
                    evidence,
                    _aware_utc(header["recorded_at"], "recorded_at"),
                    _aware_utc(header["retain_until"], "retain_until"),
                )
            else:
                raise CiEconomicsStoreUnavailable("retained measurement source kind is unsupported")
            observations = await self._list_workflow_job_observations(
                attempt, limit=len(evidence.jobs) + 1
            )
            return RecordedAttemptEconomics(
                snapshot, derive_attempt_measurements(evidence, observations)
            )
        except asyncio.CancelledError:
            raise
        except CiEconomicsStoreUnavailable:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable("retained CI measurements are unavailable") from error

    async def _list_workflow_job_observations(
        self,
        attempt: AttemptIdentity,
        *,
        limit: int,
    ) -> tuple[WorkflowJobFact, ...]:
        from ci_coordinator.persistence.ci_economics_codec import (
            decode_workflow_job_observation,
        )

        try:
            rows = (
                (
                    await self._connection.execute(
                        select(ci_workflow_observations)
                        .where(
                            ci_workflow_observations.c.installation_id
                            == attempt.scope.installation_id,
                            ci_workflow_observations.c.repository_id == attempt.scope.repository_id,
                            ci_workflow_observations.c.workflow_run_id == attempt.workflow_run_id,
                            ci_workflow_observations.c.run_attempt == attempt.run_attempt,
                            ci_workflow_observations.c.head_sha == attempt.head_sha,
                            ci_workflow_observations.c.observation_kind == "workflow_job",
                        )
                        .order_by(
                            ci_workflow_observations.c.provider_job_id,
                            ci_workflow_observations.c.semantic_hash,
                            ci_workflow_observations.c.delivery_id,
                        )
                        .distinct(
                            ci_workflow_observations.c.provider_job_id,
                            ci_workflow_observations.c.semantic_hash,
                        )
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            return tuple(decode_workflow_job_observation(dict(row)) for row in rows)
        except asyncio.CancelledError:
            raise
        except CiEconomicsStoreUnavailable:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable(
                "workflow job observations are unavailable"
            ) from error

    async def delete_expired_observations(self, *, limit: int) -> int:
        self._ensure_active()
        if type(limit) is not int or not 1 <= limit <= MAX_OBSERVATION_LOCK_BATCH:
            raise ValueError("CI economics cleanup limit is invalid")
        database_now = func.statement_timestamp()
        try:
            candidates = tuple(
                await self._connection.scalars(
                    select(ci_workflow_observations.c.delivery_id)
                    .where(ci_workflow_observations.c.retain_until <= database_now)
                    .order_by(
                        ci_workflow_observations.c.retain_until,
                        ci_workflow_observations.c.delivery_id,
                    )
                    .limit(limit)
                )
            )
            guarded = await try_lock_workflow_observations(self._connection, candidates)
            if not guarded:
                return 0
            deleted_observations = (
                await self._connection.execute(
                    delete(ci_workflow_observations)
                    .where(
                        ci_workflow_observations.c.delivery_id.in_(guarded),
                        ci_workflow_observations.c.retain_until <= database_now,
                    )
                    .returning(ci_workflow_observations.c.delivery_id)
                )
            ).all()
            return len(deleted_observations)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError) as error:
            self._mark_rollback_required()
            raise CiEconomicsStoreUnavailable("CI evidence cleanup is unavailable") from error

    async def _load_snapshot(self, subject_id: str) -> AttemptSnapshot | None:
        header = (
            (
                await self._connection.execute(
                    select(ci_workflow_attempt_snapshots).where(
                        ci_workflow_attempt_snapshots.c.subject_id == subject_id,
                        ci_workflow_attempt_snapshots.c.source_kind == "reconciliation",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if header is None:
            return None
        jobs = await self._load_snapshot_jobs(dict(header))
        return AttemptSnapshot(
            subject_id=_text(header["subject_id"], "subject_id"),
            attempt=_attempt_from_header(dict(header)),
            contract_hash=_text(header["contract_hash"], "contract_hash"),
            planned_route=cast(
                PlannedRoute,
                _text(header["planned_route"], "planned_route"),
            ),
            recorded_at=_aware_utc(header["recorded_at"], "recorded_at"),
            retain_until=_aware_utc(header["retain_until"], "retain_until"),
            snapshot_digest=_text(header["snapshot_digest"], "snapshot_digest"),
            jobs=jobs,
        )

    async def _load_snapshot_jobs(self, header: dict[str, object]) -> tuple[WorkflowJobFact, ...]:
        subject_id = _text(header["subject_id"], "subject_id")
        job_rows = (
            (
                await self._connection.execute(
                    select(ci_workflow_attempt_snapshot_jobs)
                    .where(ci_workflow_attempt_snapshot_jobs.c.subject_id == subject_id)
                    .order_by(ci_workflow_attempt_snapshot_jobs.c.provider_job_id)
                    .limit(MAX_JOBS_PER_ATTEMPT + 1)
                )
            )
            .mappings()
            .all()
        )
        if len(job_rows) > MAX_JOBS_PER_ATTEMPT:
            raise CiEconomicsStoreUnavailable("snapshot job bound exceeded")
        stored_job_count = _positive(header["job_count"], "job_count")
        if stored_job_count != len(job_rows):
            raise CiEconomicsStoreUnavailable(
                "snapshot header job count diverges from retained jobs"
            )
        return tuple(_decode_snapshot_job(dict(row), header) for row in job_rows)


def _summary_from_header(header: dict[str, object]) -> AttemptSummary:
    return AttemptSummary(
        subject_id=_text(header.get("subject_id"), "subject_id"),
        attempt=_attempt_from_header(header),
        contract_hash=_text(header.get("contract_hash"), "contract_hash"),
        planned_route=cast(PlannedRoute, _text(header.get("planned_route"), "planned_route")),
        job_count=_positive(header.get("job_count"), "job_count"),
        snapshot_digest=_text(header.get("snapshot_digest"), "snapshot_digest"),
        recorded_at=_aware_utc(header.get("recorded_at"), "recorded_at"),
    )


def _planned_route(contract: ReconciliationContract) -> PlannedRoute:
    if contract.candidate_evidence is not None:
        return "full_ci_counterfactual"
    return "selected" if contract.planning_evidence is not None else "unknown"


def _provider_snapshot_header(
    snapshot: ProviderAttemptSnapshot,
    source: CollectionSource,
    retain_until: datetime,
) -> dict[str, object]:
    attempt = snapshot.attempt
    contract = source.contract if isinstance(source, ReconciliationCollectionSource) else None
    return {
        "subject_id": snapshot.subject_id,
        "source_kind": source.kind,
        "installation_id": attempt.scope.installation_id,
        "repository_id": attempt.scope.repository_id,
        "workflow_run_id": attempt.workflow_run_id,
        "run_attempt": attempt.run_attempt,
        "head_sha": attempt.head_sha,
        "contract_hash": None if contract is None else contract.contract_hash,
        "planned_route": "unknown" if contract is None else _planned_route(contract),
        "job_count": len(snapshot.jobs),
        "snapshot_digest": snapshot.snapshot_digest,
        "retain_until": retain_until,
    }


def _snapshot_job_row(subject_id: str, job: WorkflowJobFact) -> dict[str, object]:
    mapping = job.canonical_mapping()
    return {
        "subject_id": subject_id,
        "provider_job_id": job.provider_job_id,
        "name": job.name,
        "conclusion": job.conclusion,
        "created_at": job.timing.created_at,
        "started_at": job.timing.started_at,
        "completed_at": job.timing.completed_at,
        "runner_id": job.runner.runner_id,
        "runner_name": job.runner.runner_name,
        "runner_group_id": job.runner.runner_group_id,
        "runner_group_name": job.runner.runner_group_name,
        "labels_canonical_json": canonical_json(list(job.labels)),
        "semantic_hash": job.semantic_hash,
        "job_canonical_json": encode_canonical_object(
            mapping,
            maximum_bytes=_MAX_JOB_CANONICAL_BYTES,
            context="CI snapshot job",
        ),
    }


def _decode_snapshot_job(
    row: dict[str, object],
    header: dict[str, object],
) -> WorkflowJobFact:
    mapping = decode_canonical_object(
        row.get("job_canonical_json"),
        maximum_bytes=_MAX_JOB_CANONICAL_BYTES,
        context="CI snapshot job",
    )
    attempt = _attempt_from_header(header)
    labels = decode_canonical_object(
        b'{"labels":' + _bytes(row.get("labels_canonical_json"), "labels") + b"}",
        maximum_bytes=8_192,
        context="CI snapshot labels",
    ).get("labels")
    if type(labels) is not list or any(type(item) is not str for item in labels):
        raise CiEconomicsStoreUnavailable("snapshot labels are invalid")
    fact = WorkflowJobFact(
        attempt=attempt,
        provider_job_id=_positive(row.get("provider_job_id"), "provider_job_id"),
        name=_text(row.get("name"), "name"),
        conclusion=cast(WorkflowConclusion, _text(row.get("conclusion"), "conclusion")),
        timing=JobTiming(
            _optional_aware(row.get("created_at"), "created_at"),
            _optional_aware(row.get("started_at"), "started_at"),
            _optional_aware(row.get("completed_at"), "completed_at"),
        ),
        labels=tuple(labels),
        runner=RunnerIdentity(
            _optional_positive(row.get("runner_id"), "runner_id"),
            _optional_text(row.get("runner_name"), "runner_name"),
            _optional_positive(row.get("runner_group_id"), "runner_group_id"),
            _optional_text(row.get("runner_group_name"), "runner_group_name"),
        ),
        semantic_hash=_text(row.get("semantic_hash"), "semantic_hash"),
        delivery_id=None,
    )
    if fact.canonical_mapping() != mapping:
        raise CiEconomicsStoreUnavailable("snapshot job columns diverge from canonical evidence")
    return fact


def _attempt_from_header(header: dict[str, object]) -> AttemptIdentity:
    return AttemptIdentity(
        RepositoryScope(
            _positive(header.get("installation_id"), "installation_id"),
            _positive(header.get("repository_id"), "repository_id"),
        ),
        _positive(header.get("workflow_run_id"), "workflow_run_id"),
        _positive(header.get("run_attempt"), "run_attempt"),
        _text(header.get("head_sha"), "head_sha"),
    )


def _aware_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _optional_aware(value: object, name: str) -> datetime | None:
    return None if value is None else _aware_utc(value, name)


def _positive(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _optional_positive(value: object, name: str) -> int | None:
    return None if value is None else _positive(value, name)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _bytes(value: object, name: str) -> bytes:
    if type(value) is not bytes or not value:
        raise ValueError(f"{name} must be non-empty bytes")
    return value
