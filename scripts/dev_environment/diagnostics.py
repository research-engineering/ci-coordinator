from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

OutputFormat = Literal["human", "json"]


class Reason(StrEnum):
    INVALID_ARGUMENT = "invalid_argument"
    UNSUPPORTED_TOOL = "unsupported_tool"
    DEPENDENCIES_MISSING = "dependencies_missing"
    MISSING_STATE = "missing_state"
    INVALID_STATE = "invalid_state"
    FOREIGN_STATE = "foreign_state"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    PARTIAL_OBSERVATION = "partial_observation"
    STALE_OBSERVATION = "stale_observation"
    OPERATION_BUSY = "operation_busy"
    PREPARATION_IN_PROGRESS = "preparation_in_progress"
    ENVIRONMENT_STALE = "environment_stale"
    WATCH_BLOCKED = "watch_blocked"
    SERVICE_UNAVAILABLE = "service_unavailable"


_REMEDIES: dict[Reason, tuple[str, str, bool]] = {
    Reason.INVALID_ARGUMENT: (
        "command_help",
        "Read the command's --help and correct its arguments.",
        False,
    ),
    Reason.UNSUPPORTED_TOOL: (
        "supported_tools",
        "Install the tool versions declared by this repository.",
        False,
    ),
    Reason.DEPENDENCIES_MISSING: (
        "prepare_backend",
        "Run mise run install:backend to prepare locked Python dependencies.",
        False,
    ),
    Reason.MISSING_STATE: (
        "prepare_instance",
        "Run mise run dev:prepare before using this instance.",
        False,
    ),
    Reason.INVALID_STATE: (
        "inspect_state",
        "Run mise run dev:doctor; preserve state while resolving its admission failure.",
        False,
    ),
    Reason.FOREIGN_STATE: (
        "inspect_ownership",
        "Resolve the foreign identity without deleting another instance's resources.",
        False,
    ),
    Reason.PROVIDER_UNAVAILABLE: (
        "start_docker",
        "Start the selected Docker daemon and check its context, then retry.",
        True,
    ),
    Reason.PROVIDER_TIMEOUT: (
        "inspect_provider",
        "Inspect Docker availability and retry the bounded operation.",
        True,
    ),
    Reason.INVALID_PROVIDER_RESPONSE: (
        "inspect_provider_version",
        "Check the supported Docker/Compose version and instance configuration.",
        False,
    ),
    Reason.PARTIAL_OBSERVATION: (
        "inspect_services",
        "Inspect the reported services with dev:logs; use dev:up when ready to reconcile.",
        True,
    ),
    Reason.STALE_OBSERVATION: (
        "repeat_observation",
        "Repeat the observation after the lifecycle operation settles.",
        True,
    ),
    Reason.OPERATION_BUSY: (
        "stop_watch",
        "Wait for the owned operation or explicitly use dev:down --stop-watch.",
        True,
    ),
    Reason.PREPARATION_IN_PROGRESS: (
        "retry_preparation",
        "Retry after dependency preparation or the current environment user finishes.",
        True,
    ),
    Reason.ENVIRONMENT_STALE: (
        "refresh_backend",
        "Stop watch if needed, then run mise run install:backend explicitly.",
        False,
    ),
    Reason.WATCH_BLOCKED: (
        "inspect_watch",
        "Run dev:doctor and resolve the abandoned owned watch session before further mutations.",
        False,
    ),
    Reason.SERVICE_UNAVAILABLE: (
        "inspect_services",
        "Inspect dev:status and select an available owned service.",
        True,
    ),
}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    reason: Reason
    operation: str
    phase: str = "admission"
    service: str | None = None

    def projection(self) -> dict[str, object]:
        remedy, _, retryable = _REMEDIES[self.reason]
        result: dict[str, object] = {
            "schemaVersion": 1,
            "reason": self.reason.value,
            "operation": self.operation,
            "phase": self.phase,
            "retryable": retryable,
            "remedy": remedy,
        }
        if self.service is not None:
            result["service"] = self.service
        return result

    def envelope(self) -> dict[str, object]:
        return {
            "code": "development_environment_unavailable",
            "diagnostic": self.projection(),
        }

    def human(self) -> str:
        return f"{self.operation}: {self.reason.value}\nNext: {_REMEDIES[self.reason][1]}\n"


def write_json(output: object, value: object) -> None:
    if not hasattr(output, "write"):
        raise TypeError("output must provide write")
    output.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def write_diagnostic(output: object, diagnostic: Diagnostic, output_format: OutputFormat) -> None:
    if output_format == "json":
        write_json(output, diagnostic.envelope())
    elif hasattr(output, "write"):
        output.write(diagnostic.human())
    else:
        raise TypeError("output must provide write")
