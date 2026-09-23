from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.kernel import is_safe_json_integer

type SignalConclusion = Literal[
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "skipped",
    "neutral",
    "unknown",
]
type SignalStatus = Literal["in_progress", "completed"]
_SIGNAL_CONCLUSIONS = frozenset(
    {"success", "failure", "cancelled", "timed_out", "skipped", "neutral", "unknown"}
)
_SIGNAL_STATUSES = frozenset({"in_progress", "completed"})


@dataclass(frozen=True, slots=True)
class OmittedSignal:
    signal_id: str
    name: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.signal_id, "signal_id")
        _require_non_empty_string(self.name, "name")


@dataclass(frozen=True, slots=True)
class SignalObservation:
    observation_id: str
    subject_id: str
    signal_id: str
    workflow_run_id: int
    run_attempt: int
    provider_job_id: int
    status: SignalStatus
    conclusion: SignalConclusion | None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.observation_id, "observation_id")
        _require_non_empty_string(self.subject_id, "subject_id")
        _require_non_empty_string(self.signal_id, "signal_id")
        for value, field_name in (
            (self.workflow_run_id, "workflow_run_id"),
            (self.run_attempt, "run_attempt"),
            (self.provider_job_id, "provider_job_id"),
        ):
            if type(value) is not int or value < 1 or not is_safe_json_integer(value):
                raise ValueError(f"{field_name} must be a positive JSON safe integer")
        if type(self.status) is not str or self.status not in _SIGNAL_STATUSES:
            raise ValueError("signal observation status is unsupported")
        if self.conclusion is not None and (
            type(self.conclusion) is not str or self.conclusion not in _SIGNAL_CONCLUSIONS
        ):
            raise ValueError("signal observation conclusion is unsupported")
        if self.status == "in_progress" and self.conclusion is not None:
            raise ValueError("in_progress observations must not have a conclusion")
        if self.status == "completed" and self.conclusion is None:
            raise ValueError("completed observations must have a conclusion")


def _require_non_empty_string(value: object, field_name: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
