"""Immutable effective-governance state and fail-closed outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import (
    CanonicalJsonError,
    JsonResourceLimits,
    bounded_canonical_json,
    git_branch_name_is_admitted,
    hash_object,
    load_strict_json,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type GovernanceFailureReason = Literal[
    "unavailable",
    "rate_limited",
    "not_found",
    "malformed_provider_response",
    "provider_binding_mismatch",
    "observation_limit_exceeded",
]

MAX_GOVERNANCE_RULES = 1_000
MAX_GOVERNANCE_RULE_BYTES = 1_048_576
MAX_GOVERNANCE_AGGREGATE_RULE_BYTES = 2_097_152
MAX_GOVERNANCE_DEFAULT_BRANCH_BYTES = 512
GOVERNANCE_JSON_LIMITS = JsonResourceLimits(max_depth=16, max_nodes=16_384)
GOVERNANCE_STATE_SCHEMA = "github-effective-governance-state/v1"


@dataclass(frozen=True, slots=True)
class GovernanceRepository:
    scope: RepositoryScope
    owner_id: int
    owner: str
    name: str
    full_name: str
    default_branch: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("governance repository requires an exact scope")
        _require_positive_safe_integer(self.owner_id, "repository owner id")
        _require_component(self.owner, "repository owner")
        _require_component(self.name, "repository name")
        _require_text(self.full_name, "repository full name", maximum_bytes=1_025)
        _require_text(
            self.default_branch,
            "repository default branch",
            maximum_bytes=MAX_GOVERNANCE_DEFAULT_BRANCH_BYTES,
        )
        if not git_branch_name_is_admitted(self.default_branch):
            raise ValueError("repository default branch must be a canonical Git branch name")
        if self.full_name != f"{self.owner}/{self.name}":
            raise ValueError("repository full name must match owner and name")


@dataclass(frozen=True, slots=True)
class EffectiveGovernanceRule:
    rule_type: str
    ruleset_source_type: str
    ruleset_source: str
    ruleset_id: int
    canonical_json: bytes

    def __post_init__(self) -> None:
        _require_text(self.rule_type, "rule type", maximum_bytes=256)
        _require_text(self.ruleset_source_type, "ruleset source type", maximum_bytes=256)
        _require_text(self.ruleset_source, "ruleset source", maximum_bytes=1_025)
        _require_positive_safe_integer(self.ruleset_id, "ruleset id")
        if type(self.canonical_json) is not bytes or not self.canonical_json:
            raise TypeError("effective rule canonical JSON must be non-empty exact bytes")
        try:
            value = load_strict_json(
                self.canonical_json,
                max_bytes=MAX_GOVERNANCE_RULE_BYTES,
            )
            recanonicalized = bounded_canonical_json(
                value,
                max_bytes=MAX_GOVERNANCE_RULE_BYTES,
                resource_limits=GOVERNANCE_JSON_LIMITS,
            )
        except (CanonicalJsonError, ValueError) as error:
            raise ValueError("effective rule canonical JSON is not admitted") from error
        if type(value) is not dict or recanonicalized != self.canonical_json:
            raise ValueError("effective rule JSON must be one canonical object")
        rule = cast(dict[str, object], value)
        if (
            rule.get("type") != self.rule_type
            or rule.get("ruleset_source_type") != self.ruleset_source_type
            or rule.get("ruleset_source") != self.ruleset_source
            or rule.get("ruleset_id") != self.ruleset_id
        ):
            raise ValueError("effective rule metadata contradicts its canonical JSON")

    @property
    def canonical_text(self) -> str:
        return self.canonical_json.decode("utf-8", errors="strict")


@dataclass(frozen=True, slots=True)
class GovernanceState:
    repository: GovernanceRepository
    api_version: str
    rules: tuple[EffectiveGovernanceRule, ...]

    def __post_init__(self) -> None:
        if type(self.repository) is not GovernanceRepository:
            raise TypeError("governance state requires an exact repository")
        _require_text(self.api_version, "provider API version", maximum_bytes=64)
        if type(self.rules) is not tuple or len(self.rules) > MAX_GOVERNANCE_RULES:
            raise ValueError("governance state exceeds its rule-count bound")
        if any(type(rule) is not EffectiveGovernanceRule for rule in self.rules):
            raise TypeError("governance state requires exact effective rules")
        canonical_rules = tuple(rule.canonical_json for rule in self.rules)
        if canonical_rules != tuple(sorted(canonical_rules)):
            raise ValueError("effective rules must use canonical byte order")
        if len(set(canonical_rules)) != len(canonical_rules):
            raise ValueError("effective rules must be unique")
        if sum(len(value) for value in canonical_rules) > MAX_GOVERNANCE_AGGREGATE_RULE_BYTES:
            raise ValueError("governance state exceeds its aggregate byte bound")

    def identity_mapping(self) -> dict[str, object]:
        repository = self.repository
        return {
            "schemaVersion": GOVERNANCE_STATE_SCHEMA,
            "apiVersion": self.api_version,
            "scope": {
                "installationId": repository.scope.installation_id,
                "repositoryId": repository.scope.repository_id,
            },
            "repository": {
                "ownerId": repository.owner_id,
                "owner": repository.owner,
                "name": repository.name,
                "fullName": repository.full_name,
                "defaultBranch": repository.default_branch,
            },
            "rules": [load_strict_json(rule.canonical_json) for rule in self.rules],
        }


@dataclass(frozen=True, slots=True)
class GovernanceObservation:
    repository: GovernanceRepository
    api_version: str
    observed_at: datetime
    rules: tuple[EffectiveGovernanceRule, ...]
    state_digest: str
    consistency: Literal["best_effort"] = "best_effort"
    baseline_state: Literal["unbaselined"] = "unbaselined"

    def __post_init__(self) -> None:
        state = GovernanceState(self.repository, self.api_version, self.rules)
        _require_aware_instant(self.observed_at)
        if self.state_digest != hash_object(state.identity_mapping()):
            raise ValueError("governance state digest does not match the observed state")
        if self.consistency != "best_effort" or self.baseline_state != "unbaselined":
            raise ValueError("governance observation claims unsupported authority")

    @classmethod
    def from_state(cls, state: GovernanceState, *, observed_at: datetime) -> GovernanceObservation:
        if type(state) is not GovernanceState:
            raise TypeError("governance observation requires an exact state")
        return cls(
            repository=state.repository,
            api_version=state.api_version,
            observed_at=observed_at,
            rules=state.rules,
            state_digest=hash_object(state.identity_mapping()),
        )


@dataclass(frozen=True, slots=True)
class GovernanceObservationForbidden:
    pass


@dataclass(frozen=True, slots=True)
class GovernanceObservationUnavailable:
    reason: GovernanceFailureReason
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.reason not in {
            "unavailable",
            "rate_limited",
            "not_found",
            "malformed_provider_response",
            "provider_binding_mismatch",
            "observation_limit_exceeded",
        }:
            raise ValueError("governance observation failure reason is not admitted")
        if self.retry_after_seconds is not None and (
            self.reason != "rate_limited"
            or type(self.retry_after_seconds) is not int
            or not 0 <= self.retry_after_seconds <= 3_600
        ):
            raise ValueError("governance observation retry delay is invalid")


type GovernanceReadResult = GovernanceState | GovernanceObservationUnavailable
type GovernanceObservationOutcome = (
    GovernanceObservation | GovernanceObservationForbidden | GovernanceObservationUnavailable
)


def _require_positive_safe_integer(value: object, name: str) -> None:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER:
        raise ValueError(f"{name} must be a positive safe integer")


def _require_component(value: object, name: str) -> None:
    _require_text(value, name, maximum_bytes=512)
    if not isinstance(value, str) or value in {".", ".."} or "/" in value or "\0" in value:
        raise ValueError(f"{name} must be a repository path component")


def _require_text(value: object, name: str, *, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{name} must be bounded canonical Unicode scalar text")


def _require_aware_instant(value: object) -> None:
    offset = value.utcoffset() if type(value) is datetime and value.tzinfo is not None else None
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or offset is None
        or offset.total_seconds() % 60 != 0
    ):
        raise ValueError("governance observation time must be timezone-aware")
