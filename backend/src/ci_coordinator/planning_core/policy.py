from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.repo_context import PlanningInput
from ci_coordinator.repo_context.freshness import (
    is_unicode_scalar_string,
    validate_path_pattern,
)
from ci_coordinator.validation_contract import ValidationCatalog

if TYPE_CHECKING:
    from ci_coordinator.config_control.planning_projection import DynamicCiPlanningProjection


@dataclass(frozen=True, slots=True)
class AgentAdvicePolicy:
    enabled: bool = False
    model_id_allowlist: tuple[str, ...] = ()
    prompt_hash_allowlist: tuple[str, ...] = ()
    min_confidence: float = 1.0
    prompt_injection_eval_required: bool = False

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("agent advice enabled must be a boolean")
        object.__setattr__(
            self,
            "model_id_allowlist",
            _canonical_model_ids(self.model_id_allowlist),
        )
        object.__setattr__(
            self,
            "prompt_hash_allowlist",
            _canonical_sha256s(self.prompt_hash_allowlist, field_name="prompt_hash_allowlist"),
        )
        if type(self.min_confidence) not in {int, float} or not 0 <= self.min_confidence <= 1:
            raise ValueError("agent advice minimum confidence must be in [0, 1]")
        if type(self.prompt_injection_eval_required) is not bool:
            raise ValueError("agent advice prompt-injection evaluation flag must be a boolean")
        if self.enabled and (
            not self.model_id_allowlist
            or not self.prompt_hash_allowlist
            or not self.prompt_injection_eval_required
        ):
            raise ValueError("enabled agent advice requires allowlists and injection evaluation")


@dataclass(frozen=True, slots=True)
class PlanningPolicy:
    config_epoch_id: str
    compiled_policy_hash: str
    policy_hash: str
    catalog: ValidationCatalog
    fallback_timeout_seconds: int
    agent_advice: AgentAdvicePolicy = field(default_factory=AgentAdvicePolicy)

    def __post_init__(self) -> None:
        _require_sha256(self.config_epoch_id, field_name="config_epoch_id")
        _require_sha256(self.compiled_policy_hash, field_name="compiled_policy_hash")
        _require_sha256(self.policy_hash, field_name="policy_hash")
        if not isinstance(self.catalog, ValidationCatalog):
            raise TypeError("catalog must be a ValidationCatalog")
        if (
            type(self.fallback_timeout_seconds) is not int
            or not 1 <= self.fallback_timeout_seconds <= 3600
        ):
            raise ValueError("fallback_timeout_seconds must be an integer in [1, 3600]")
        for obligation in self.catalog.obligations:
            for pattern in obligation.responsibility_paths:
                if validate_path_pattern(pattern) is not None:
                    raise ValueError("obligation responsibility path is not admitted")

    @property
    def catalog_hash(self) -> str:
        return self.catalog.catalog_hash

    @classmethod
    def from_projection(cls, projection: DynamicCiPlanningProjection) -> PlanningPolicy:
        projection.assert_integrity()
        catalog = getattr(projection, "validation_catalog", None)
        if not isinstance(catalog, ValidationCatalog):
            raise ValueError("planning projection does not expose a closed validation catalog")
        return cls(
            config_epoch_id=projection.epoch_id,
            compiled_policy_hash=projection.compiled_policy_hash,
            policy_hash=projection.policy_hash,
            catalog=catalog,
            fallback_timeout_seconds=projection.fallback_timeout_seconds,
            agent_advice=AgentAdvicePolicy(
                enabled=projection.agent_advice.enabled,
                model_id_allowlist=projection.agent_advice.model_id_allowlist,
                prompt_hash_allowlist=projection.agent_advice.prompt_hash_allowlist,
                min_confidence=projection.agent_advice.min_confidence,
                prompt_injection_eval_required=projection.agent_advice.prompt_injection_eval_required,
            ),
        )


def planner_admission_reasons(input: PlanningInput, policy: PlanningPolicy) -> tuple[str, ...]:
    reasons = set(input.fallback_reasons)
    reasons.update(planner_rejection_reasons(input, policy))
    return tuple(sorted(reasons, key=utf16_sort_key))


def planner_rejection_reasons(input: PlanningInput, policy: PlanningPolicy) -> tuple[str, ...]:
    reasons: set[str] = set()
    if policy.config_epoch_id != input.policy.epoch_id:
        reasons.add("planning_config_epoch_mismatch")
    if policy.compiled_policy_hash != input.policy.compiled_policy_hash:
        reasons.add("planning_compiled_policy_hash_mismatch")
    if policy.policy_hash != input.policy.policy_hash:
        reasons.add("planning_policy_hash_mismatch")
    return tuple(sorted(reasons, key=utf16_sort_key))


def _canonical_model_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    if any(
        not is_unicode_scalar_string(value)
        or not value
        or "\0" in value
        or len(value.encode("utf-8")) > 256
        for value in values
    ):
        raise ValueError("model_id_allowlist must contain bounded Unicode scalar strings")
    return tuple(sorted(set(values), key=utf16_sort_key))


def _canonical_sha256s(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    for value in values:
        _require_sha256(value, field_name=field_name + " item")
    return tuple(sorted(set(values), key=utf16_sort_key))


def _require_sha256(value: object, *, field_name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be lowercase SHA-256 hexadecimal")
