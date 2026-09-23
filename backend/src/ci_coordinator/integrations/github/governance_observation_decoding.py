"""Strict decoders for effective-governance provider responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.governance_observation import (
    GOVERNANCE_JSON_LIMITS,
    MAX_GOVERNANCE_DEFAULT_BRANCH_BYTES,
    MAX_GOVERNANCE_RULE_BYTES,
    EffectiveGovernanceRule,
)
from ci_coordinator.integrations.github._response_decoding import (
    canonical_text_or_none,
    object_or_none,
    positive_safe_integer,
    repository_component_is_admitted,
)
from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.kernel import (
    CanonicalJsonError,
    StrictJsonError,
    bounded_canonical_json,
    git_branch_name_is_admitted,
    load_strict_json,
)

MAX_GOVERNANCE_PAGE_BYTES = 1_048_576
MAX_GOVERNANCE_PAGE_RULES = 100

type GovernanceDecodeFailureReason = Literal[
    "malformed_provider_response",
    "observation_limit_exceeded",
]


@dataclass(frozen=True, slots=True)
class DecodedGovernanceRepository:
    repository_id: int
    owner_id: int
    repository: GitHubRepository
    full_name: str
    default_branch: str


@dataclass(frozen=True, slots=True)
class GovernanceDecodeFailure:
    reason: GovernanceDecodeFailureReason


type GovernanceRulePageDecodeResult = tuple[EffectiveGovernanceRule, ...] | GovernanceDecodeFailure


def decode_governance_repository(
    body: bytes,
    *,
    max_json_bytes: int,
) -> DecodedGovernanceRepository | None:
    value = _json_object(body, max_json_bytes=max_json_bytes)
    if value is None:
        return None
    owner = object_or_none(value.get("owner"))
    repository_id = positive_safe_integer(value.get("id"))
    owner_id = None if owner is None else positive_safe_integer(owner.get("id"))
    owner_login = (
        None if owner is None else canonical_text_or_none(owner.get("login"), maximum_bytes=512)
    )
    name = canonical_text_or_none(value.get("name"), maximum_bytes=512)
    full_name = canonical_text_or_none(value.get("full_name"), maximum_bytes=1_025)
    default_branch = canonical_text_or_none(
        value.get("default_branch"),
        maximum_bytes=MAX_GOVERNANCE_DEFAULT_BRANCH_BYTES,
    )
    if (
        repository_id is None
        or owner_id is None
        or owner_login is None
        or name is None
        or full_name != f"{owner_login}/{name}"
        or default_branch is None
        or not repository_component_is_admitted(owner_login)
        or not repository_component_is_admitted(name)
        or not git_branch_name_is_admitted(default_branch)
    ):
        return None
    try:
        return DecodedGovernanceRepository(
            repository_id=repository_id,
            owner_id=owner_id,
            repository=GitHubRepository(owner_login, name),
            full_name=full_name,
            default_branch=default_branch,
        )
    except ValueError:
        return None


def decode_effective_rule_page(body: bytes) -> GovernanceRulePageDecodeResult:
    if type(body) is not bytes or len(body) > MAX_GOVERNANCE_PAGE_BYTES:
        return GovernanceDecodeFailure("observation_limit_exceeded")
    try:
        value = load_strict_json(body, max_bytes=MAX_GOVERNANCE_PAGE_BYTES)
    except StrictJsonError:
        return GovernanceDecodeFailure("malformed_provider_response")
    if type(value) is not list:
        return GovernanceDecodeFailure("malformed_provider_response")
    if len(value) > MAX_GOVERNANCE_PAGE_RULES:
        return GovernanceDecodeFailure("observation_limit_exceeded")
    try:
        bounded_canonical_json(
            value,
            max_bytes=MAX_GOVERNANCE_PAGE_BYTES,
            resource_limits=GOVERNANCE_JSON_LIMITS,
        )
    except CanonicalJsonError as error:
        return GovernanceDecodeFailure(_canonical_failure_reason(error))

    rules: list[EffectiveGovernanceRule] = []
    for candidate in value:
        rule = object_or_none(candidate)
        if rule is None:
            return GovernanceDecodeFailure("malformed_provider_response")
        rule_type = canonical_text_or_none(rule.get("type"), maximum_bytes=256)
        source_type = canonical_text_or_none(
            rule.get("ruleset_source_type"),
            maximum_bytes=256,
        )
        source = canonical_text_or_none(rule.get("ruleset_source"), maximum_bytes=1_025)
        ruleset_id = positive_safe_integer(rule.get("ruleset_id"))
        if rule_type is None or source_type is None or source is None or ruleset_id is None:
            return GovernanceDecodeFailure("malformed_provider_response")
        try:
            canonical = bounded_canonical_json(
                rule,
                max_bytes=MAX_GOVERNANCE_RULE_BYTES,
                resource_limits=GOVERNANCE_JSON_LIMITS,
            )
            rules.append(
                EffectiveGovernanceRule(
                    rule_type=rule_type,
                    ruleset_source_type=source_type,
                    ruleset_source=source,
                    ruleset_id=ruleset_id,
                    canonical_json=canonical,
                )
            )
        except CanonicalJsonError as error:
            return GovernanceDecodeFailure(_canonical_failure_reason(error))
        except (TypeError, ValueError):
            return GovernanceDecodeFailure("malformed_provider_response")
    return tuple(rules)


def _json_object(body: bytes, *, max_json_bytes: int) -> dict[str, object] | None:
    try:
        return object_or_none(load_strict_json(body, max_bytes=max_json_bytes))
    except StrictJsonError:
        return None


def _canonical_failure_reason(error: CanonicalJsonError) -> GovernanceDecodeFailureReason:
    if error.code in {
        "canonical_json_max_bytes_exceeded",
        "json_max_depth_exceeded",
        "json_max_nodes_exceeded",
    }:
        return "observation_limit_exceeded"
    return "malformed_provider_response"
