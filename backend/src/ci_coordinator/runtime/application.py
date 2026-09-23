"""Compose only runtime surfaces whose dependencies are actually owned."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    ObservabilityRouteDependencies,
)
from ci_coordinator.observability import (
    ReadinessStatus,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
)
from ci_coordinator.runtime.composition import (
    ProductionAdmissionConfigurationError,
    RuntimeDependencyConfigurationError,
    compose_enforcing_dependencies,
    compose_non_enforcing_dependencies,
)
from ci_coordinator.runtime.event_logging import runtime_event_logger
from ci_coordinator.runtime_settings import (
    EnforcingRuntimeSettings,
    NonEnforcingRuntimeSettings,
    RuntimeSettings,
    RuntimeSettingsProjection,
    load_bundled_caller_inventory,
    load_bundled_entrypoint_disposition,
    redacted_settings_projection,
)


@dataclass(frozen=True, slots=True)
class RuntimeApplication:
    """A running ASGI candidate and its non-secret process projection."""

    app: FastAPI
    settings: RuntimeSettingsProjection


@dataclass(frozen=True, slots=True)
class RuntimeCompositionRejection:
    """A redacted, fail-closed explanation for an unavailable runtime mode."""

    code: Literal[
        "runtime_caller_inventory_unavailable",
        "runtime_entrypoint_disposition_unavailable",
        "runtime_dependencies_unavailable",
        "production_admission_unavailable",
    ]
    unavailable_dependencies: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.unavailable_dependencies:
            raise ValueError("runtime rejection requires unavailable dependencies")
        if tuple(sorted(set(self.unavailable_dependencies))) != self.unavailable_dependencies:
            raise ValueError("runtime rejection dependencies must be unique and ordered")


def compose_runtime_application(
    settings: RuntimeSettings,
) -> RuntimeApplication | RuntimeCompositionRejection:
    """Return a runnable application only when its promised behavior is realizable.

    Disabled mode owns no external effect. Non-enforcing mode is admitted only
    after its concrete durable and provider-backed dependency graph is built.
    """
    entrypoint_evidence_rejection = _entrypoint_evidence_rejection()
    if entrypoint_evidence_rejection is not None:
        return entrypoint_evidence_rejection
    if isinstance(settings, NonEnforcingRuntimeSettings):
        try:
            app, _resources = compose_non_enforcing_dependencies(settings)
        except RuntimeDependencyConfigurationError:
            return RuntimeCompositionRejection(
                "runtime_dependencies_unavailable",
                ("runtime_configuration",),
            )
        return RuntimeApplication(
            app=app,
            settings=redacted_settings_projection(settings),
        )
    if isinstance(settings, EnforcingRuntimeSettings):
        try:
            app, _resources = compose_enforcing_dependencies(settings)
        except ProductionAdmissionConfigurationError:
            return RuntimeCompositionRejection(
                "production_admission_unavailable",
                ("production_admission",),
            )
        except RuntimeDependencyConfigurationError:
            return RuntimeCompositionRejection(
                "runtime_dependencies_unavailable",
                ("runtime_configuration",),
            )
        return RuntimeApplication(
            app=app,
            settings=redacted_settings_projection(settings),
        )
    runtime_metrics = RuntimeMetrics()
    structured_logger = runtime_event_logger()
    diagnostics = RuntimeDiagnosticObserver(structured_logger)
    return RuntimeApplication(
        app=create_app(
            HttpRouteDependencies(
                observability=ObservabilityRouteDependencies(
                    readiness=_disabled_readiness,
                    metrics=runtime_metrics,
                    request_logger=structured_logger,
                    diagnostics=diagnostics,
                )
            ),
            include_operator_ui=False,
        ),
        settings=redacted_settings_projection(settings),
    )


async def _disabled_readiness() -> ReadinessStatus:
    return ReadinessStatus(ready=False, unavailable_dependencies=("runtime_mode_disabled",))


def _entrypoint_evidence_rejection() -> RuntimeCompositionRejection | None:
    try:
        caller_inventory = load_bundled_caller_inventory()
    except (ModuleNotFoundError, OSError, ValueError):
        return RuntimeCompositionRejection(
            "runtime_caller_inventory_unavailable",
            ("runtime_caller_inventory",),
        )
    try:
        load_bundled_entrypoint_disposition(caller_inventory)
    except (ModuleNotFoundError, OSError, ValueError):
        return RuntimeCompositionRejection(
            "runtime_entrypoint_disposition_unavailable",
            ("runtime_entrypoint_disposition",),
        )
    return None
