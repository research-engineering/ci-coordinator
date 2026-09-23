"""Atomic reconciliation state and pair-owned audit operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from ci_coordinator.app.planning_evidence import (
    PLANNING_EVIDENCE_AUDIT_EVENT_TYPE,
    prepare_planning_evidence_audit,
)
from ci_coordinator.app.reconciliation_audit import reconciliation_terminal_audit_event
from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditAppendDuplicate,
    PreparedAuditEvent,
    prepare_audit_event,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.reconciliation_state_repository import (
    _PostgresReconciliationRepository,
)
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.reconciliation import (
    ReconciliationAttemptClaim,
    ReconciliationClaimLost,
    ReconciliationContract,
    ReconciliationConvergenceState,
    ReconciliationResult,
    ReconciliationSubject,
    ResultDuplicate,
    ResultRecord,
    ResultRecorded,
    SubjectRegistered,
    SubjectRegistration,
    SubjectRegistrationDuplicate,
)


class _PostgresReconciliationPairRepository:
    """Own state transitions whose audit evidence must commit atomically."""

    def __init__(
        self,
        state: _PostgresReconciliationRepository,
        audit_events: _PostgresAuditEventRepository,
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._state = state
        self._audit_events = audit_events
        self._mark_rollback_required = mark_rollback_required

    async def register_subject(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
        convergence: ReconciliationConvergenceState,
        *,
        occurred_at: datetime,
        execution_origin: Literal["legacy", "full_ci", "selected"] = "legacy",
        production_guard: ProductionIssuanceGuard | None = None,
    ) -> SubjectRegistration:
        _require_occurred_at(occurred_at)
        result = await self._state.register_subject(
            subject,
            contract,
            convergence,
            execution_origin=execution_origin,
            production_guard=production_guard,
        )
        if isinstance(result, SubjectRegistered | SubjectRegistrationDuplicate):
            await self._append_planning_evidence(subject, contract, occurred_at)
        return result

    async def record_result(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
        *,
        occurred_at: datetime,
    ) -> ResultRecord | ReconciliationClaimLost:
        _require_occurred_at(occurred_at)
        recorded = await self._state.record_result(
            claim,
            expected_revision,
            result,
        )
        if isinstance(recorded, ReconciliationClaimLost):
            return recorded
        if not await self._append_terminal_result(claim.subject, recorded, occurred_at):
            self._mark_rollback_required()
            return ReconciliationClaimLost(claim.subject.subject_id)
        return recorded

    async def _append_planning_evidence(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
        occurred_at: datetime,
    ) -> None:
        if contract.planning_evidence is None:
            return
        attempted = prepare_planning_evidence_audit(
            subject,
            contract,
            occurred_at=occurred_at,
        )
        existing = await self._audit_events._find_pair_owned(
            attempted.idempotency_key,
            PLANNING_EVIDENCE_AUDIT_EVENT_TYPE,
            _scope(subject),
        )
        if existing is not None:
            attempted = prepare_planning_evidence_audit(
                subject,
                contract,
                occurred_at=existing.created_at,
            )
        await self._require_append(
            attempted,
            _scope(subject),
            "planning evidence conflicts with its reconciliation subject",
        )

    async def _append_terminal_result(
        self,
        subject: ReconciliationSubject,
        recorded: ResultRecord,
        occurred_at: datetime,
    ) -> bool:
        if isinstance(recorded, ResultRecorded):
            retained = recorded.result
            revision = recorded.revision
        elif isinstance(recorded, ResultDuplicate):
            retained = recorded.existing
            revision = recorded.revision
        else:
            return True
        if retained.state == "pending":
            return True
        event = reconciliation_terminal_audit_event(
            subject,
            retained,
            revision=revision,
            occurred_at=occurred_at,
        )
        existing = await self._audit_events._find_pair_owned(
            event.idempotency_key,
            event.event_type,
            _scope(subject),
        )
        if isinstance(recorded, ResultDuplicate) and existing is None:
            return False
        if existing is not None:
            event = reconciliation_terminal_audit_event(
                subject,
                retained,
                revision=revision,
                occurred_at=existing.created_at,
            )
        appended = await self._require_append(
            prepare_audit_event(event),
            _scope(subject),
            "reconciliation result conflicts with its pair-owned audit evidence",
        )
        return not isinstance(recorded, ResultDuplicate) or isinstance(
            appended, AuditAppendDuplicate
        )

    async def _require_append(
        self,
        event: PreparedAuditEvent,
        scope: RepositoryScope,
        conflict_message: str,
    ) -> AuditAppendAppended | AuditAppendDuplicate:
        audit = await self._audit_events._append_pair_owned(event, scope)
        if not isinstance(audit, AuditAppendAppended | AuditAppendDuplicate):
            self._mark_rollback_required()
            raise PersistenceInvariantViolation(conflict_message)
        return audit


def _require_occurred_at(value: datetime) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("reconciliation audit time must be timezone-aware")


def _scope(subject: ReconciliationSubject) -> RepositoryScope:
    return RepositoryScope(subject.installation_id, subject.repository_id)
