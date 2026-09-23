"""Single-use pair-owned audit capability for governance-baseline approval."""

from __future__ import annotations

from datetime import datetime
from typing import Final, NoReturn

from ci_coordinator.audit_replay import AuditEventInput, PreparedAuditEvent, prepare_audit_event
from ci_coordinator.governance_baseline.model import (
    GovernanceBaselineDraft,
    baseline_id_for,
    canonical_instant,
)

GOVERNANCE_BASELINE_APPROVED_EVENT_TYPE: Final = "governance-baseline-approved/v1"
_AUDIT_SCHEMA: Final = "governance-baseline-approval-audit/v1"
_PREPARED_TOKEN = object()


class PreparedGovernanceBaseline:
    """Non-forgeable baseline and audit pair consumed by persistence."""

    __approved_at: datetime
    __audit_event: PreparedAuditEvent
    __consumed: bool
    __draft: GovernanceBaselineDraft

    __slots__ = ("__approved_at", "__audit_event", "__consumed", "__draft")

    def __init__(
        self,
        token: object,
        *,
        draft: GovernanceBaselineDraft,
        approved_at: datetime,
        audit_event: PreparedAuditEvent,
    ) -> None:
        if token is not _PREPARED_TOKEN:
            raise TypeError("PreparedGovernanceBaseline cannot be constructed directly")
        if (
            type(draft) is not GovernanceBaselineDraft
            or type(approved_at) is not datetime
            or type(audit_event) is not PreparedAuditEvent
        ):
            raise TypeError("prepared governance baseline pair is invalid")
        object.__setattr__(self, "_PreparedGovernanceBaseline__draft", draft)
        object.__setattr__(self, "_PreparedGovernanceBaseline__approved_at", approved_at)
        object.__setattr__(self, "_PreparedGovernanceBaseline__audit_event", audit_event)
        object.__setattr__(self, "_PreparedGovernanceBaseline__consumed", False)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("PreparedGovernanceBaseline cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("PreparedGovernanceBaseline is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("PreparedGovernanceBaseline cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("PreparedGovernanceBaseline cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("PreparedGovernanceBaseline cannot be serialized")

    @property
    def draft(self) -> GovernanceBaselineDraft:
        return self.__draft

    @property
    def approved_at(self) -> datetime:
        return self.__approved_at

    @property
    def audit_input_hash(self) -> str:
        return self.__audit_event.input_hash

    def _take_audit_event(self) -> PreparedAuditEvent:
        if self.__consumed:
            raise ValueError("prepared governance baseline audit capability was already consumed")
        object.__setattr__(self, "_PreparedGovernanceBaseline__consumed", True)
        return self.__audit_event


def prepare_governance_baseline(
    draft: GovernanceBaselineDraft,
    *,
    approved_at: datetime,
) -> PreparedGovernanceBaseline:
    if type(draft) is not GovernanceBaselineDraft:
        raise TypeError("governance baseline preparation requires an exact draft")
    baseline_id = baseline_id_for(draft, approved_at=approved_at)
    command = draft.command
    scope = command.scope
    predecessor = command.expected_active
    event = prepare_audit_event(
        AuditEventInput(
            idempotency_key=(
                f"governance-baseline:{scope.installation_id}:"
                f"{scope.repository_id}:{command.operation_id}"
            ),
            installation_id=scope.installation_id,
            repository_id=scope.repository_id,
            subject_type="policy-decision",
            subject_id=baseline_id,
            event_type=GOVERNANCE_BASELINE_APPROVED_EVENT_TYPE,
            created_at=canonical_instant(approved_at),
            actor=command.actor,
            payload={
                "schemaVersion": _AUDIT_SCHEMA,
                "operationId": command.operation_id,
                "baselineId": baseline_id,
                "version": draft.version,
                "stateDigest": draft.state_digest,
                "observedAt": canonical_instant(draft.observed_at),
                "supersedesBaselineId": (None if predecessor is None else predecessor.baseline_id),
                "supersedesVersion": None if predecessor is None else predecessor.version,
                "reason": command.reason,
            },
        )
    )
    return PreparedGovernanceBaseline(
        _PREPARED_TOKEN,
        draft=draft,
        approved_at=approved_at,
        audit_event=event,
    )


def is_pair_owned_governance_baseline_event_type(value: object) -> bool:
    return value == GOVERNANCE_BASELINE_APPROVED_EVENT_TYPE
