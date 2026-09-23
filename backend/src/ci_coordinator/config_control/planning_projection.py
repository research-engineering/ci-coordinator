"""Typed deterministic-planning projection of an admitted compiled epoch."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal, cast

from ci_coordinator.config_control._compiler import compiled_policy_hash, config_epoch_id
from ci_coordinator.config_control.contracts import ValidatedEpochDraft
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.validation_contract import (
    ExecutableWitness,
    ExecutionProfile,
    ShardingPolicy,
    ValidationCatalog,
    ValidationDepth,
    ValidationObligation,
    depth_rank,
    validation_depth,
)

__all__ = [
    "DynamicCiPlanningProjection",
    "ExecutableWitness",
    "ExecutionProfile",
    "ShardingPolicy",
    "ValidationCatalog",
    "ValidationDepth",
    "ValidationObligation",
    "project_dynamic_ci_planning",
]


@dataclass(frozen=True, slots=True)
class DynamicCiPlanningProjection:
    _admitted_epoch: ValidatedEpochDraft = field(repr=False)
    epoch_id: str
    compiled_policy_hash: str
    policy_hash: str
    dependency_graph_source: Literal["configured", "generated"]
    global_risk_paths: tuple[str, ...]
    risk_classes: tuple[str, ...]
    agent_advice: _CompiledAgentAdvice
    fallback_timeout_seconds: int
    validation_catalog: ValidationCatalog

    def assert_integrity(self) -> None:
        """Reject a projection whose public facts differ from its admitted epoch."""

        _verify_epoch_identity(self._admitted_epoch)
        if self != _project_verified_epoch(self._admitted_epoch):
            raise ValueError("planning projection facts do not match the admitted epoch")


def project_dynamic_ci_planning(
    draft: ValidatedEpochDraft,
) -> DynamicCiPlanningProjection | None:
    """Return planner facts, or no projection for an observe-only epoch."""

    _verify_epoch_identity(draft)
    return _project_verified_epoch(draft)


def _project_verified_epoch(
    draft: ValidatedEpochDraft,
) -> DynamicCiPlanningProjection | None:
    compiled = _object(json.loads(draft.compiled_policy_bytes), field_name="compiled policy")
    dynamic = compiled.get("dynamicCi")
    if dynamic is None:
        return None
    value = _object(dynamic, field_name="compiled dynamic CI policy")
    catalog = ValidationCatalog(
        obligations=tuple(
            _validation_obligation(item)
            for item in _objects(value.get("obligations"), field_name="dynamicCi.obligations")
        ),
        witnesses=tuple(
            _executable_witness(item)
            for item in _objects(value.get("witnesses"), field_name="dynamicCi.witnesses")
        ),
        execution_profiles=tuple(
            _execution_profile(item)
            for item in _objects(
                value.get("executionProfiles"),
                field_name="dynamicCi.executionProfiles",
            )
        ),
    )
    configured_catalog_hash = _sha256(
        value.get("configuredValidationCatalogHash"),
        field_name="dynamicCi.configuredValidationCatalogHash",
    )
    if catalog.catalog_hash != configured_catalog_hash:
        raise ValueError("configured validation catalog hash does not match catalog facts")
    return DynamicCiPlanningProjection(
        _admitted_epoch=draft,
        epoch_id=draft.epoch_id,
        compiled_policy_hash=draft.epoch_hash,
        policy_hash=_sha256(value.get("policyHash"), field_name="dynamicCi.policyHash"),
        dependency_graph_source=_dependency_graph_source(
            value.get("dependencyGraphSource"),
            field_name="dynamicCi.dependencyGraphSource",
        ),
        global_risk_paths=_canonical_strings(
            value.get("globalRiskPaths"), field_name="dynamicCi.globalRiskPaths"
        ),
        risk_classes=_canonical_strings(
            value.get("riskClasses"), field_name="dynamicCi.riskClasses"
        ),
        agent_advice=_agent_advice(value.get("agentAdvice")),
        fallback_timeout_seconds=_integer(
            value.get("fallbackTimeoutSeconds"),
            field_name="dynamicCi.fallbackTimeoutSeconds",
            minimum=1,
            maximum=3600,
        ),
        validation_catalog=catalog,
    )


@dataclass(frozen=True, slots=True)
class _CompiledAgentAdvice:
    enabled: bool
    model_id_allowlist: tuple[str, ...]
    prompt_hash_allowlist: tuple[str, ...]
    min_confidence: float
    prompt_injection_eval_required: bool


def _agent_advice(value: object) -> _CompiledAgentAdvice:
    advice = _object(value, field_name="dynamicCi.agentAdvice")
    confidence = _number(
        advice.get("minConfidence"),
        field_name="dynamicCi.agentAdvice.minConfidence",
        minimum=0,
        maximum=1,
    )
    return _CompiledAgentAdvice(
        enabled=_boolean(advice.get("enabled"), field_name="dynamicCi.agentAdvice.enabled"),
        model_id_allowlist=_canonical_strings(
            advice.get("modelIdAllowlist"),
            field_name="dynamicCi.agentAdvice.modelIdAllowlist",
        ),
        prompt_hash_allowlist=_canonical_strings(
            advice.get("promptHashAllowlist"),
            field_name="dynamicCi.agentAdvice.promptHashAllowlist",
        ),
        min_confidence=confidence,
        prompt_injection_eval_required=_boolean(
            advice.get("promptInjectionEvalRequired"),
            field_name="dynamicCi.agentAdvice.promptInjectionEvalRequired",
        ),
    )


def _validation_obligation(value: object) -> ValidationObligation:
    obligation = _object(value, field_name="dynamicCi.obligation")
    responsibility = _object(
        obligation.get("responsibility"),
        field_name="dynamicCi.obligation.responsibility",
    )
    return ValidationObligation(
        obligation_id=_nonempty_string(
            obligation.get("obligationId"),
            field_name="dynamicCi.obligation.obligationId",
        ),
        responsibility_paths=_canonical_strings(
            responsibility.get("paths"),
            field_name="dynamicCi.obligation.responsibility.paths",
        ),
        responsibility_risk_classes=_canonical_strings(
            responsibility.get("riskClasses"),
            field_name="dynamicCi.obligation.responsibility.riskClasses",
        ),
        required_witness_ids=_canonical_strings(
            obligation.get("requiredWitnessIds"),
            field_name="dynamicCi.obligation.requiredWitnessIds",
            require_nonempty=True,
        ),
        default_depth=_depth(
            obligation.get("defaultDepth"),
            field_name="dynamicCi.obligation.defaultDepth",
        ),
        full_depth=_depth(
            obligation.get("fullDepth"),
            field_name="dynamicCi.obligation.fullDepth",
        ),
        omit_allowed=_boolean(
            obligation.get("omitAllowed"),
            field_name="dynamicCi.obligation.omitAllowed",
        ),
    )


def _executable_witness(value: object) -> ExecutableWitness:
    witness = _object(value, field_name="dynamicCi.witness")
    depths = tuple(
        _depth(item, field_name="dynamicCi.witness.supportedDepths item")
        for item in _array(witness.get("supportedDepths"), field_name="supportedDepths")
    )
    if depths != tuple(sorted(set(depths), key=depth_rank)):
        raise ValueError("dynamicCi.witness.supportedDepths must be canonical")
    return ExecutableWitness(
        witness_id=_nonempty_string(
            witness.get("witnessId"), field_name="dynamicCi.witness.witnessId"
        ),
        execution_profile_id=_nonempty_string(
            witness.get("executionProfileId"),
            field_name="dynamicCi.witness.executionProfileId",
        ),
        supported_depths=depths,
    )


def _execution_profile(value: object) -> ExecutionProfile:
    profile = _object(value, field_name="dynamicCi.executionProfile")
    sharding = _object(
        profile.get("shardingPolicy"),
        field_name="dynamicCi.executionProfile.shardingPolicy",
    )
    return ExecutionProfile(
        profile_id=_profile_string(profile, "profileId"),
        runner_profile_id=_profile_string(profile, "runnerProfileId"),
        permission_profile_id=_profile_string(profile, "permissionProfileId"),
        credential_profile_id=_profile_string(profile, "credentialProfileId"),
        fixture_profile_id=_profile_string(profile, "fixtureProfileId"),
        service_profile_ids=_canonical_strings(
            profile.get("serviceProfileIds"),
            field_name="dynamicCi.executionProfile.serviceProfileIds",
        ),
        capacity_class_id=_profile_string(profile, "capacityClassId"),
        sharding_policy=ShardingPolicy(
            max_shards=_bounded_integer(sharding, "maxShards", 256),
            max_parallel=_bounded_integer(sharding, "maxParallel", 256),
            max_items_per_shard=_bounded_integer(sharding, "maxItemsPerShard", 10_000),
            setup_seconds_per_shard=_bounded_number(sharding, "setupSecondsPerShard", 3600),
            cpu_weight=_bounded_number(sharding, "cpuWeight", 1000),
            wall_weight=_bounded_number(sharding, "wallWeight", 1000),
            operator_weight=_bounded_number(sharding, "operatorWeight", 1000),
        ),
    )


def _profile_string(profile: dict[str, object], field: str) -> str:
    return _nonempty_string(profile.get(field), field_name=f"dynamicCi.executionProfile.{field}")


def _bounded_integer(value: dict[str, object], field: str, maximum: int) -> int:
    return _integer(
        value.get(field),
        field_name=f"dynamicCi.executionProfile.shardingPolicy.{field}",
        minimum=1,
        maximum=maximum,
    )


def _bounded_number(value: dict[str, object], field: str, maximum: float) -> float:
    return _number(
        value.get(field),
        field_name=f"dynamicCi.executionProfile.shardingPolicy.{field}",
        minimum=0,
        maximum=maximum,
    )


def _verify_epoch_identity(draft: ValidatedEpochDraft) -> None:
    compiled_hash = compiled_policy_hash(draft.compiled_policy_bytes)
    if compiled_hash != draft.epoch_hash:
        raise ValueError("compiled policy bytes do not match epoch hash")
    expected_epoch_id = config_epoch_id(
        scope=draft.scope,
        source_hash=draft.source_hash,
        document_hash=draft.document_hash,
        epoch_hash=compiled_hash,
    )
    if expected_epoch_id != draft.epoch_id:
        raise ValueError("validated epoch fields do not match epoch identity")


def _object(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise ValueError(field_name + " must be an object")
    return cast(dict[str, object], value)


def _objects(value: object, *, field_name: str) -> list[dict[str, object]]:
    return [
        _object(item, field_name=field_name + " item")
        for item in _array(value, field_name=field_name)
    ]


def _array(value: object, *, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(field_name + " must be an array")
    return cast(list[object], value)


def _canonical_strings(
    value: object,
    *,
    field_name: str,
    require_nonempty: bool = False,
) -> tuple[str, ...]:
    values = tuple(
        _nonempty_string(item, field_name=field_name + " item")
        for item in _array(value, field_name=field_name)
    )
    if require_nonempty and not values:
        raise ValueError(field_name + " must not be empty")
    if values != tuple(sorted(set(values), key=utf16_sort_key)):
        raise ValueError(field_name + " must be canonical")
    return values


def _nonempty_string(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value or not _is_unicode_scalar_string(value):
        raise ValueError(field_name + " must be a non-empty Unicode scalar string")
    return value


def _sha256(value: object, *, field_name: str) -> str:
    result = _nonempty_string(value, field_name=field_name)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(field_name + " must be lowercase SHA-256 hexadecimal")
    return result


def _dependency_graph_source(
    value: object, *, field_name: str
) -> Literal["configured", "generated"]:
    result = _nonempty_string(value, field_name=field_name)
    if result not in {"configured", "generated"}:
        raise ValueError(field_name + " is not an admitted dependency graph source")
    return cast(Literal["configured", "generated"], result)


def _depth(value: object, *, field_name: str) -> ValidationDepth:
    try:
        return validation_depth(value, field_name=field_name)
    except ValueError as error:
        raise ValueError(field_name + " is not an admitted validation depth") from error


def _integer(value: object, *, field_name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(field_name + " is outside its admitted range")
    return value


def _number(value: object, *, field_name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(field_name + " is outside its admitted range")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(field_name + " is outside its admitted range")
    return result


def _boolean(value: object, *, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(field_name + " must be a boolean")
    return value


def _is_unicode_scalar_string(value: str) -> bool:
    return all(not 0xD800 <= ord(character) <= 0xDFFF for character in value)
