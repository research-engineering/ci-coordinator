from __future__ import annotations

from collections.abc import Callable
from functools import cache
from hashlib import sha256
from typing import cast

from ci_coordinator.config_control._resources import ContractResource, contract_document
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.kernel.canonical_json import canonical_json
from ci_coordinator.validation_contract import ValidationDepth, depth_rank, validation_depth

type JsonObject = dict[str, object]


def compile_dynamic_ci(dynamic: JsonObject) -> JsonObject:
    catalog = compile_validation_catalog(dynamic)
    risk_classes = _sorted_strings(dynamic["riskClasses"])
    advice_input = _object(dynamic["agentAdvice"])
    advice = {
        "enabled": _boolean(advice_input["enabled"]),
        "modelIdAllowlist": _sorted_strings(advice_input["modelIdAllowlist"]),
        "promptHashAllowlist": _sorted_strings(advice_input["promptHashAllowlist"]),
        "minConfidence": advice_input["minConfidence"],
        "promptInjectionEvalRequired": _boolean(advice_input["promptInjectionEvalRequired"]),
    }
    dependency_graph = _object(dynamic["dependencyGraph"])
    dependency_graph_source = _string(dependency_graph["source"])
    global_risk_paths = _sorted_strings(dependency_graph["globalRiskPaths"])
    configured_catalog_hash = canonical_object_hash(catalog)
    policy_hash = canonical_object_hash(
        {
            "policyVersion": dynamic["policyVersion"],
            "configuredValidationCatalogHash": configured_catalog_hash,
            "riskClasses": risk_classes,
            "agentAdvice": advice,
            "dependencyGraphSource": dependency_graph_source,
            "globalRiskPaths": global_risk_paths,
            "fallbackTimeoutSeconds": dynamic["fallbackTimeoutSeconds"],
        }
    )
    return {
        "policyHash": policy_hash,
        "configuredValidationCatalogHash": configured_catalog_hash,
        "riskClasses": risk_classes,
        "agentAdvice": advice,
        "dependencyGraphSource": dependency_graph_source,
        "globalRiskPaths": global_risk_paths,
        "fallbackTimeoutSeconds": dynamic["fallbackTimeoutSeconds"],
        **catalog,
    }


def compile_validation_catalog(dynamic: JsonObject) -> JsonObject:
    _admit_contract()
    return {
        "obligations": _compile_ordered(
            dynamic["obligations"],
            compile_validation_obligation,
            identity_field="obligationId",
        ),
        "witnesses": _compile_ordered(
            dynamic["witnesses"],
            compile_executable_witness,
            identity_field="witnessId",
        ),
        "executionProfiles": _compile_ordered(
            dynamic["executionProfiles"],
            compile_execution_profile,
            identity_field="profileId",
        ),
    }


def compile_validation_obligation(value: JsonObject) -> JsonObject:
    responsibility = _object(value["responsibility"])
    return {
        "obligationId": _string(value["obligationId"]),
        "responsibility": {
            "paths": _sorted_strings(responsibility["paths"]),
            "riskClasses": _sorted_strings(responsibility["riskClasses"]),
        },
        "requiredWitnessIds": _sorted_strings(value["requiredWitnessIds"]),
        "defaultDepth": _string(value["defaultDepth"]),
        "fullDepth": _string(value["fullDepth"]),
        "omitAllowed": _boolean(value["omitAllowed"]),
    }


def compile_executable_witness(value: JsonObject) -> JsonObject:
    return {
        "witnessId": _string(value["witnessId"]),
        "executionProfileId": _string(value["executionProfileId"]),
        "supportedDepths": _sorted_depths(value["supportedDepths"]),
    }


def compile_execution_profile(value: JsonObject) -> JsonObject:
    sharding = _object(value["shardingPolicy"])
    return {
        "profileId": _string(value["profileId"]),
        "runnerProfileId": _string(value["runnerProfileId"]),
        "permissionProfileId": _string(value["permissionProfileId"]),
        "credentialProfileId": _string(value["credentialProfileId"]),
        "fixtureProfileId": _string(value["fixtureProfileId"]),
        "serviceProfileIds": _sorted_strings(value["serviceProfileIds"]),
        "capacityClassId": _string(value["capacityClassId"]),
        "shardingPolicy": {
            "maxShards": _integer(sharding["maxShards"]),
            "maxParallel": _integer(sharding["maxParallel"]),
            "maxItemsPerShard": _integer(sharding["maxItemsPerShard"]),
            "setupSecondsPerShard": _number(sharding["setupSecondsPerShard"]),
            "cpuWeight": _number(sharding["cpuWeight"]),
            "wallWeight": _number(sharding["wallWeight"]),
            "operatorWeight": _number(sharding["operatorWeight"]),
        },
    }


def sorted_unique_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(values), key=utf16_sort_key))


def canonical_object_hash(value: object) -> str:
    return sha256(canonical_json(value)).hexdigest()


def _compile_ordered(
    value: object,
    compiler: Callable[[JsonObject], JsonObject],
    *,
    identity_field: str,
) -> list[JsonObject]:
    compiled = [compiler(_object(item)) for item in _array(value)]
    return sorted(compiled, key=lambda item: utf16_sort_key(_string(item[identity_field])))


def _sorted_strings(value: object) -> list[str]:
    return list(sorted_unique_strings(_strings(_array(value))))


def _sorted_depths(value: object) -> list[ValidationDepth]:
    depths: set[ValidationDepth] = {validation_depth(item) for item in _array(value)}
    return list(sorted(depths, key=depth_rank))


@cache
def _admit_contract() -> None:
    profile = contract_document(ContractResource.DOCUMENT_PROFILE)
    compilation = _object(profile["compilation"])
    dynamic = _object(compilation["dynamicCi"])
    hashes = _object(dynamic["compatibilityHashes"])
    catalog = _object(hashes["configuredValidationCatalogHash"])
    policy = _object(hashes["policyHash"])
    expected_catalog_fields = {
        "obligations": "compiled-dynamicCi.obligations:all-fields",
        "witnesses": "compiled-dynamicCi.witnesses:all-fields",
        "executionProfiles": "compiled-dynamicCi.executionProfiles:all-fields",
    }
    expected_policy_fields = {
        "policyVersion": "/repository/dynamicCi/policyVersion",
        "configuredValidationCatalogHash": "computed:configuredValidationCatalogHash",
        "riskClasses": "compiled-dynamicCi.riskClasses",
        "agentAdvice": "compiled-dynamicCi.agentAdvice:all-fields",
        "dependencyGraphSource": "compiled-dynamicCi.dependencyGraphSource",
        "globalRiskPaths": "compiled-dynamicCi.globalRiskPaths",
        "fallbackTimeoutSeconds": "compiled-dynamicCi.fallbackTimeoutSeconds",
    }
    if catalog["fields"] != expected_catalog_fields:
        raise RuntimeError("unsupported configured validation catalog hash projection")
    if policy["fields"] != expected_policy_fields:
        raise RuntimeError("unsupported dynamic policy hash projection")
    if dynamic["sortedUniqueFields"] != [
        "riskClasses",
        "agentAdvice.modelIdAllowlist",
        "agentAdvice.promptHashAllowlist",
        "dependencyGraph.globalRiskPaths",
    ]:
        raise RuntimeError("unsupported dynamic policy sorted field inventory")
    if dynamic["entityOrder"] != {
        "obligations": "obligationId:ECMAScript-UTF16",
        "witnesses": "witnessId:ECMAScript-UTF16",
        "executionProfiles": "profileId:ECMAScript-UTF16",
    }:
        raise RuntimeError("unsupported dynamic entity ordering")
    if dynamic["sortedUniqueNestedFields"] != [
        "obligations.responsibility.paths",
        "obligations.responsibility.riskClasses",
        "obligations.requiredWitnessIds",
        "witnesses.supportedDepths",
        "executionProfiles.serviceProfileIds",
    ]:
        raise RuntimeError("unsupported dynamic nested set inventory")
    if compilation["setOrder"] != "ECMAScript-UTF16":
        raise RuntimeError("unsupported dynamic set ordering")


def _object(value: object) -> JsonObject:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise RuntimeError("normalized dynamic CI object invariant failed")
    return cast(JsonObject, value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise RuntimeError("normalized dynamic CI array invariant failed")
    return cast(list[object], value)


def _strings(values: list[object]) -> tuple[str, ...]:
    return tuple(_string(value) for value in values)


def _string(value: object) -> str:
    if type(value) is not str:
        raise RuntimeError("normalized dynamic CI string invariant failed")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise RuntimeError("normalized dynamic CI integer invariant failed")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError("normalized dynamic CI number invariant failed")
    return float(value)


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise RuntimeError("normalized dynamic CI boolean invariant failed")
    return value
