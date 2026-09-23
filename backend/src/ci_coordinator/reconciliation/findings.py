from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type ReconciliationState = Literal["pending", "success", "failure", "conflict"]
type ReconciliationFindingKind = Literal[
    "missing_expected_contract",
    "missing_expected_signal",
    "incomplete_expected_signal",
    "failed_expected_signal",
    "skipped_without_proof",
    "neutral_without_policy",
    "omitted_execution",
    "contradictory_observation",
    "reconciliation_timed_out",
    "reconciliation_attempts_exhausted",
    "ambiguous_provider_signal",
]
_FINDING_KINDS = frozenset(
    {
        "missing_expected_contract",
        "missing_expected_signal",
        "incomplete_expected_signal",
        "failed_expected_signal",
        "skipped_without_proof",
        "neutral_without_policy",
        "omitted_execution",
        "contradictory_observation",
        "reconciliation_timed_out",
        "reconciliation_attempts_exhausted",
        "ambiguous_provider_signal",
    }
)
_RECONCILIATION_STATES = frozenset({"pending", "success", "failure", "conflict"})


@dataclass(frozen=True, slots=True)
class ReconciliationFinding:
    kind: ReconciliationFindingKind
    signal_id: str | None
    observation_ids: tuple[str, ...]
    message: str

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in _FINDING_KINDS:
            raise ValueError("reconciliation finding kind is unsupported")
        if self.signal_id is not None and (type(self.signal_id) is not str or not self.signal_id):
            raise ValueError("signal_id must be a non-empty string when present")
        if type(self.observation_ids) is not tuple:
            raise TypeError("observation_ids must be a tuple")
        if any(
            type(observation_id) is not str or not observation_id
            for observation_id in self.observation_ids
        ):
            raise ValueError("observation_ids must contain non-empty strings")
        if type(self.message) is not str or not self.message:
            raise ValueError("message must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    subject_id: str
    state: ReconciliationState
    findings: tuple[ReconciliationFinding, ...]

    def __post_init__(self) -> None:
        if type(self.subject_id) is not str or not self.subject_id:
            raise ValueError("subject_id must be a non-empty string")
        if type(self.state) is not str or self.state not in _RECONCILIATION_STATES:
            raise ValueError("reconciliation result state is unsupported")
        if type(self.findings) is not tuple:
            raise TypeError("findings must be a tuple")
        if any(type(finding) is not ReconciliationFinding for finding in self.findings):
            raise TypeError("findings must contain ReconciliationFinding values")

    @property
    def is_success(self) -> bool:
        return self.state == "success"
