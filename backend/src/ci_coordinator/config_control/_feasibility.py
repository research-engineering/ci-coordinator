from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import cast

from ci_coordinator.config_control._dynamic_ci_compatibility import compile_validation_catalog
from ci_coordinator.config_control._resources import ContractResource, contract_document
from ci_coordinator.config_control.contracts import PolicyDiagnostic
from ci_coordinator.kernel.canonical_json import CanonicalJsonError, bounded_canonical_json

type JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class _FeasibilityMeasurement:
    canonical_bytes: int


def validate_producer_feasibility(document: JsonObject) -> PolicyDiagnostic | None:
    dynamic_value = _object(document["repository"])["dynamicCi"]
    if dynamic_value is None:
        return None
    dynamic = _object(dynamic_value)
    if not _boolean(dynamic["planningEnabled"]):
        return None
    measurement = measure_producer_feasibility(dynamic)
    contract = _contract()
    if measurement.canonical_bytes <= contract.limit:
        return None
    return PolicyDiagnostic(
        code="feasibility.audit_projection_exceeded",
        phase="feasibility",
        rule_id="feasibility.audit-projection",
        instance_pointer="/repository/dynamicCi",
        parameters={
            "profileId": contract.profile_id,
            "limit": contract.limit,
            "observed": contract.limit + 1,
            "projectionId": contract.projection_id,
        },
    )


def measure_producer_feasibility(dynamic: JsonObject) -> _FeasibilityMeasurement:
    contract = _contract()
    projection = compile_validation_catalog(dynamic)
    try:
        encoded = bounded_canonical_json(projection, max_bytes=contract.limit)
    except CanonicalJsonError as error:
        if error.code != "canonical_json_max_bytes_exceeded":
            raise RuntimeError("config feasibility projection violated canonical JSON") from error
        return _FeasibilityMeasurement(contract.limit + 1)
    return _FeasibilityMeasurement(len(encoded))


@dataclass(frozen=True, slots=True)
class _FeasibilityContract:
    profile_id: str
    projection_id: str
    limit: int


@cache
def _contract() -> _FeasibilityContract:
    profile = contract_document(ContractResource.FEASIBILITY_PROFILE)
    input_contract = _object(profile["input"])
    projection = _object(profile["projection"])
    admission = _object(profile["byteAdmission"])
    if input_contract != {
        "document": "normalized repository policy",
        "pointer": "/repository/dynamicCi",
        "nullResult": "feasible",
        "planningDisabledResult": "feasible",
    }:
        raise RuntimeError("unsupported config feasibility input contract")
    if projection["fields"] != {
        "obligations": "compiled-dynamicCi.obligations:all-fields",
        "witnesses": "compiled-dynamicCi.witnesses:all-fields",
        "executionProfiles": "compiled-dynamicCi.executionProfiles:all-fields",
    }:
        raise RuntimeError("unsupported config feasibility projection")
    if projection["projectionId"] != "configured-validation-catalog-audit-lower-bound/v1":
        raise RuntimeError("unsupported config feasibility projection identity")
    if projection["canonicalOrder"] != "kernel.canonical-json/v1":
        raise RuntimeError("unsupported config feasibility canonical order")
    if projection["excludes"] != [
        "policyVersion",
        "riskClasses",
        "agentAdvice",
        "dependencyGraphSource",
        "globalRiskPaths",
        "fallbackTimeoutSeconds",
    ]:
        raise RuntimeError("unsupported config feasibility exclusion inventory")
    if admission["measureId"] != "canonical-json-utf-8-octets/v1":
        raise RuntimeError("unsupported config feasibility measure")
    limit = _integer(admission["limit"])
    if admission["overflowObserved"] != limit + 1:
        raise RuntimeError("unsupported config feasibility overflow sentinel")
    if admission["evaluation"] != (
        "encode the complete projection with bounded canonical JSON and stop after "
        "proving limit plus one"
    ):
        raise RuntimeError("unsupported config feasibility evaluation")
    if _object(profile["diagnostic"]) != {
        "code": "feasibility.audit_projection_exceeded",
        "phase": "feasibility",
        "ruleId": "feasibility.audit-projection",
        "instancePointer": "/repository/dynamicCi",
        "parameters": {
            "profileId": "profileId",
            "limit": "byteAdmission.limit",
            "observed": "byteAdmission.overflowObserved",
            "projectionId": "projection.projectionId",
        },
    }:
        raise RuntimeError("unsupported config feasibility diagnostic")
    return _FeasibilityContract(
        profile_id=_string(profile["profileId"]),
        projection_id=_string(projection["projectionId"]),
        limit=limit,
    )


def _object(value: object) -> JsonObject:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise RuntimeError("normalized policy object invariant failed")
    return cast(JsonObject, value)


def _string(value: object) -> str:
    if type(value) is not str:
        raise RuntimeError("config feasibility string invariant failed")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or value < 1:
        raise RuntimeError("config feasibility integer invariant failed")
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise RuntimeError("normalized policy boolean invariant failed")
    return value
