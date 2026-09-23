from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .evidence import WorkflowAuthorityEvidence

type WorkflowAuthorityFailureReason = Literal[
    "invalid_revision",
    "malformed_provider_response",
    "not_found",
    "provider_binding_mismatch",
    "rate_limited",
    "source_limit_exceeded",
    "source_tree_missing",
    "unavailable",
    "unsupported_object",
]


@dataclass(frozen=True, slots=True)
class WorkflowAuthorityUnavailable:
    reason: WorkflowAuthorityFailureReason

    def __post_init__(self) -> None:
        if self.reason not in {
            "invalid_revision",
            "malformed_provider_response",
            "not_found",
            "provider_binding_mismatch",
            "rate_limited",
            "source_limit_exceeded",
            "source_tree_missing",
            "unavailable",
            "unsupported_object",
        }:
            raise ValueError("workflow authority failure reason is not admitted")


type WorkflowAuthorityReadOutcome = WorkflowAuthorityEvidence | WorkflowAuthorityUnavailable
