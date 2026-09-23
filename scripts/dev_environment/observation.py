from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from scripts.dev_environment.compose import ComposeError, ComposeProject, ServiceStatus
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.identity import InstanceIdentity

_ENDPOINT_SERVICES = {"api": "backend", "ui": "frontend", "postgres": "postgres"}


@dataclass(frozen=True, slots=True)
class Observation:
    payload: dict[str, object]
    reason: Reason | None = None


def unavailable_observation(identity: InstanceIdentity, reason: Reason) -> Observation:
    return Observation(
        {
            "projectName": identity.project_name,
            "rootDigest": identity.root_digest,
            "state": "missing" if reason == Reason.MISSING_STATE else "unavailable",
            "endpoints": {},
            "services": [],
            "observation": _metadata("unavailable", [{"reason": reason.value}]),
        },
        reason,
    )


def observe(identity: InstanceIdentity, project: ComposeProject) -> Observation:
    try:
        with project.observation_budget():
            for _attempt in range(2):
                statuses = project.diagnostic_status()
                before = project.observation_signature(statuses)
                endpoints: dict[str, str] = {}
                reasons: list[dict[str, str]] = []
                running = {item.service for item in statuses if item.state == "running"}
                for endpoint, service in _ENDPOINT_SERVICES.items():
                    if service not in running:
                        reasons.append(
                            {
                                "service": service,
                                "reason": Reason.SERVICE_UNAVAILABLE.value,
                            }
                        )
                        continue
                    try:
                        endpoints[endpoint] = project.endpoint(service)
                    except ComposeError as error:
                        if error.reason == Reason.FOREIGN_STATE:
                            raise
                        reasons.append({"service": service, "reason": error.reason.value})
                after_statuses = project.diagnostic_status()
                after = project.observation_signature(after_statuses)
                stable = before == after
                if not stable:
                    reasons.append({"reason": Reason.STALE_OBSERVATION.value})
                payload: dict[str, object] = {
                    "projectName": identity.project_name,
                    "rootDigest": identity.root_digest,
                    "state": "observed",
                    "endpoints": endpoints,
                    "services": [_service(item) for item in statuses],
                    "observation": _metadata("stable" if stable else "changing", reasons),
                }
                if stable:
                    return Observation(payload, Reason.PARTIAL_OBSERVATION if reasons else None)
            return Observation(payload, Reason.STALE_OBSERVATION)
    except ComposeError as error:
        if error.reason == Reason.FOREIGN_STATE:
            raise
        return unavailable_observation(identity, error.reason)


def _metadata(consistency: str, reasons: list[dict[str, str]]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "observedAt": datetime.now(UTC).isoformat(),
        "consistency": consistency,
        "completeness": "complete" if consistency == "stable" and not reasons else "partial",
        "reasons": reasons,
    }


def _service(status: ServiceStatus) -> dict[str, object]:
    return {
        "health": status.health,
        "service": status.service,
        "state": status.state,
        "exitCode": status.exit_code,
    }


def render_human(payload: dict[str, object]) -> str:
    lines = [
        f"{payload.get('projectName', 'Development environment')}: "
        f"{payload.get('state', 'observed')}"
    ]
    services = payload.get("services", [])
    if isinstance(services, list):
        lines.extend(
            f"  {item['service']}: {item['state']} {item.get('health', '')} "
            f"(exit {item.get('exitCode', '?')})"
            for item in services
            if isinstance(item, dict)
        )
    endpoints = payload.get("endpoints", {})
    if isinstance(endpoints, dict):
        lines.extend(f"  {name}: {url}" for name, url in sorted(endpoints.items()))
    metadata = payload.get("observation")
    if isinstance(metadata, dict):
        lines.append(f"Observation: {metadata['consistency']}, {metadata['completeness']}")
    debugger = payload.get("debugger")
    if isinstance(debugger, dict):
        lines.append(
            "Attach using this configuration; interrupt this command to restore ordinary mode:"
        )
        lines.append(json.dumps(debugger, sort_keys=True, indent=2))
    return "\n".join(lines) + "\n"
