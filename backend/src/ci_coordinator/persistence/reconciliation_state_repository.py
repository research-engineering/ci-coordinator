"""PostgreSQL reconciliation state with durable claim and lease fencing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sqlalchemy import func, insert, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ci_coordinator.persistence.canonical_row import CanonicalRowCodecError
from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable
from ci_coordinator.persistence.production_issuance_guard import production_guard_time_predicates
from ci_coordinator.persistence.reconciliation_queries import (
    next_reconciliation_claim_statement,
)
from ci_coordinator.persistence.reconciliation_state_codec import (
    decode_result_row,
    decode_subject_row,
    encode_observation_row,
    encode_result_row,
    encode_subject_row,
    snapshot_from_rows,
)
from ci_coordinator.persistence.schema import (
    reconciliation_observations,
    reconciliation_results,
    reconciliation_subjects,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.reconciliation import (
    ReconciliationAttemptClaim,
    ReconciliationClaimLost,
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationConvergenceState,
    ReconciliationResult,
    ReconciliationSnapshot,
    ReconciliationSubject,
    SignalObservation,
    acquire_reconciliation_claim,
    defer_reconciliation_claim,
    reconciliation_claim_is_active,
    release_terminal_claim,
)
from ci_coordinator.reconciliation.snapshot import (
    ObservationAppend,
    ObservationAppended,
    ResultRecord,
    ResultRecorded,
    SubjectRegistered,
    SubjectRegistration,
    SubjectRegistrationConflict,
)
from ci_coordinator.reconciliation.state_store import (
    append_observation as apply_observation,
)
from ci_coordinator.reconciliation.state_store import record_result as apply_result
from ci_coordinator.reconciliation.state_store import register_subject as apply_registration

type ClaimedSnapshot = tuple[ReconciliationSnapshot, ReconciliationConvergenceState]
type ObservationPersistenceResult = ObservationAppend | ReconciliationClaimLost
type ResultPersistenceResult = ResultRecord | ReconciliationClaimLost


class _PostgresReconciliationRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        profile: ShadowReconciliationStateProfile,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._profile = profile
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def register_subject(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
        convergence: ReconciliationConvergenceState,
        *,
        execution_origin: Literal["legacy", "full_ci", "selected"] = "legacy",
        production_guard: ProductionIssuanceGuard | None = None,
    ) -> SubjectRegistration:
        self._ensure_active()
        if execution_origin not in {"legacy", "full_ci", "selected"} or (
            (execution_origin == "selected") != (production_guard is not None)
        ):
            raise ValueError("reconciliation execution origin requires its exact guard class")
        try:
            attempted = encode_subject_row(subject, contract, convergence, self._profile)
            current = None if production_guard is None else production_guard.current_evidence
            if production_guard is not None and (
                current is None
                or production_guard.reconciliation_subject_id != subject.subject_id
                or production_guard.scope.installation_id != subject.installation_id
                or production_guard.scope.repository_id != subject.repository_id
            ):
                raise ValueError("selected registration lacks its exact current subject evidence")
            origin = {
                "execution_origin": execution_origin,
                "production_generation": None
                if current is None
                else current.scope_grant.relation.generation,
                "production_authority_id": None
                if production_guard is None
                else production_guard.authority_id,
            }
            attempted.update(origin)
            statement = postgres_insert(reconciliation_subjects)
            if production_guard is None:
                statement = statement.values(attempted)
            else:
                statement = statement.from_select(
                    tuple(attempted),
                    select(
                        *(
                            literal(value, type_=reconciliation_subjects.c[name].type).label(name)
                            for name, value in attempted.items()
                        )
                    ).where(*production_guard_time_predicates(production_guard)),
                )
            inserted = await self._connection.scalar(
                statement.on_conflict_do_nothing(
                    index_elements=[reconciliation_subjects.c.subject_id]
                ).returning(reconciliation_subjects.c.subject_id)
            )
            if inserted == subject.subject_id:
                outcome = apply_registration(None, subject, contract)
                if not isinstance(outcome, SubjectRegistered):
                    raise PersistenceInvariantViolation(
                        "new reconciliation subject produced a non-registration outcome"
                    )
                return outcome
            existing = await self._load_snapshot_state(subject, for_update=False)
            if existing is None:
                if production_guard is not None:
                    raise StoreUnavailable(
                        "selected registration authority expired before insertion"
                    )
                raise PersistenceInvariantViolation(
                    "reconciliation subject conflict row is unavailable"
                )
            retained_origin = (
                await self._connection.execute(
                    select(*(reconciliation_subjects.c[name] for name in origin)).where(
                        reconciliation_subjects.c.subject_id == subject.subject_id
                    )
                )
            ).one_or_none()
            if retained_origin is None or tuple(retained_origin) != tuple(origin.values()):
                return SubjectRegistrationConflict(existing[0])
            return apply_registration(existing[0], subject, contract)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CanonicalRowCodecError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("reconciliation state row is invalid") from error
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("reconciliation subject persistence is unavailable") from error

    async def claim_next(
        self,
        *,
        worker_id: str,
        policy: ReconciliationConvergencePolicy,
    ) -> ReconciliationAttemptClaim | None:
        _require_claim_inputs(worker_id, policy)
        self._ensure_active()
        try:
            result = await self._connection.execute(next_reconciliation_claim_statement())
            row = result.mappings().one_or_none()
            if row is None:
                return None
            subject, contract, revision, convergence = decode_subject_row(
                dict(row),
                self._profile,
            )
            now = await self._database_time()
            acquired = acquire_reconciliation_claim(
                convergence,
                subject,
                contract,
                revision,
                worker_id=worker_id,
                now=now,
                policy=policy,
            )
            if acquired is None:
                return None
            changed = await self._connection.execute(
                update(reconciliation_subjects)
                .where(
                    reconciliation_subjects.c.subject_id == subject.subject_id,
                    reconciliation_subjects.c.claim_generation == convergence.claim_generation,
                    reconciliation_subjects.c.next_attempt_at <= func.statement_timestamp(),
                    or_(
                        reconciliation_subjects.c.lease_token.is_(None),
                        reconciliation_subjects.c.lease_expires_at <= func.statement_timestamp(),
                    ),
                    func.statement_timestamp() >= acquired.claim.claimed_at,
                    func.statement_timestamp() < acquired.claim.lease_expires_at,
                )
                .values(_convergence_values(acquired.state))
            )
            if changed.rowcount != 1:
                return None
            return acquired.claim
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CanonicalRowCodecError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("reconciliation claim row is invalid") from error
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("reconciliation claim persistence is unavailable") from error

    async def load_snapshot(
        self,
        claim: ReconciliationAttemptClaim,
    ) -> ReconciliationSnapshot | ReconciliationClaimLost:
        _require_claim(claim)
        self._ensure_active()
        try:
            loaded = await self._required_snapshot_state(claim.subject, for_update=False)
            _require_claim_contract(loaded[0], claim)
            if not reconciliation_claim_is_active(loaded[1], claim, await self._database_time()):
                return ReconciliationClaimLost(claim.subject.subject_id)
            return loaded[0]
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CanonicalRowCodecError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("reconciliation state row is invalid") from error
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("reconciliation snapshot load is unavailable") from error

    async def append_observation(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        observation: SignalObservation,
    ) -> ObservationPersistenceResult:
        _require_claim(claim)
        self._ensure_active()
        try:
            snapshot, convergence = await self._required_snapshot_state(
                claim.subject,
                for_update=True,
            )
            _require_claim_contract(snapshot, claim)
            outcome = apply_observation(snapshot, expected_revision, observation)
            if isinstance(outcome, ObservationAppended):
                if not reconciliation_claim_is_active(
                    convergence, claim, await self._database_time()
                ):
                    return ReconciliationClaimLost(claim.subject.subject_id)
                row = encode_observation_row(
                    claim.subject,
                    outcome.snapshot.revision,
                    observation,
                    self._profile,
                )
                changed = await self._connection.execute(
                    update(reconciliation_subjects)
                    .where(
                        *_claim_authority(claim),
                        reconciliation_subjects.c.revision == expected_revision,
                    )
                    .values(revision=outcome.snapshot.revision)
                )
                if changed.rowcount != 1:
                    return ReconciliationClaimLost(claim.subject.subject_id)
                await self._connection.execute(insert(reconciliation_observations).values(row))
            return outcome
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CanonicalRowCodecError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("reconciliation state row is invalid") from error
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable(
                "reconciliation observation persistence is unavailable"
            ) from error

    async def record_result(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
    ) -> ResultPersistenceResult:
        _require_claim(claim)
        if result.state == "pending":
            raise ValueError("pending reconciliation result cannot be persisted")
        self._ensure_active()
        try:
            snapshot, convergence = await self._required_snapshot_state(
                claim.subject,
                for_update=True,
            )
            _require_claim_contract(snapshot, claim)
            existing = await self._load_result(claim.subject, expected_revision)
            outcome = apply_result(snapshot, expected_revision, result, existing)
            if isinstance(outcome, ResultRecorded):
                released = release_terminal_claim(convergence, claim, await self._database_time())
                if isinstance(released, ReconciliationClaimLost):
                    return released
                if not await self._update_convergence(claim, released):
                    return ReconciliationClaimLost(claim.subject.subject_id)
                await self._connection.execute(
                    insert(reconciliation_results).values(
                        encode_result_row(
                            claim.subject,
                            expected_revision,
                            result,
                            self._profile,
                        )
                    )
                )
            return outcome
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CanonicalRowCodecError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("reconciliation state row is invalid") from error
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("reconciliation result persistence is unavailable") from error

    async def defer_claim(
        self,
        claim: ReconciliationAttemptClaim,
    ) -> ReconciliationConvergenceState | ReconciliationClaimLost:
        _require_claim(claim)
        self._ensure_active()
        try:
            snapshot, convergence = await self._required_snapshot_state(
                claim.subject,
                for_update=True,
            )
            _require_claim_contract(snapshot, claim)
            deferred = defer_reconciliation_claim(convergence, claim, await self._database_time())
            if isinstance(deferred, ReconciliationClaimLost):
                return deferred
            if not await self._update_convergence(claim, deferred):
                return ReconciliationClaimLost(claim.subject.subject_id)
            return deferred
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CanonicalRowCodecError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("reconciliation state row is invalid") from error
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("reconciliation defer persistence is unavailable") from error

    async def _update_convergence(
        self,
        claim: ReconciliationAttemptClaim,
        state: ReconciliationConvergenceState,
    ) -> bool:
        changed = await self._connection.execute(
            update(reconciliation_subjects)
            .where(*_claim_authority(claim))
            .values(_convergence_values(state))
        )
        return changed.rowcount == 1

    async def _database_time(self) -> datetime:
        value = await self._connection.scalar(select(func.clock_timestamp()))
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("database reconciliation time is invalid")
        return value

    async def _required_snapshot_state(
        self,
        subject: ReconciliationSubject,
        *,
        for_update: bool,
    ) -> ClaimedSnapshot:
        loaded = await self._load_snapshot_state(subject, for_update=for_update)
        if loaded is None:
            raise PersistenceInvariantViolation("reconciliation subject is not registered")
        return loaded

    async def _load_snapshot_state(
        self,
        subject: ReconciliationSubject,
        *,
        for_update: bool,
    ) -> ClaimedSnapshot | None:
        statement = select(reconciliation_subjects).where(
            reconciliation_subjects.c.subject_id == subject.subject_id
        )
        if for_update:
            statement = statement.with_for_update()
        else:
            statement = statement.with_for_update(read=True)
        result = await self._connection.execute(statement)
        subject_row = result.mappings().one_or_none()
        if subject_row is None:
            return None
        observations = await self._connection.execute(
            select(reconciliation_observations)
            .where(reconciliation_observations.c.subject_id == subject.subject_id)
            .order_by(reconciliation_observations.c.revision)
        )
        snapshot, convergence = snapshot_from_rows(
            dict(subject_row),
            tuple(dict(row) for row in observations.mappings()),
            self._profile,
        )
        if snapshot.subject != subject:
            raise PersistenceInvariantViolation(
                "stored reconciliation subject differs from requested subject"
            )
        return snapshot, convergence

    async def _load_result(
        self,
        subject: ReconciliationSubject,
        revision: int,
    ) -> ReconciliationResult | None:
        result = await self._connection.execute(
            select(reconciliation_results).where(
                reconciliation_results.c.subject_id == subject.subject_id,
                reconciliation_results.c.revision == revision,
            )
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        stored_revision, stored_result = decode_result_row(dict(row), subject, self._profile)
        if stored_revision != revision:
            raise PersistenceInvariantViolation("stored reconciliation result revision is invalid")
        return stored_result


def _convergence_values(state: ReconciliationConvergenceState) -> dict[str, object]:
    return {
        "next_attempt_at": state.next_attempt_at,
        "attempt_count": state.attempt_count,
        "backoff_seconds": state.backoff_seconds,
        "claim_generation": state.claim_generation,
        "lease_token": state.lease_token,
        "lease_acquired_at": state.lease_acquired_at,
        "lease_expires_at": state.lease_expires_at,
    }


def _require_claim_inputs(
    worker_id: object,
    policy: object,
) -> None:
    if (
        type(worker_id) is not str
        or len(worker_id) != 64
        or any(character not in "0123456789abcdef" for character in worker_id)
    ):
        raise ValueError("reconciliation worker id must be a lowercase SHA-256 digest")
    if type(policy) is not ReconciliationConvergencePolicy:
        raise TypeError("reconciliation convergence policy must be exact")


def _require_claim(claim: object) -> None:
    if type(claim) is not ReconciliationAttemptClaim:
        raise TypeError("reconciliation operation requires an exact claim")


def _claim_authority(claim: ReconciliationAttemptClaim) -> tuple[ColumnElement[bool], ...]:
    now = func.statement_timestamp()
    return (
        reconciliation_subjects.c.subject_id == claim.subject.subject_id,
        reconciliation_subjects.c.claim_generation == claim.generation,
        reconciliation_subjects.c.lease_token == claim.token,
        reconciliation_subjects.c.lease_acquired_at <= now,
        reconciliation_subjects.c.lease_expires_at > now,
    )


def _require_claim_contract(
    snapshot: ReconciliationSnapshot,
    claim: ReconciliationAttemptClaim,
) -> None:
    if snapshot.subject != claim.subject or snapshot.contract != claim.contract:
        raise PersistenceInvariantViolation("reconciliation claim differs from retained state")
