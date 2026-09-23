"""Private bounded diagnostics that never alter authoritative outcomes."""

from __future__ import annotations

from typing import Final, Literal

from ci_coordinator.observability.logging import StructuredEventLogger

type RuntimeDiagnosticStage = Literal[
    "activity_cleanup",
    "active_config_epoch",
    "browser_oauth_callback",
    "candidate_context",
    "capacity_inputs",
    "capacity_projection",
    "ci_economics_collection",
    "ci_economics_expiry",
    "ci_economics_observation_cleanup",
    "ci_economics_tombstone_purge",
    "ci_observation_discovery",
    "ci_observation_gap_cleanup",
    "ci_history_collection",
    "ci_history_delivery",
    "ci_history_detail_cleanup",
    "http_request",
    "planning_override",
    "production_authorization",
    "production_preauthorization",
    "reconciliation_registration",
    "reconciliation_round",
    "readiness_database",
    "readiness_dependency",
    "runner_snapshot",
]

_STAGES: Final = frozenset(
    {
        "activity_cleanup",
        "active_config_epoch",
        "browser_oauth_callback",
        "candidate_context",
        "capacity_inputs",
        "capacity_projection",
        "ci_economics_collection",
        "ci_economics_expiry",
        "ci_economics_observation_cleanup",
        "ci_economics_tombstone_purge",
        "ci_observation_discovery",
        "ci_observation_gap_cleanup",
        "ci_history_collection",
        "ci_history_delivery",
        "ci_history_detail_cleanup",
        "http_request",
        "planning_override",
        "production_authorization",
        "production_preauthorization",
        "reconciliation_registration",
        "reconciliation_round",
        "readiness_database",
        "readiness_dependency",
        "runner_snapshot",
    }
)


class RuntimeDiagnosticObserver:
    """Project exception classes without messages, arguments, or tracebacks."""

    def __init__(self, logger: StructuredEventLogger) -> None:
        if type(logger) is not StructuredEventLogger:
            raise TypeError("runtime diagnostics require an exact structured logger")
        self._logger = logger

    def unexpected_failure(
        self,
        stage: RuntimeDiagnosticStage,
        error: BaseException,
        *,
        correlation_id: str | None = None,
    ) -> None:
        admitted_stage = stage if stage in _STAGES else "diagnostic_contract"
        self._logger.emit(
            {
                "event": "unexpected_failure",
                "exceptionType": type(error).__name__,
                "reason": "unexpected_exception",
                "stage": admitted_stage,
            },
            correlation_id=correlation_id,
        )
