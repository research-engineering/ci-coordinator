from __future__ import annotations

from typing import Final, Literal, TypeGuard

type AuditSubjectType = Literal[
    "webhook-delivery",
    "observation",
    "policy-decision",
    "reconciliation-state",
    "coordinator-check",
    "dynamic-ci-plan",
    "release-evidence",
    "policy-drift",
]


AUDIT_SUBJECT_TYPES: Final[tuple[AuditSubjectType, ...]] = (
    "webhook-delivery",
    "observation",
    "policy-decision",
    "reconciliation-state",
    "coordinator-check",
    "dynamic-ci-plan",
    "release-evidence",
    "policy-drift",
)

_AUDIT_SUBJECT_TYPE_SET: Final = frozenset(AUDIT_SUBJECT_TYPES)


def is_audit_subject_type(value: object) -> TypeGuard[AuditSubjectType]:
    return type(value) is str and value in _AUDIT_SUBJECT_TYPE_SET
