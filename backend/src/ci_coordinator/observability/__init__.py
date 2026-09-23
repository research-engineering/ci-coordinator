"""Read-only correctness and availability projections."""

from ci_coordinator.observability.background_health import (
    BackgroundHealth,
    BackgroundHealthState,
)
from ci_coordinator.observability.diagnostics import (
    RuntimeDiagnosticObserver,
    RuntimeDiagnosticStage,
)
from ci_coordinator.observability.health import HealthStatus, health
from ci_coordinator.observability.logging import (
    StructuredEventLogger,
    default_structured_event_logger,
    redacted_log_event,
)
from ci_coordinator.observability.metrics import MetricsRegistry, PrometheusSnapshot
from ci_coordinator.observability.readiness import (
    DependencyReadiness,
    ReadinessStatus,
    RuntimeReadinessUseCase,
    assess_readiness,
)
from ci_coordinator.observability.request_observation import (
    HttpRequestObservationMiddleware,
    PlanOperationDiagnostic,
    bind_plan_operation_diagnostic,
)
from ci_coordinator.observability.runtime_metrics import (
    MaintenanceOperationName,
    MaintenanceOperationResult,
    PlanningStage,
    PlanningUnavailabilityReason,
    RuntimeMetrics,
)

__all__ = [
    "BackgroundHealth",
    "BackgroundHealthState",
    "DependencyReadiness",
    "HealthStatus",
    "HttpRequestObservationMiddleware",
    "MaintenanceOperationName",
    "MaintenanceOperationResult",
    "MetricsRegistry",
    "PlanOperationDiagnostic",
    "PlanningStage",
    "PlanningUnavailabilityReason",
    "PrometheusSnapshot",
    "ReadinessStatus",
    "RuntimeDiagnosticObserver",
    "RuntimeDiagnosticStage",
    "RuntimeMetrics",
    "RuntimeReadinessUseCase",
    "StructuredEventLogger",
    "assess_readiness",
    "bind_plan_operation_diagnostic",
    "default_structured_event_logger",
    "health",
    "redacted_log_event",
]
