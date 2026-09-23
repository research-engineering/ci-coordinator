"""Canonical byte codec for governance-baseline operation commands."""

from __future__ import annotations

from typing import cast

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline.model import (
    MAX_BASELINE_COMMAND_BYTES,
    GovernanceBaselineCommand,
    GovernanceBaselinePointer,
)
from ci_coordinator.kernel import (
    CanonicalJsonError,
    JsonResourceLimits,
    bounded_canonical_json,
    load_strict_json,
)

_COMMAND_SCHEMA = "governance-baseline-command/v1"
_COMMAND_JSON_LIMITS = JsonResourceLimits(max_depth=8, max_nodes=32)


def encode_governance_baseline_command(command: GovernanceBaselineCommand) -> bytes:
    if type(command) is not GovernanceBaselineCommand:
        raise TypeError("governance baseline command encoding requires an exact command")
    return bounded_canonical_json(
        {
            "schemaVersion": _COMMAND_SCHEMA,
            "scope": {
                "installationId": command.scope.installation_id,
                "repositoryId": command.scope.repository_id,
            },
            "operationId": command.operation_id,
            "expectedStateDigest": command.expected_state_digest,
            "expectedActive": (
                None
                if command.expected_active is None
                else command.expected_active.identity_mapping()
            ),
            "actor": command.actor,
            "reason": command.reason,
        },
        max_bytes=MAX_BASELINE_COMMAND_BYTES,
        resource_limits=_COMMAND_JSON_LIMITS,
    )


def decode_governance_baseline_command(value: bytes) -> GovernanceBaselineCommand:
    if type(value) is not bytes:
        raise TypeError("governance baseline command decoding requires exact bytes")
    try:
        root = _exact_object(
            load_strict_json(value, max_bytes=MAX_BASELINE_COMMAND_BYTES),
            {
                "schemaVersion",
                "scope",
                "operationId",
                "expectedStateDigest",
                "expectedActive",
                "actor",
                "reason",
            },
            "governance baseline command",
        )
        if root["schemaVersion"] != _COMMAND_SCHEMA:
            raise ValueError("governance baseline command schema is unsupported")
        scope_value = _exact_object(
            root["scope"],
            {"installationId", "repositoryId"},
            "governance baseline scope",
        )
        scope = RepositoryScope(
            _exact_int(scope_value["installationId"], "installation id"),
            _exact_int(scope_value["repositoryId"], "repository id"),
        )
        expected = root["expectedActive"]
        pointer = None
        if expected is not None:
            expected_value = _exact_object(
                expected,
                {"baselineId", "version", "stateDigest"},
                "expected governance baseline",
            )
            pointer = GovernanceBaselinePointer(
                scope=scope,
                baseline_id=_exact_text(expected_value["baselineId"], "baseline id"),
                version=_exact_int(expected_value["version"], "baseline version"),
                state_digest=_exact_text(expected_value["stateDigest"], "baseline state digest"),
            )
        command = GovernanceBaselineCommand(
            scope=scope,
            operation_id=_exact_text(root["operationId"], "operation id"),
            expected_state_digest=_exact_text(
                root["expectedStateDigest"],
                "expected state digest",
            ),
            expected_active=pointer,
            actor=_exact_text(root["actor"], "actor"),
            reason=_exact_text(root["reason"], "reason"),
        )
        if encode_governance_baseline_command(command) != value:
            raise ValueError("governance baseline command bytes are not canonical")
        return command
    except (CanonicalJsonError, KeyError, TypeError, UnicodeError, ValueError) as error:
        raise ValueError("governance baseline command bytes are not admitted") from error


def _exact_object(
    value: object,
    keys: set[str],
    name: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{name} has an unsupported shape")
    return cast(dict[str, object], value)


def _exact_text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be exact text")
    return value


def _exact_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    return value
