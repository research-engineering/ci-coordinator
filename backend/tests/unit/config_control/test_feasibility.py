from __future__ import annotations

from copy import deepcopy

import pytest

import ci_coordinator.config_control._feasibility as feasibility_module
from ci_coordinator.config_control._feasibility import (
    measure_producer_feasibility,
    validate_producer_feasibility,
)
from ci_coordinator.config_control._resources import ContractResource, contract_document
from ci_coordinator.config_control.contracts import PolicyDiagnostic
from ci_coordinator.kernel.canonical_json import CanonicalJsonError


def test_complete_catalog_projection_is_deterministic_and_non_mutating() -> None:
    dynamic = _dynamic_policy()
    snapshot = deepcopy(dynamic)

    first = measure_producer_feasibility(dynamic)
    second = measure_producer_feasibility(dynamic)

    assert dynamic == snapshot
    assert first == second
    assert first.canonical_bytes == 729


def test_feasibility_projection_excludes_non_catalog_policy_facts() -> None:
    baseline = measure_producer_feasibility(_dynamic_policy())
    changed = _dynamic_policy()
    changed["policyVersion"] = "x" * 100_000
    _object(changed["agentAdvice"])["modelIdAllowlist"] = ["model" * 20_000]
    _object(changed["dependencyGraph"])["globalRiskPaths"] = ["global/" + "x" * 100_000]

    assert measure_producer_feasibility(changed) == baseline


def test_null_and_disabled_dynamic_policy_require_no_runtime_audit_projection() -> None:
    assert validate_producer_feasibility({"repository": {"dynamicCi": None}}) is None
    dynamic = _dynamic_policy()
    dynamic["planningEnabled"] = False

    assert validate_producer_feasibility(_policy_document(dynamic)) is None


def test_exact_feasibility_byte_limit_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = feasibility_module._contract()
    monkeypatch.setattr(
        feasibility_module,
        "measure_producer_feasibility",
        lambda _: feasibility_module._FeasibilityMeasurement(contract.limit),
    )

    assert validate_producer_feasibility(_policy_document(_dynamic_policy())) is None


def test_unexpected_canonical_failure_is_not_reclassified_as_size_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_canonical_encoding(*_: object, **__: object) -> bytes:
        raise CanonicalJsonError("json_invalid_value", "$", "invalid test value")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(feasibility_module, "bounded_canonical_json", fail_canonical_encoding)

    with pytest.raises(
        RuntimeError,
        match="config feasibility projection violated canonical JSON",
    ):
        measure_producer_feasibility(_dynamic_policy())


def test_complete_catalog_projection_fails_closed_at_the_audit_byte_limit() -> None:
    dynamic = _dynamic_policy(path="a" * 1_048_000)
    result = validate_producer_feasibility(_policy_document(dynamic))

    assert result == PolicyDiagnostic(
        code="feasibility.audit_projection_exceeded",
        phase="feasibility",
        rule_id="feasibility.audit-projection",
        instance_pointer="/repository/dynamicCi",
        parameters={
            "profileId": "ci-config-dynamic-ci-audit-feasibility/v1",
            "limit": 1_048_576,
            "observed": 1_048_577,
            "projectionId": "configured-validation-catalog-audit-lower-bound/v1",
        },
    )


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        (("input", "pointer"), "/other", "input contract"),
        (("projection", "projectionId"), "other/v1", "projection identity"),
        (("projection", "excludes"), [], "exclusion inventory"),
        (("byteAdmission", "evaluation"), "other", "evaluation"),
        (("diagnostic", "ruleId"), "other", "diagnostic"),
    ],
)
def test_feasibility_machine_contract_rejects_projection_drift(
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str, str],
    replacement: object,
    message: str,
) -> None:
    profile = deepcopy(contract_document(ContractResource.FEASIBILITY_PROFILE))
    _object(profile[path[0]])[path[1]] = replacement
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(feasibility_module, "contract_document", lambda _: profile)
    feasibility_module._contract.cache_clear()
    try:
        with pytest.raises(RuntimeError, match=message):
            feasibility_module._contract()
    finally:
        feasibility_module._contract.cache_clear()


def _dynamic_policy(*, path: str = "backend/**") -> dict[str, object]:
    return {
        "planningEnabled": True,
        "policyVersion": "policy-v1",
        "riskClasses": ["source"],
        "agentAdvice": {
            "enabled": False,
            "modelIdAllowlist": [],
            "promptHashAllowlist": [],
            "minConfidence": 0.7,
            "promptInjectionEvalRequired": False,
        },
        "dependencyGraph": {"source": "configured", "globalRiskPaths": []},
        "fallbackTimeoutSeconds": 60,
        "obligations": [
            {
                "obligationId": "repository-quality",
                "responsibility": {"paths": [path], "riskClasses": ["source"]},
                "requiredWitnessIds": ["python-quality"],
                "defaultDepth": "standard",
                "fullDepth": "full",
                "omitAllowed": False,
            }
        ],
        "witnesses": [
            {
                "witnessId": "python-quality",
                "executionProfileId": "python-linux",
                "supportedDepths": ["standard", "full"],
            }
        ],
        "executionProfiles": [
            {
                "profileId": "python-linux",
                "runnerProfileId": "ubuntu-24.04",
                "permissionProfileId": "contents-read",
                "credentialProfileId": "none",
                "fixtureProfileId": "none",
                "serviceProfileIds": [],
                "capacityClassId": "hosted-standard",
                "shardingPolicy": {
                    "maxShards": 8,
                    "maxParallel": 4,
                    "maxItemsPerShard": 1000,
                    "setupSecondsPerShard": 15.0,
                    "cpuWeight": 1.0,
                    "wallWeight": 1.0,
                    "operatorWeight": 1.0,
                },
            }
        ],
    }


def _policy_document(dynamic: dict[str, object]) -> dict[str, object]:
    return {"repository": {"dynamicCi": dynamic}}


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value
