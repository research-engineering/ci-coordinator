"""PostgreSQL signed-plan idempotency repository within one admitted transaction."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.dml import ReturningInsert

from ci_coordinator.audit_replay import AuditAppendAppended, AuditAppendDuplicate
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.config_epoch_repository import (
    _PostgresConfigEpochRepository,
)
from ci_coordinator.persistence.errors import PersistenceError, PersistenceInvariantViolation
from ci_coordinator.persistence.issued_plan_audit import prepare_issued_plan_audit
from ci_coordinator.persistence.issued_plan_codec import (
    IssuedPlanCodecError,
    decode_record,
    encode_envelope,
)
from ci_coordinator.persistence.operator_override_repository import (
    PostgresOperatorOverrideRepository,
)
from ci_coordinator.persistence.production_issuance_guard import (
    production_guard_rejection,
    production_guard_time_predicates,
)
from ci_coordinator.persistence.repository_scope_lock import lock_repository_scope
from ci_coordinator.persistence.runtime_state_profile import (
    RuntimeIngressIssuanceStateProfile,
)
from ci_coordinator.persistence.schema import (
    issued_plan_envelopes,
    reconciliation_subjects,
)
from ci_coordinator.plan_issuance import (
    IssuanceGuardRejected,
    IssuanceSaveResult,
    IssuanceStoreUnavailable,
    IssuedPlanRecord,
    production_guard_binds_record,
)
from ci_coordinator.production_admission import ProductionIssuanceGuard

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RECORD_ID = re.compile(r"^issued_plan_[0-9a-f]{32}$")


class _PostgresIssuanceRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        audit_events: _PostgresAuditEventRepository,
        config_epochs: _PostgresConfigEpochRepository,
        operator_overrides: PostgresOperatorOverrideRepository,
        compatibility_profile: CompatibilityProfile,
        profile: RuntimeIngressIssuanceStateProfile,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._audit_events = audit_events
        self._config_epochs = config_epochs
        self._operator_overrides = operator_overrides
        self._compatibility_profile = compatibility_profile
        self._profile = profile
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def save(
        self,
        attempted: IssuedPlanRecord,
        guard: ProductionIssuanceGuard | None = None,
    ) -> IssuanceSaveResult:
        self._ensure_active()
        try:
            row = _row_for(attempted, self._profile)
            selected = attempted.envelope.payload.verified_plan_id is not None
            if selected != (guard is not None) or (
                guard is not None and not production_guard_binds_record(guard, attempted)
            ):
                return IssuanceGuardRejected("production_binding_invalid")
            if guard is not None:
                await lock_repository_scope(self._connection, guard.scope)
                rejection = await self._validate_guard(guard, attempted.envelope.expires_at)
                if rejection is not None:
                    return rejection
            inserted = await self._connection.scalar(
                _insert_record_statement(
                    row,
                    guard=guard,
                    plan_expires_at=attempted.envelope.expires_at,
                )
            )
            if inserted == attempted.idempotency_key:
                retained = attempted
                existing_record = None
            else:
                if guard is not None:
                    temporal_rejection = await self._temporal_rejection(
                        guard,
                        attempted.envelope.expires_at,
                    )
                    if temporal_rejection is not None:
                        return temporal_rejection
                stored = await self._connection.execute(
                    select(issued_plan_envelopes).where(
                        issued_plan_envelopes.c.idempotency_key == attempted.idempotency_key
                    )
                )
                existing = stored.mappings().one_or_none()
                if existing is None:
                    raise IssuedPlanCodecError("issued-plan conflict row is unavailable")
                retained = decode_record(dict(existing), self._profile)
                existing_record = retained
            repository = retained.envelope.payload.repository
            audit = await self._audit_events._append_pair_owned(
                prepare_issued_plan_audit(retained),
                RepositoryScope(repository.installation_id, repository.repository_id),
            )
            if not isinstance(audit, AuditAppendAppended | AuditAppendDuplicate):
                raise PersistenceInvariantViolation(
                    "issued-plan state conflicts with its pair-owned audit evidence"
                )
            if guard is not None:
                rejection = await self._temporal_rejection(guard, attempted.envelope.expires_at)
                if rejection is not None:
                    self._mark_rollback_required()
                    return rejection
            return existing_record
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (IssuedPlanCodecError, PersistenceError, SQLAlchemyError) as error:
            self._mark_rollback_required()
            raise IssuanceStoreUnavailable("issued plan persistence is unavailable") from error

    async def _validate_guard(
        self,
        guard: ProductionIssuanceGuard,
        plan_expires_at: datetime,
    ) -> IssuanceGuardRejected | None:
        now = await self._database_now()
        temporal_rejection = _temporal_rejection(now, plan_expires_at, guard.not_after)
        if temporal_rejection is not None:
            return temporal_rejection
        if plan_expires_at > guard.not_after:
            raise PersistenceInvariantViolation("issued plan outlives its production authority")
        rejection = await production_guard_rejection(
            self._connection,
            guard,
            now=now,
            config_epochs=self._config_epochs,
            operator_overrides=self._operator_overrides,
            compatibility_profile=self._compatibility_profile,
        )
        if rejection is not None:
            return rejection
        current = guard.current_evidence
        if current is None:
            return IssuanceGuardRejected("current_evidence_invalid")
        registered_subject = await self._connection.scalar(
            select(reconciliation_subjects.c.subject_id).where(
                reconciliation_subjects.c.subject_id == guard.reconciliation_subject_id,
                reconciliation_subjects.c.installation_id == guard.scope.installation_id,
                reconciliation_subjects.c.repository_id == guard.scope.repository_id,
                reconciliation_subjects.c.execution_origin == "selected",
                reconciliation_subjects.c.production_generation
                == current.scope_grant.relation.generation,
                reconciliation_subjects.c.production_authority_id == guard.authority_id,
            )
        )
        if registered_subject != guard.reconciliation_subject_id:
            return IssuanceGuardRejected("production_binding_invalid")
        return None

    async def _temporal_rejection(
        self,
        guard: ProductionIssuanceGuard,
        plan_expires_at: datetime,
    ) -> IssuanceGuardRejected | None:
        now = await self._database_now()
        if guard.current_evidence is None or not guard.current_evidence.is_current_at(now):
            return IssuanceGuardRejected("current_evidence_invalid")
        return _temporal_rejection(
            now,
            plan_expires_at,
            guard.not_after,
        )

    async def _database_now(self) -> datetime:
        now = await self._connection.scalar(select(func.clock_timestamp()))
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
            raise PersistenceInvariantViolation("database clock did not return an aware instant")
        return now


def _row_for(
    record: IssuedPlanRecord,
    profile: RuntimeIngressIssuanceStateProfile,
) -> dict[str, object]:
    if type(record) is not IssuedPlanRecord:
        raise IssuedPlanCodecError("issued-plan record must be exact")
    _bounded(record.idempotency_key, profile.issuance_idempotency_key_utf8_bytes, "idempotency key")
    _bounded(record.record_id, profile.issued_plan_record_id_utf8_bytes, "record id")
    if _RECORD_ID.fullmatch(record.record_id) is None:
        raise IssuedPlanCodecError("issued-plan record id has an unsupported shape")
    if _DIGEST.fullmatch(record.request_hash) is None:
        raise IssuedPlanCodecError("issued-plan request hash is invalid")
    return {
        "idempotency_key": record.idempotency_key,
        "record_id": record.record_id,
        "request_hash": record.request_hash,
        "installation_id": record.envelope.payload.repository.installation_id,
        "repository_id": record.envelope.payload.repository.repository_id,
        "production_admission_authority_id": (
            record.envelope.payload.production_admission_receipt_id
        ),
        "issued_at": record.envelope.issued_at,
        "expires_at": record.envelope.expires_at,
        "envelope_canonical_json": encode_envelope(record.envelope, profile),
    }


def _insert_record_statement(
    row: dict[str, object],
    *,
    guard: ProductionIssuanceGuard | None,
    plan_expires_at: datetime,
) -> ReturningInsert[Any]:
    statement = postgres_insert(issued_plan_envelopes)
    if guard is None:
        statement = statement.values(row)
    else:
        evidence = guard.current_evidence
        if evidence is None:
            raise IssuedPlanCodecError("selected issuance requires current production evidence")
        columns = tuple(row)
        values = tuple(
            literal(row[name], type_=issued_plan_envelopes.c[name].type).label(name)
            for name in columns
        )
        statement = statement.from_select(
            columns,
            select(*values).where(
                func.clock_timestamp() < plan_expires_at,
                *production_guard_time_predicates(guard),
            ),
        )
    return statement.on_conflict_do_nothing(
        index_elements=[issued_plan_envelopes.c.idempotency_key]
    ).returning(issued_plan_envelopes.c.idempotency_key)


def _temporal_rejection(
    now: datetime,
    plan_expires_at: datetime,
    authority_expires_at: datetime,
) -> IssuanceGuardRejected | None:
    if now >= plan_expires_at:
        return IssuanceGuardRejected("plan_expired")
    if now >= authority_expires_at:
        return IssuanceGuardRejected("authority_expired")
    return None


def _bounded(value: object, maximum: int, context: str) -> None:
    if type(value) is not str or not value or len(value.encode("utf-8")) > maximum:
        raise IssuedPlanCodecError(f"issued-plan {context} violates the admitted byte bound")
