from __future__ import annotations

import json
import re
from collections.abc import Mapping

from scripts.bounded_process import CommandResult
from scripts.ci_business_witness.oracle import STAGES

_STAGES = frozenset(
    (
        *STAGES,
        "initialization",
        "build-application",
        "build-browser",
        "admit-trust-bundle",
        "seed-keycloak-runtime",
        "log-transport-qualification",
        "compose-admission",
        "database-start",
        "database-provision",
        "database-migrate",
        "identity-start",
        "identity-readiness",
        "application-start",
        "application-readiness",
        "application-identity",
        "database-endpoint",
        "browser-journey",
    )
)
_FAILURES = frozenset(
    {
        "cancelled",
        "lifecycle",
        "output-limit",
        "pipe-closure",
        "residual-descendant",
        "signal",
        "spawn",
        "timeout",
    }
)
_COMPOSE = frozenset({"config", "create", "run", "up", "ps", "port", "logs", "stop", "down"})
_SIGNALS = frozenset({"SIGINT", "SIGTERM", "SIGKILL", "SIGABRT", "SIGSEGV", "SIGPIPE"})
_BUILD_MARKERS = {
    "curl: (6)": "download-dns",
    "curl: (22)": "download-http",
    "curl: (28)": "download-timeout",
    "curl: (60)": "download-tls",
    "computed checksum did NOT match": "checksum-mismatch",
    "failed to fetch anonymous token": "registry-token",
    "429 Too Many Requests": "registry-rate-limit",
    "No space left on device": "disk-full",
    "ERR_PNPM_": "frontend-dependency",
    "error TS": "frontend-types",
    "failed to solve": "buildkit-failed",
}
_READINESS_FAILURES = frozenset({"http-status", "dns", "tls", "connect", "timeout", "transport"})
_REJECTIONS = frozenset(
    {
        "missing_required_setting",
        "invalid_setting_value",
        "unsupported_python_runtime",
        "runtime_caller_inventory_unavailable",
        "runtime_entrypoint_disposition_unavailable",
        "runtime_dependencies_unavailable",
        "production_admission_unavailable",
    }
)
_MARKERS = {
    "Application startup complete.": "startup-complete",
    "Application startup failed.": "startup-failed",
    "KeycloakUnavailable": "keycloak-unavailable",
    "Keycloak discovery is unavailable": "keycloak-discovery-unavailable",
    "Keycloak JWKS is unavailable": "keycloak-jwks-unavailable",
    "Keycloak dependency is unavailable": "keycloak-transport-unavailable",
    "CERTIFICATE_VERIFY_FAILED": "tls-verification-failed",
    "ConnectionRefusedError": "connection-refused",
    "ModuleNotFoundError": "module-unavailable",
    "PermissionError": "permission-denied",
    "ValueError:": "value-rejected",
    "TypeError:": "type-rejected",
    "ImportError:": "import-failed",
    "RuntimeError:": "runtime-error",
    "Address already in use": "address-in-use",
}
_BROWSER_PHASES = frozenset(
    {
        "authenticate",
        "authenticated-checkpoint",
        "navigate-budgets",
        "open-editor",
        "fill-editor",
        "submit-policy",
        "commit-response",
        "committed-checkpoint",
        "reload-policy",
        "replay",
        "conflicts",
        "csrf",
        "logout",
        "anonymous",
        "canary",
        "complete",
    }
)
_BROWSER_MEDIA = frozenset({"json", "html", "other"})
_BROWSER_ERROR_MARKERS = (
    ("response-body-protocol", r"Protocol error \(Network\.getResponseBody\)"),
    (
        "response-body-no-resource",
        r"No resource with given identifier found|No data found for resource with given identifier",
    ),
    ("target-closed", r"Target page, context or browser has been closed|Target closed"),
    ("json-truncated", r"Unexpected end of JSON input|Unterminated string in JSON"),
    ("json-unexpected-token", r"Unexpected token\b"),
    (
        "request-failed",
        r"Request failed\b|net::ERR_(?:FAILED|ABORTED|CONNECTION_CLOSED|CONNECTION_RESET|"
        r"CONNECTION_REFUSED|TIMED_OUT|NAME_NOT_RESOLVED|INTERNET_DISCONNECTED|"
        r"HTTP2_PROTOCOL_ERROR)\b",
    ),
    ("timeout", r"(?:Test timeout of|Timeout) [1-9][0-9]*ms exceeded"),
)


def browser_diagnostic(output: str, progress: str) -> dict[str, object]:
    locations: set[tuple[int, int]] = set()
    markers: set[str] = set()
    for line in output[: 4 * 1024 * 1024].splitlines():
        if len(line) > 4096:
            continue
        error = re.match(r"[ \t]*(Error|TimeoutError|SyntaxError|TargetClosedError): (.*)", line)
        if error is not None:
            if error[1] == "TimeoutError":
                markers.add("timeout")
            markers.update(
                category
                for category, pattern in _BROWSER_ERROR_MARKERS
                if re.search(pattern, error[2]) is not None
            )
        match = re.match(
            r"[ \t]*at [^\r\n]*?/tests/connected/administratorBudget\.spec\.ts:"
            r"([1-9][0-9]{0,4}):([1-9][0-9]{0,3})(?=[)\s]|$)",
            line,
        )
        if match is not None and len(locations) < 8:
            locations.add((int(match[1]), int(match[2])))
    diagnostic: dict[str, object] = {
        "errorMarkers": sorted(markers),
        "sourceLocations": [{"line": line, "column": column} for line, column in sorted(locations)],
        "progressAdmitted": False,
    }
    value = _object(progress, maximum=4096)
    if value is None or set(value) != {
        "browserPhase",
        "policyReadStatus",
        "policyWriteStatus",
        "policyWriteMedia",
        "policyWriteFinished",
        "policyWriteFailed",
        "policyWriteAborted",
    }:
        return diagnostic
    phase = value["browserPhase"]
    media = value["policyWriteMedia"]
    statuses = (value["policyReadStatus"], value["policyWriteStatus"])
    finished, failed, aborted = (
        value[key] for key in ("policyWriteFinished", "policyWriteFailed", "policyWriteAborted")
    )
    if (
        type(phase) is str
        and phase in _BROWSER_PHASES
        and (media is None or (type(media) is str and media in _BROWSER_MEDIA))
        and all(type(observed) is bool for observed in (finished, failed, aborted))
        and (aborted is False or failed is True)
        and all(
            status is None or (type(status) is int and 100 <= status <= 599) for status in statuses
        )
    ):
        diagnostic.update(value)
        diagnostic["progressAdmitted"] = True
    return diagnostic


def provider_diagnostic(
    stage: str, arguments: tuple[str, ...], result: CommandResult
) -> dict[str, object]:
    operation = "unknown"
    if arguments and arguments[0] == "compose" and len(arguments) > 5:
        operation = "compose:" + (arguments[5] if arguments[5] in _COMPOSE else "unknown")
    elif arguments and arguments[0] in {
        "build",
        "run",
        "inspect",
        "create",
        "start",
        "exec",
        "pull",
        "cp",
        "rm",
        "info",
        "version",
    }:
        operation = arguments[0]
    elif len(arguments) > 1 and arguments[0] in {"image", "network", "container", "volume"}:
        operation = (
            arguments[0]
            + ":"
            + (arguments[1] if arguments[1] in {"ls", "inspect", "rm"} else "unknown")
        )
    return {
        "stage": stage if stage in _STAGES else "unknown",
        "operation": operation,
        "exitCode": _exit_code(result.status),
        "failureKind": result.failure_kind
        if result.failure_kind in _FAILURES
        else (None if result.failure_kind is None else "unknown"),
        "signal": result.signal
        if result.signal in _SIGNALS
        else (None if result.signal is None else "unknown"),
        "processError": result.error is not None,
        "stdoutBytes": len(result.stdout.encode("utf-8", "surrogateescape")),
        "stderrBytes": len(result.stderr.encode("utf-8", "surrogateescape")),
        "buildErrorMarkers": sorted(
            label
            for marker, label in _BUILD_MARKERS.items()
            if operation == "build" and marker in result.stderr
        ),
    }


def readiness_diagnostic(output: str) -> dict[str, object]:
    value = _object(output, maximum=4096)
    if value is None or set(value) != {
        "schemaVersion",
        "ready",
        "attempts",
        "httpStatus",
        "failure",
    }:
        return {"admitted": False}
    status, failure, attempts, ready = (
        value[key] for key in ("httpStatus", "failure", "attempts", "ready")
    )
    if not (
        value["schemaVersion"] == "connected-readiness/v1"
        and type(ready) is bool
        and type(attempts) is int
        and 1 <= attempts <= 512
        and (status is None or (type(status) is int and 100 <= status <= 599))
        and (failure is None or (type(failure) is str and failure in _READINESS_FAILURES))
        and ((ready and status == 200 and failure is None) or (not ready and failure is not None))
    ):
        return {"admitted": False}
    return {
        "admitted": True,
        "ready": ready,
        "attempts": attempts,
        "httpStatus": status,
        "failure": failure,
    }


def container_diagnostic(output: str) -> dict[str, object]:
    state = _object(output, maximum=65536)
    if state is None:
        return {"availability": "unavailable"}
    status = state.get("Status")
    health = state.get("Health")
    health_status = health.get("Status") if type(health) is dict else "none"
    return {
        "availability": "observed",
        "status": status
        if type(status) is str
        and status in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
        else "unknown",
        "exitCode": _exit_code(state.get("ExitCode")),
        "running": state.get("Running") if type(state.get("Running")) is bool else None,
        "oomKilled": state.get("OOMKilled") if type(state.get("OOMKilled")) is bool else None,
        "health": health_status
        if type(health_status) is str
        and health_status in {"healthy", "starting", "unhealthy", "none"}
        else "unknown",
        "providerError": bool(state.get("Error")),
    }


def startup_diagnostic(output: str, *, allowed_fields: frozenset[str]) -> dict[str, object]:
    markers = {label for text, label in _MARKERS.items() if text in output}
    rejections = []
    for line in output.splitlines()[:4096]:
        start = line.find("{")
        value = _object(line[start:], maximum=4096) if start >= 0 else None
        if value is not None and value.get("event") == "server_log":
            if value.get("level") == "INFO" and value.get("reason") == "startup_complete":
                markers.add("startup-complete")
            elif value.get("level") == "ERROR" and value.get("reason") == "startup_failed":
                markers.add("startup-failed")
        if value is None or type(value.get("code")) is not str or value["code"] not in _REJECTIONS:
            continue
        field = value.get("field")
        rejections.append(
            {
                "code": value["code"],
                "field": field if type(field) is str and field in allowed_fields else None,
            }
        )
        if len(rejections) == 16:
            break
    return {"markers": sorted(markers), "rejections": rejections}


def _object(output: str, *, maximum: int) -> Mapping[str, object] | None:
    if not 0 < len(output.encode("utf-8", "surrogateescape")) <= maximum:
        return None
    try:
        value = json.loads(output)
    except (ValueError, RecursionError):
        return None
    return value if type(value) is dict else None


def _exit_code(value: object) -> int | None:
    return value if type(value) is int and -255 <= value <= 255 else None
