"""Canonical byte projection for retained governance state."""

from __future__ import annotations

from typing import cast

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation.model import (
    GOVERNANCE_JSON_LIMITS,
    GOVERNANCE_STATE_SCHEMA,
    MAX_GOVERNANCE_RULE_BYTES,
    EffectiveGovernanceRule,
    GovernanceRepository,
    GovernanceState,
)
from ci_coordinator.kernel import (
    CanonicalJsonError,
    JsonResourceLimits,
    bounded_canonical_json,
    load_strict_json,
)

MAX_GOVERNANCE_STATE_BYTES = 2_200_000
_STATE_JSON_LIMITS = JsonResourceLimits(max_depth=18, max_nodes=2_200_000)


def encode_governance_state(state: GovernanceState) -> bytes:
    if type(state) is not GovernanceState:
        raise TypeError("governance-state encoding requires an exact state")
    return bounded_canonical_json(
        state.identity_mapping(),
        max_bytes=MAX_GOVERNANCE_STATE_BYTES,
        resource_limits=_STATE_JSON_LIMITS,
    )


def decode_governance_state(value: bytes) -> GovernanceState:
    if type(value) is not bytes:
        raise TypeError("governance-state decoding requires exact bytes")
    try:
        decoded = load_strict_json(value, max_bytes=MAX_GOVERNANCE_STATE_BYTES)
        root = _exact_object(
            decoded,
            {"schemaVersion", "apiVersion", "scope", "repository", "rules"},
            "governance state",
        )
        if root["schemaVersion"] != GOVERNANCE_STATE_SCHEMA:
            raise ValueError("governance-state schema is unsupported")
        scope_value = _exact_object(
            root["scope"],
            {"installationId", "repositoryId"},
            "governance scope",
        )
        scope = RepositoryScope(
            _exact_int(scope_value["installationId"], "installation id"),
            _exact_int(scope_value["repositoryId"], "repository id"),
        )
        repository_value = _exact_object(
            root["repository"],
            {"ownerId", "owner", "name", "fullName", "defaultBranch"},
            "governance repository",
        )
        raw_rules = root["rules"]
        if type(raw_rules) is not list:
            raise TypeError("governance rules must be an exact array")
        rules = tuple(_decode_rule(rule) for rule in cast(list[object], raw_rules))
        state = GovernanceState(
            repository=GovernanceRepository(
                scope=scope,
                owner_id=_exact_int(repository_value["ownerId"], "repository owner id"),
                owner=_exact_text(repository_value["owner"], "repository owner"),
                name=_exact_text(repository_value["name"], "repository name"),
                full_name=_exact_text(repository_value["fullName"], "repository full name"),
                default_branch=_exact_text(
                    repository_value["defaultBranch"],
                    "repository default branch",
                ),
            ),
            api_version=_exact_text(root["apiVersion"], "provider API version"),
            rules=rules,
        )
        if encode_governance_state(state) != value:
            raise ValueError("governance-state bytes are not canonical")
        return state
    except (CanonicalJsonError, KeyError, TypeError, ValueError) as error:
        raise ValueError("governance-state bytes are not admitted") from error


def _decode_rule(value: object) -> EffectiveGovernanceRule:
    if type(value) is not dict:
        raise TypeError("effective governance rule must be an exact object")
    rule = cast(dict[str, object], value)
    canonical = bounded_canonical_json(
        rule,
        max_bytes=MAX_GOVERNANCE_RULE_BYTES,
        resource_limits=GOVERNANCE_JSON_LIMITS,
    )
    return EffectiveGovernanceRule(
        rule_type=_exact_text(rule.get("type"), "rule type"),
        ruleset_source_type=_exact_text(
            rule.get("ruleset_source_type"),
            "ruleset source type",
        ),
        ruleset_source=_exact_text(rule.get("ruleset_source"), "ruleset source"),
        ruleset_id=_exact_int(rule.get("ruleset_id"), "ruleset id"),
        canonical_json=canonical,
    )


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
