"""PostgreSQL collection lifecycle with fair claims and revision fencing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ci_coordinator.ci_economics import (
    ClaimTransitionResult,
    CollectionClaim,
    CollectionClaimLost,
    CollectionPolicy,
    CollectionState,
    CollectionTerminalized,
    ProviderAttemptSnapshot,
    RetryableCollectionFailureReason,
    SnapshotRecordResult,
    acquire_collection_claim,
    complete_collection_claim,
    defer_collection_claim,
    expire_collection_state,
    initial_collection_state,
    reject_collection_claim,
)
from ci_coordinator.ci_economics.sources import (
    CollectionSource,
    ProviderRunCollectionSource,
    ProviderSourceRegistrationResult,
    ReconciliationCollectionSource,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_economics_collection_codec import (
    decode_collection_source,
    decode_collection_state,
    encode_collection_record,
    encode_collection_transition,
)
from ci_coordinator.persistence.ci_economics_repository import (
    _PostgresCiEconomicsRepository,
)
from ci_coordinator.persistence.ci_economics_source_registration import register_provider_sources
from ci_coordinator.persistence.errors import (
    PersistenceError,
    PersistenceInvariantViolation,
)
from ci_coordinator.persistence.reconciliation_subject_codec import decode_subject_row
from ci_coordinator.persistence.schema import (
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshots,
    reconciliation_results,
    reconciliation_subjects,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)
from ci_coordinator.reconciliation import ReconciliationContract, ReconciliationSubject


class _PostgresCiEconomicsCollectionRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        evidence: _PostgresCiEconomicsRepository,
        reconciliation_profile: ShadowReconciliationStateProfile,
        collection_policy: CollectionPolicy,
        maximum_terminalizations_per_claim: int,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._evidence = evidence
        self._reconciliation_profile = reconciliation_profile
        self._policy = collection_policy
        _require_limit(
            maximum_terminalizations_per_claim,
            100,
            "collection terminalization",
        )
        self._maximum_terminalizations_per_claim = maximum_terminalizations_per_claim
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def register_eligible(self, *, limit: int) -> int:
        self._ensure_active()
        _require_limit(limit, 100, "collection registration")
        database_now = func.statement_timestamp()
        window = text(f"INTERVAL '{self._policy.collection_window_seconds} seconds'")
        terminal_result_exists = exists(
            select(reconciliation_results.c.subject_id).where(
                reconciliation_results.c.subject_id == reconciliation_subjects.c.subject_id
            )
        )
        state_exists = exists(
            select(ci_workflow_attempt_collections.c.subject_id).where(
                ci_workflow_attempt_collections.c.subject_id == reconciliation_subjects.c.subject_id
            )
        )
        try:
            rows = (
                await self._connection.execute(
                    select(
                        reconciliation_subjects,
                        database_now.label("database_now"),
                    )
                    .where(
                        terminal_result_exists,
                        ~state_exists,
                        reconciliation_subjects.c.created_at <= database_now,
                        reconciliation_subjects.c.created_at + window > database_now,
                    )
                    .order_by(
                        reconciliation_subjects.c.created_at,
                        reconciliation_subjects.c.subject_id,
                    )
                    .limit(limit)
                )
            ).mappings()
            records = []
            for row in rows:
                subject, contract, _revision, _convergence = decode_subject_row(
                    dict(row), self._reconciliation_profile
                )
                state = initial_collection_state(
                    _text(row["subject_id"], "subject_id"),
                    _time(row["created_at"], "created_at"),
                    _time(row["database_now"], "database_now"),
                    self._policy,
                )
                records.append(
                    encode_collection_record(
                        state, ReconciliationCollectionSource(subject, contract)
                    )
                )
            if not records:
                return 0
            inserted = (
                await self._connection.execute(
                    postgres_insert(ci_workflow_attempt_collections)
                    .values(records)
                    .on_conflict_do_nothing()
                    .returning(ci_workflow_attempt_collections.c.subject_id)
                )
            ).all()
            return len(inserted)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation(
                "CI economics collection registration failed"
            ) from error

    async def claim_next(self, *, worker_id: str) -> CollectionClaim | None:
        self._ensure_active()
        _require_digest(worker_id, "collection worker id")
        try:
            for _ in range(self._maximum_terminalizations_per_claim):
                selected = await self._lock_next_due()
                if selected is None:
                    return None
                state, source, database_now = selected
                outcome = acquire_collection_claim(
                    state,
                    source,
                    worker_id=worker_id,
                    now=database_now,
                    policy=self._policy,
                )
                if outcome is None:
                    return None
                if isinstance(outcome, CollectionTerminalized):
                    if not await self._write_supervisor_transition(state, outcome.state):
                        raise PersistenceInvariantViolation(
                            "collection terminalization CAS failed under row lock"
                        )
                    continue
                if not await self._write_supervisor_transition(state, outcome.state):
                    raise PersistenceInvariantViolation(
                        "collection acquisition CAS failed under row lock"
                    )
                return outcome.claim
            return None
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("CI economics claim acquisition failed") from error

    async def register_provider_source(
        self, source: ProviderRunCollectionSource
    ) -> ProviderSourceRegistrationResult:
        self._ensure_active()
        if type(source) is not ProviderRunCollectionSource:
            raise TypeError("independent registration requires an exact provider source")
        outcomes = await self.register_provider_sources(source.attempt.scope, (source,))
        return outcomes[0]

    async def register_provider_sources(
        self,
        scope: RepositoryScope,
        sources: tuple[ProviderRunCollectionSource, ...],
    ) -> tuple[ProviderSourceRegistrationResult, ...]:
        self._ensure_active()
        try:
            return await register_provider_sources(self._connection, scope, sources, self._policy)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("provider source registration failed") from error

    async def defer_claim(
        self,
        claim: CollectionClaim,
        reason: RetryableCollectionFailureReason,
    ) -> ClaimTransitionResult:
        self._ensure_active()
        try:
            state, database_now = await self._lock_claim_state(claim)
            outcome = defer_collection_claim(
                state,
                claim,
                database_now,
                reason,
                self._policy,
            )
            if isinstance(outcome, CollectionClaimLost):
                return "claim_lost"
            return (
                "applied"
                if await self._write_holder_transition(state, outcome, claim)
                else "claim_lost"
            )
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("CI economics claim deferral failed") from error

    async def reject_claim(self, claim: CollectionClaim) -> ClaimTransitionResult:
        self._ensure_active()
        try:
            state, database_now = await self._lock_claim_state(claim)
            outcome = reject_collection_claim(state, claim, database_now)
            if isinstance(outcome, CollectionClaimLost):
                return "claim_lost"
            return (
                "applied"
                if await self._write_holder_transition(state, outcome, claim)
                else "claim_lost"
            )
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("CI economics claim rejection failed") from error

    async def record_snapshot(
        self,
        claim: CollectionClaim,
        snapshot: ProviderAttemptSnapshot,
    ) -> SnapshotRecordResult:
        self._ensure_active()
        if type(snapshot) is not ProviderAttemptSnapshot:
            raise TypeError("collection completion requires an exact provider snapshot")
        try:
            state, database_now = await self._lock_claim_state(claim)
            source = claim.source
            if snapshot.subject_id != source.source_id or snapshot.attempt != source.attempt:
                raise ValueError("provider snapshot belongs to another collection claim")
            successor = complete_collection_claim(state, claim, database_now)
            if isinstance(successor, CollectionClaimLost):
                return "claim_lost"
            await self._evidence.record_snapshot(
                snapshot,
                source,
                retain_until=state.evidence_retain_until,
            )
            if not await self._write_holder_transition(state, successor, claim):
                self._mark_rollback_required()
                return "claim_lost"
            return "captured"
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation(
                "CI economics snapshot completion failed"
            ) from error

    async def expire_evidence(self, *, limit: int) -> int:
        self._ensure_active()
        _require_limit(limit, 1_000, "collection expiry")
        try:
            database_now = func.statement_timestamp()
            snapshot_exists = exists(
                select(ci_workflow_attempt_snapshots.c.subject_id).where(
                    ci_workflow_attempt_snapshots.c.subject_id
                    == ci_workflow_attempt_collections.c.subject_id
                )
            )
            rows = tuple(
                (
                    await self._connection.execute(
                        select(
                            ci_workflow_attempt_collections,
                            database_now.label("database_now"),
                            snapshot_exists.label("snapshot_exists"),
                        )
                        .where(
                            ci_workflow_attempt_collections.c.status.in_(
                                ("captured", "terminal_unavailable")
                            ),
                            ci_workflow_attempt_collections.c.evidence_retain_until <= database_now,
                        )
                        .order_by(
                            ci_workflow_attempt_collections.c.evidence_retain_until,
                            ci_workflow_attempt_collections.c.subject_id,
                        )
                        .limit(limit)
                        .with_for_update(of=ci_workflow_attempt_collections, skip_locked=True)
                    )
                ).mappings()
            )
            expired = 0
            for row in rows:
                state = decode_collection_state(dict(row))
                now = _time(row["database_now"], "database_now")
                successor = expire_collection_state(state, now)
                if successor is None:
                    raise PersistenceInvariantViolation("selected collection state cannot expire")
                has_snapshot = row["snapshot_exists"] is True
                if has_snapshot != (state.status == "captured"):
                    raise PersistenceInvariantViolation(
                        "terminal collection outcome diverges from snapshot presence"
                    )
                await self._connection.execute(
                    delete(ci_job_measurement_reports).where(
                        ci_job_measurement_reports.c.subject_id == state.subject_id,
                        ci_job_measurement_reports.c.retain_until <= func.statement_timestamp(),
                    )
                )
                if state.status == "captured":
                    deleted = await self._connection.scalar(
                        delete(ci_workflow_attempt_snapshots)
                        .where(
                            ci_workflow_attempt_snapshots.c.subject_id == state.subject_id,
                            ci_workflow_attempt_snapshots.c.retain_until
                            <= func.statement_timestamp(),
                        )
                        .returning(ci_workflow_attempt_snapshots.c.subject_id)
                    )
                    if deleted != state.subject_id:
                        raise PersistenceInvariantViolation(
                            "captured collection state has no expirable snapshot"
                        )
                if not await self._write_expiry_transition(state, successor):
                    raise PersistenceInvariantViolation(
                        "collection expiry CAS failed under row lock"
                    )
                expired += 1
            return expired
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("CI economics evidence expiry failed") from error

    async def purge_tombstones(self, *, limit: int) -> int:
        self._ensure_active()
        _require_limit(limit, 1_000, "collection tombstone purge")
        try:
            database_now = func.statement_timestamp()
            candidates = (
                select(ci_workflow_attempt_collections.c.subject_id)
                .where(
                    ci_workflow_attempt_collections.c.status == "expired",
                    ci_workflow_attempt_collections.c.tombstone_retain_until <= database_now,
                    ci_workflow_attempt_collections.c.deadline_at <= database_now,
                )
                .order_by(
                    ci_workflow_attempt_collections.c.tombstone_retain_until,
                    ci_workflow_attempt_collections.c.subject_id,
                )
                .limit(limit)
                .with_for_update(of=ci_workflow_attempt_collections, skip_locked=True)
            )
            deleted = (
                await self._connection.execute(
                    delete(ci_workflow_attempt_collections)
                    .where(ci_workflow_attempt_collections.c.subject_id.in_(candidates))
                    .returning(ci_workflow_attempt_collections.c.subject_id)
                )
            ).all()
            return len(deleted)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError) as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("CI economics tombstone purge failed") from error

    async def _lock_next_due(self) -> tuple[CollectionState, CollectionSource, datetime] | None:
        database_now = func.statement_timestamp()
        due = or_(
            and_(
                ci_workflow_attempt_collections.c.status.in_(("pending", "deferred")),
                ci_workflow_attempt_collections.c.next_attempt_at <= database_now,
            ),
            and_(
                ci_workflow_attempt_collections.c.status == "leased",
                ci_workflow_attempt_collections.c.lease_expires_at <= database_now,
            ),
        )
        row = (
            (
                await self._connection.execute(
                    select(
                        ci_workflow_attempt_collections,
                        database_now.label("database_now"),
                    )
                    .where(due)
                    .order_by(
                        func.coalesce(
                            ci_workflow_attempt_collections.c.next_attempt_at,
                            ci_workflow_attempt_collections.c.lease_expires_at,
                        ),
                        ci_workflow_attempt_collections.c.source_created_at,
                        ci_workflow_attempt_collections.c.subject_id,
                    )
                    .limit(1)
                    .with_for_update(of=ci_workflow_attempt_collections, skip_locked=True)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        mapping = dict(row)
        return (
            decode_collection_state(mapping),
            await self._load_source(mapping),
            _time(row["database_now"], "database_now"),
        )

    async def _lock_claim_state(
        self,
        claim: CollectionClaim,
    ) -> tuple[CollectionState, datetime]:
        if type(claim) is not CollectionClaim:
            raise TypeError("collection transition requires an exact claim")
        database_now = func.statement_timestamp()
        row = (
            (
                await self._connection.execute(
                    select(
                        ci_workflow_attempt_collections,
                        database_now.label("database_now"),
                    )
                    .where(ci_workflow_attempt_collections.c.subject_id == claim.source.source_id)
                    .with_for_update(of=ci_workflow_attempt_collections)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise PersistenceInvariantViolation("active collection state disappeared")
        if await self._load_source(dict(row)) != claim.source:
            raise PersistenceInvariantViolation("collection claim source changed")
        return decode_collection_state(dict(row)), _time(row["database_now"], "database_now")

    async def _load_source(self, row: dict[str, object]) -> CollectionSource:
        reconciliation = None
        if row.get("source_kind") == "reconciliation":
            subject, contract = await self._load_subject(
                _text(row.get("legacy_subject_id"), "legacy_subject_id")
            )
            reconciliation = ReconciliationCollectionSource(subject, contract)
        return decode_collection_source(row, reconciliation=reconciliation)

    async def _load_subject(
        self,
        subject_id: str,
    ) -> tuple[ReconciliationSubject, ReconciliationContract]:
        row = (
            (
                await self._connection.execute(
                    select(reconciliation_subjects).where(
                        reconciliation_subjects.c.subject_id == subject_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise PersistenceInvariantViolation("collection source subject is unavailable")
        subject, contract, _revision, _convergence = decode_subject_row(
            dict(row),
            self._reconciliation_profile,
        )
        return subject, contract

    async def _write_supervisor_transition(
        self,
        previous: CollectionState,
        successor: CollectionState,
    ) -> bool:
        authority: list[ColumnElement[bool]] = [
            ci_workflow_attempt_collections.c.subject_id == previous.subject_id,
            ci_workflow_attempt_collections.c.policy_hash == previous.policy_hash,
            ci_workflow_attempt_collections.c.revision == previous.revision,
            ci_workflow_attempt_collections.c.status == previous.status,
            ci_workflow_attempt_collections.c.claim_generation == previous.claim_generation,
            ci_workflow_attempt_collections.c.attempt_count == previous.attempt_count,
        ]
        if previous.status == "leased":
            authority.extend(
                (
                    ci_workflow_attempt_collections.c.lease_owner_id == previous.lease_owner_id,
                    ci_workflow_attempt_collections.c.lease_token == previous.lease_token,
                    ci_workflow_attempt_collections.c.lease_acquired_at
                    == previous.lease_acquired_at,
                    ci_workflow_attempt_collections.c.lease_expires_at == previous.lease_expires_at,
                    ci_workflow_attempt_collections.c.lease_expires_at
                    <= func.statement_timestamp(),
                )
            )
        else:
            authority.extend(
                (
                    ci_workflow_attempt_collections.c.next_attempt_at == previous.next_attempt_at,
                    ci_workflow_attempt_collections.c.next_attempt_at <= func.statement_timestamp(),
                )
            )
        if successor.status == "leased":
            lease_expires_at = successor.lease_expires_at
            if lease_expires_at is None:
                raise AssertionError("leased collection successor has no expiry")
            authority.append(func.statement_timestamp() < lease_expires_at)
        return await self._update_state(successor, *authority)

    async def _write_holder_transition(
        self,
        previous: CollectionState,
        successor: CollectionState,
        claim: CollectionClaim,
    ) -> bool:
        return await self._update_state(
            successor,
            ci_workflow_attempt_collections.c.subject_id == claim.source.source_id,
            ci_workflow_attempt_collections.c.policy_hash == claim.policy_hash,
            ci_workflow_attempt_collections.c.status == "leased",
            ci_workflow_attempt_collections.c.revision == claim.revision,
            ci_workflow_attempt_collections.c.claim_generation == claim.generation,
            ci_workflow_attempt_collections.c.attempt_count == claim.attempt_count,
            ci_workflow_attempt_collections.c.lease_owner_id == claim.worker_id,
            ci_workflow_attempt_collections.c.lease_token == claim.token,
            ci_workflow_attempt_collections.c.lease_acquired_at == claim.claimed_at,
            ci_workflow_attempt_collections.c.lease_expires_at == claim.lease_expires_at,
            ci_workflow_attempt_collections.c.lease_expires_at > func.statement_timestamp(),
        )

    async def _write_expiry_transition(
        self,
        previous: CollectionState,
        successor: CollectionState,
    ) -> bool:
        return await self._update_state(
            successor,
            ci_workflow_attempt_collections.c.subject_id == previous.subject_id,
            ci_workflow_attempt_collections.c.policy_hash == previous.policy_hash,
            ci_workflow_attempt_collections.c.revision == previous.revision,
            ci_workflow_attempt_collections.c.status == previous.status,
            ci_workflow_attempt_collections.c.evidence_retain_until <= func.statement_timestamp(),
        )

    async def _update_state(
        self,
        successor: CollectionState,
        *authority: ColumnElement[bool],
    ) -> bool:
        values = encode_collection_transition(successor)
        values["updated_at"] = func.statement_timestamp()
        updated = await self._connection.scalar(
            update(ci_workflow_attempt_collections)
            .where(*authority)
            .values(**values)
            .returning(ci_workflow_attempt_collections.c.subject_id)
        )
        return updated == successor.subject_id


def _require_limit(value: object, maximum: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} limit is invalid")


def _require_digest(value: object, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _time(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value
