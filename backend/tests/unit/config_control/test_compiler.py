from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from io import StringIO

import pytest
from ruamel.yaml import YAML

import ci_coordinator.config_control._compiler as compiler_module
import ci_coordinator.config_control._schema_validation as schema_validation_module
from ci_coordinator.config_control import admit_policy_document
from ci_coordinator.config_control._compiler import (
    _admit_output_bytes,
    _Compilation,
    _compile_document,
    _compiled_structure_diagnostic,
    compile_policy,
    compiled_policy_hash,
    config_epoch_id,
)
from ci_coordinator.config_control._dynamic_ci_compatibility import (
    compile_dynamic_ci,
    sorted_unique_strings,
)
from ci_coordinator.config_control._resources import ContractResource, contract_document
from ci_coordinator.config_control.contracts import (
    PolicyDiagnostic,
    PolicySourceFormat,
    RepositoryScope,
    ValidatedEpochDraft,
)
from ci_coordinator.config_control.planning_projection import (
    ExecutableWitness,
    ExecutionProfile,
    ValidationCatalog,
    ValidationObligation,
    project_dynamic_ci_planning,
)
from ci_coordinator.kernel.canonical_json import canonical_json


def test_compiler_projects_the_complete_policy_and_fixed_identity_vectors() -> None:
    document = _normalized_policy_document()
    snapshot = deepcopy(document)

    result = compile_policy(document, raw_source=b'{"fixture":"compiler"}', source_format="json")

    assert isinstance(result, _Compilation)
    assert document == snapshot
    assert result.scope == RepositoryScope(installation_id=101, repository_id=202)
    assert result.normalized_document_bytes == canonical_json(document)
    assert json.loads(result.compiled_policy_bytes) == _expected_compiled_policy()
    assert result.source_hash == "feb499384859a8771e16a81b5a4482bbf13d6d06e59e37f0fc8153a62df3e2a6"
    assert (
        result.document_hash == "b456265bd689a5258696b1b34031441bd0ef93be76937690125305d98d26815a"
    )
    assert result.epoch_hash == "9bded91f0f5d22e00c0faf9196d8c71123c310fa34a474b27ad674dd2404e204"
    assert result.epoch_id == "880fb818cfe1893ad53b3c6766e99854cc69209bcf7403dededd6f95da07a9a9"


def test_compiler_maps_null_and_disabled_dynamic_policy_to_null() -> None:
    null_document = _normalized_policy_document()
    _repository(null_document)["dynamicCi"] = None
    disabled_document = _normalized_policy_document()
    _dynamic(disabled_document)["planningEnabled"] = False

    assert _compile_document(null_document)["dynamicCi"] is None
    assert _compile_document(disabled_document)["dynamicCi"] is None


@pytest.mark.parametrize("source_format", ("json", "yaml-1.2"))
@pytest.mark.parametrize(
    ("field", "token", "integer"),
    (
        ("maxShards", "8.0", 8),
        ("maxShards", "8e0", 8),
        ("maxParallel", "4.0", 4),
        ("maxParallel", "4e0", 4),
        ("maxItemsPerShard", "1000.0", 1000),
        ("maxItemsPerShard", "1e3", 1000),
    ),
)
def test_public_admission_normalizes_each_integral_sharding_number(
    source_format: PolicySourceFormat, field: str, token: str, integer: int
) -> None:
    control_source = _sharding_source(field, str(integer), source_format)
    source = _sharding_source(field, token, source_format)
    control = admit_policy_document(control_source, source_format)
    admitted = admit_policy_document(source, source_format)

    assert isinstance(control, ValidatedEpochDraft)
    assert isinstance(admitted, ValidatedEpochDraft)
    assert admitted.source_bytes == source != control.source_bytes
    assert admitted.source_hash != control.source_hash
    assert admitted.epoch_id != control.epoch_id
    assert admitted == replace(
        control,
        source_bytes=source,
        source_hash=admitted.source_hash,
        epoch_id=admitted.epoch_id,
    )
    projection = project_dynamic_ci_planning(admitted)
    control_projection = project_dynamic_ci_planning(control)
    assert projection is not None and control_projection is not None
    assert projection.policy_hash == control_projection.policy_hash
    assert projection.validation_catalog == control_projection.validation_catalog
    sharding = projection.validation_catalog.execution_profiles[0].sharding_policy
    assert (sharding.max_shards, sharding.max_parallel, sharding.max_items_per_shard) == (
        8,
        4,
        1000,
    )
    assert all(
        type(value) is int
        for value in (sharding.max_shards, sharding.max_parallel, sharding.max_items_per_shard)
    )


@pytest.mark.parametrize("source_format", ("json", "yaml-1.2"))
@pytest.mark.parametrize("field", ("maxShards", "maxParallel", "maxItemsPerShard"))
@pytest.mark.parametrize(
    ("token", "keyword"),
    (("true", "type"), ('"8"', "type"), ("8.5", "type"), ("0", "minimum")),
)
def test_public_sharding_admission_preserves_structural_diagnostics(
    source_format: PolicySourceFormat, field: str, token: str, keyword: str
) -> None:
    result = admit_policy_document(_sharding_source(field, token, source_format), source_format)

    assert result == (
        PolicyDiagnostic(
            code="structure.invalid",
            phase="structure",
            rule_id="schema:" + keyword,
            instance_pointer="/repository/dynamicCi/executionProfiles/0/shardingPolicy/" + field,
            parameters={
                "schemaKeyword": keyword,
                "schemaPointer": "/$defs/shardingPolicy/properties/" + field + "/" + keyword,
            },
        ),
    )


@pytest.mark.parametrize("source_format", ("json", "yaml-1.2"))
@pytest.mark.parametrize(
    ("field", "token"),
    (("maxShards", "257.0"), ("maxParallel", "257e0"), ("maxItemsPerShard", "10001.0")),
)
def test_public_sharding_admission_does_not_widen_numeric_bounds(
    source_format: PolicySourceFormat, field: str, token: str
) -> None:
    result = admit_policy_document(_sharding_source(field, token, source_format), source_format)

    assert result == (
        PolicyDiagnostic(
            code="structure.invalid",
            phase="structure",
            rule_id="schema:maximum",
            instance_pointer="/repository/dynamicCi/executionProfiles/0/shardingPolicy/" + field,
            parameters={
                "schemaKeyword": "maximum",
                "schemaPointer": "/$defs/shardingPolicy/properties/" + field + "/maximum",
            },
        ),
    )


@pytest.mark.parametrize("source_format", ("json", "yaml-1.2"))
def test_integral_sharding_values_still_require_bounded_parallelism(
    source_format: PolicySourceFormat,
) -> None:
    result = admit_policy_document(
        _sharding_source("maxParallel", "9.0", source_format), source_format
    )

    assert result == (
        PolicyDiagnostic(
            code="semantics.invalid",
            phase="semantics",
            rule_id="profile.parallelism-bounded",
            instance_pointer="/repository/dynamicCi/executionProfiles/0/shardingPolicy/maxParallel",
            parameters={},
        ),
    )


@pytest.mark.parametrize("source_format", ("json", "yaml-1.2"))
def test_disabled_dynamic_policy_remains_observe_only_with_integral_sharding_values(
    source_format: PolicySourceFormat,
) -> None:
    source = _sharding_source("maxShards", "8.0", source_format, planning_enabled=False)
    control_source = _sharding_source("maxShards", "8", source_format, planning_enabled=False)
    control = admit_policy_document(control_source, source_format)
    admitted = admit_policy_document(source, source_format)

    assert isinstance(control, ValidatedEpochDraft)
    assert isinstance(admitted, ValidatedEpochDraft)
    assert admitted.normalized_document_bytes == control.normalized_document_bytes
    assert admitted.compiled_policy_bytes == control.compiled_policy_bytes
    assert json.loads(admitted.compiled_policy_bytes)["dynamicCi"] is None
    assert project_dynamic_ci_planning(admitted) is None


def _sharding_source(
    field: str, token: str, source_format: PolicySourceFormat, *, planning_enabled: bool = True
) -> bytes:
    document = _normalized_policy_document()
    _dynamic(document)["planningEnabled"] = planning_enabled
    profile = _object(_array(_dynamic(document)["executionProfiles"])[0])
    original = _object(profile["shardingPolicy"])[field]
    if source_format == "json":
        text = json.dumps(document)
        prefix = json.dumps(field) + ": "
    else:
        stream = StringIO()
        yaml = YAML(typ="safe", pure=True)
        yaml.default_flow_style = False
        yaml.dump(document, stream)
        text = stream.getvalue()
        prefix = field + ": "
        assert "\n" in text and not text.startswith("{")
    old = prefix + str(original)
    assert text.count(old) == 1
    return text.replace(old, prefix + token, 1).encode("utf-8")


def test_compiler_reports_schema_and_bounded_output_failures_exactly() -> None:
    assert _compiled_structure_diagnostic({}) == PolicyDiagnostic(
        code="compile.output_invalid",
        phase="compile",
        rule_id="schema:required",
        instance_pointer="",
        parameters={"schemaKeyword": "required", "schemaPointer": "/required"},
    )
    limit = 4_194_304
    assert len(_admitted_bytes("a" * (limit - 2))) == limit
    assert _admit_output_bytes("a" * (limit - 1), "compiled-policy") == PolicyDiagnostic(
        code="compile.output_too_large",
        phase="compile",
        rule_id="compile.output-bytes",
        instance_pointer="",
        parameters={"projection": "compiled-policy", "limit": limit, "observed": limit + 1},
    )


@pytest.mark.parametrize(
    ("field", "value", "pointer", "schema_pointer"),
    [
        (
            "policyHash",
            "z" * 64,
            "/dynamicCi/policyHash",
            "/$defs/sha256/pattern",
        ),
        (
            "obligationId",
            "a\u0661",
            "/dynamicCi/obligations/0/obligationId",
            "/$defs/nominalId/pattern",
        ),
    ],
)
def test_compiled_schema_projects_pattern_failures_exactly(
    field: str,
    value: str,
    pointer: str,
    schema_pointer: str,
) -> None:
    compiled = _expected_compiled_policy()
    dynamic = _object(compiled["dynamicCi"])
    if field == "policyHash":
        dynamic[field] = value
    else:
        _object(_array(dynamic["obligations"])[0])[field] = value

    assert _compiled_structure_diagnostic(compiled) == PolicyDiagnostic(
        code="compile.output_invalid",
        phase="compile",
        rule_id="schema:pattern",
        instance_pointer=pointer,
        parameters={"schemaKeyword": "pattern", "schemaPointer": schema_pointer},
    )


def test_compile_policy_rejects_an_invalid_compiler_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compiler_module, "_compile_document", lambda _: {})

    result = compile_policy(
        _normalized_policy_document(), raw_source=b"source", source_format="json"
    )

    assert isinstance(result, PolicyDiagnostic)
    assert result.code == "compile.output_invalid"
    assert result.instance_pointer == ""


def test_catalog_and_nested_sets_compile_to_one_canonical_order() -> None:
    dynamic = _dynamic_policy()
    expected = compile_dynamic_ci(dynamic)
    permuted = deepcopy(dynamic)
    for field in ("obligations", "witnesses", "executionProfiles"):
        _array(permuted[field]).reverse()
    first_obligation = _object(_array(permuted["obligations"])[0])
    _array(first_obligation["requiredWitnessIds"]).reverse()
    first_witness = _object(_array(permuted["witnesses"])[0])
    _array(first_witness["supportedDepths"]).reverse()
    first_profile = _object(_array(permuted["executionProfiles"])[0])
    _array(first_profile["serviceProfileIds"]).reverse()

    assert compile_dynamic_ci(permuted) == expected


def test_dynamic_policy_sets_use_ecmascript_utf16_order() -> None:
    assert sorted_unique_strings(("\ue000", "\U00010000", "\ue000")) == (
        "\U00010000",
        "\ue000",
    )
    dynamic = _dynamic_policy()
    _object(dynamic["dependencyGraph"])["globalRiskPaths"] = [
        "\ue000",
        "\U00010000",
    ]

    compiled = compile_dynamic_ci(dynamic)

    assert compiled["globalRiskPaths"] == ["\U00010000", "\ue000"]


def test_policy_hash_commits_every_policy_fact_and_catalog_hash_only_catalog_facts() -> None:
    baseline = compile_dynamic_ci(_dynamic_policy())
    changed_graph = _dynamic_policy()
    _object(changed_graph["dependencyGraph"])["globalRiskPaths"] = ["other/**"]
    graph_result = compile_dynamic_ci(changed_graph)
    changed_catalog = _dynamic_policy()
    _object(_array(changed_catalog["obligations"])[0])["omitAllowed"] = False
    catalog_result = compile_dynamic_ci(changed_catalog)

    assert (
        graph_result["configuredValidationCatalogHash"]
        == baseline["configuredValidationCatalogHash"]
    )
    assert graph_result["policyHash"] != baseline["policyHash"]
    assert (
        catalog_result["configuredValidationCatalogHash"]
        != baseline["configuredValidationCatalogHash"]
    )
    assert catalog_result["policyHash"] != baseline["policyHash"]


def test_planning_projection_reexports_shared_values_as_one_closed_catalog() -> None:
    result = admit_policy_document(canonical_json(_normalized_policy_document()), "json")
    assert isinstance(result, ValidatedEpochDraft)

    projection = project_dynamic_ci_planning(result)
    assert projection is not None

    assert isinstance(projection.validation_catalog, ValidationCatalog)
    assert all(
        isinstance(item, ValidationObligation) for item in projection.validation_catalog.obligations
    )
    assert all(
        isinstance(item, ExecutableWitness) for item in projection.validation_catalog.witnesses
    )
    assert all(
        isinstance(item, ExecutionProfile)
        for item in projection.validation_catalog.execution_profiles
    )
    assert (
        projection.validation_catalog.catalog_hash
        == compile_dynamic_ci(_dynamic_policy())["configuredValidationCatalogHash"]
    )
    projection.assert_integrity()


def test_planning_projection_accepts_an_unconditional_mandatory_obligation() -> None:
    document = _normalized_policy_document()
    dynamic = _dynamic(document)
    dynamic["riskClasses"] = ["source"]
    obligation = _object(_array(dynamic["obligations"])[0])
    obligation["responsibility"] = {"paths": [], "riskClasses": []}
    obligation["omitAllowed"] = False

    result = admit_policy_document(canonical_json(document), "json")
    assert isinstance(result, ValidatedEpochDraft)

    projection = project_dynamic_ci_planning(result)
    assert projection is not None
    projected = projection.validation_catalog.obligations[0]

    assert projected.responsibility_paths == ()
    assert projected.responsibility_risk_classes == ()
    assert projected.required_witness_ids == ("dependency-witness",)
    projection.assert_integrity()


def test_planning_projection_rejects_a_self_consistent_epoch_with_catalog_hash_drift() -> None:
    result = admit_policy_document(canonical_json(_normalized_policy_document()), "json")
    assert isinstance(result, ValidatedEpochDraft)
    compiled = _object(json.loads(result.compiled_policy_bytes))
    dynamic = _object(compiled["dynamicCi"])
    dynamic["configuredValidationCatalogHash"] = "0" * 64
    compiled_bytes = canonical_json(compiled)
    epoch_hash = compiled_policy_hash(compiled_bytes)
    tampered = replace(
        result,
        compiled_policy_bytes=compiled_bytes,
        epoch_hash=epoch_hash,
        epoch_id=config_epoch_id(
            scope=result.scope,
            source_hash=result.source_hash,
            document_hash=result.document_hash,
            epoch_hash=epoch_hash,
        ),
    )

    with pytest.raises(ValueError, match="catalog hash does not match"):
        project_dynamic_ci_planning(tampered)


def test_planning_projection_integrity_rejects_public_fact_drift() -> None:
    result = admit_policy_document(canonical_json(_normalized_policy_document()), "json")
    assert isinstance(result, ValidatedEpochDraft)
    projection = project_dynamic_ci_planning(result)
    assert projection is not None

    with pytest.raises(ValueError, match="planning projection facts do not match"):
        replace(projection, fallback_timeout_seconds=61).assert_integrity()


def test_compiler_contract_rejects_non_integer_machine_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = deepcopy(contract_document(ContractResource.DOCUMENT_PROFILE))
    _object(profile["outputs"])["maxCompiledPolicyCanonicalBytes"] = 4_194_304.0
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(compiler_module, "contract_document", lambda _: profile)
    compiler_module._contract.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="compiler contract integer invariant failed"):
            compiler_module._contract()
    finally:
        compiler_module._contract.cache_clear()


def test_structural_validator_rejects_machine_keyword_order_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = deepcopy(contract_document(ContractResource.DOCUMENT_PROFILE))
    diagnostics = _object(profile["diagnostics"])
    structural = _object(diagnostics["structuralFailureAlgorithm"])
    _array(structural["keywordOrder"]).remove("pattern")
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(schema_validation_module, "contract_document", lambda _: profile)
    schema_validation_module._admit_contract.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="unsupported structural keyword order"):
            schema_validation_module._admit_contract()
    finally:
        schema_validation_module._admit_contract.cache_clear()


def _expected_compiled_policy() -> dict[str, object]:
    dynamic = compile_dynamic_ci(_dynamic_policy())
    return {
        "schemaVersion": "ci-compiled-repository-policy/v1",
        "scope": {"installationId": 101, "repositoryId": 202},
        "owner": "example-org",
        "name": "ci-coordinator",
        "defaultBranch": "main",
        "rules": [
            {
                "name": "default",
                "event": "push",
                "branches": ["main"],
                "mode": "observe",
                "timing": {
                    "expectedSignalTimeoutSeconds": 3600,
                    "absenceVerificationWindowSeconds": 300,
                    "absencePollLookbackSeconds": 3600,
                    "lateFindingWindowSeconds": 86400,
                    "mutableDecisionWindowSeconds": 300,
                },
                "expectedSignals": [
                    {
                        "kind": "workflow",
                        "name": "Full Check",
                        "workflowFile": "full-check.yml",
                        "source": "native",
                        "requiredConclusion": "success",
                        "required": True,
                    }
                ],
                "omittedSignals": [],
            }
        ],
        "dynamicCi": dynamic,
    }


def _normalized_policy_document() -> dict[str, object]:
    return {
        "schemaVersion": "ci-repository-policy/v1",
        "repository": {
            "installationId": 101,
            "repositoryId": 202,
            "owner": "example-org",
            "name": "ci-coordinator",
            "defaultBranch": "main",
            "rules": [
                {
                    "name": "default",
                    "on": {"event": "push", "branches": ["main"]},
                    "mode": "observe",
                    "timing": {
                        "expectedSignalTimeoutSeconds": 3600,
                        "absenceVerificationWindowSeconds": 300,
                        "absencePollLookbackSeconds": 3600,
                        "lateFindingWindowSeconds": 86400,
                        "mutableDecisionWindowSeconds": 300,
                    },
                    "expectedSignals": [
                        {
                            "kind": "workflow",
                            "name": "Full Check",
                            "workflowFile": "full-check.yml",
                            "source": "native",
                            "requiredConclusion": "success",
                            "required": True,
                        }
                    ],
                    "omittedSignals": [],
                }
            ],
            "dynamicCi": _dynamic_policy(),
        },
    }


def _dynamic_policy() -> dict[str, object]:
    return {
        "planningEnabled": True,
        "policyVersion": "policy-v1",
        "riskClasses": ["dependency", "source"],
        "agentAdvice": {
            "enabled": False,
            "modelIdAllowlist": [],
            "promptHashAllowlist": [],
            "minConfidence": 0.7,
            "promptInjectionEvalRequired": False,
        },
        "dependencyGraph": {"source": "configured", "globalRiskPaths": ["global/**"]},
        "fallbackTimeoutSeconds": 60,
        "obligations": [
            _obligation("dependency-audit", "dependency-witness", "dependency"),
            _obligation("repository-quality", "python-quality", "source"),
        ],
        "witnesses": [
            _witness("dependency-witness", "python-linux"),
            _witness("python-quality", "python-linux"),
        ],
        "executionProfiles": [_execution_profile()],
    }


def _obligation(obligation_id: str, witness_id: str, risk_class: str) -> dict[str, object]:
    return {
        "obligationId": obligation_id,
        "responsibility": {
            "paths": [f"backend/{obligation_id}/**"],
            "riskClasses": [risk_class],
        },
        "requiredWitnessIds": [witness_id],
        "defaultDepth": "standard",
        "fullDepth": "full",
        "omitAllowed": True,
    }


def _witness(witness_id: str, profile_id: str) -> dict[str, object]:
    return {
        "witnessId": witness_id,
        "executionProfileId": profile_id,
        "supportedDepths": ["standard", "full"],
    }


def _execution_profile() -> dict[str, object]:
    return {
        "profileId": "python-linux",
        "runnerProfileId": "ubuntu-24.04",
        "permissionProfileId": "contents-read",
        "credentialProfileId": "none",
        "fixtureProfileId": "none",
        "serviceProfileIds": ["postgres-18.6", "redis-8"],
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


def _admitted_bytes(value: object) -> bytes:
    result = _admit_output_bytes(value, "compiled-policy")
    assert isinstance(result, bytes)
    return result


def _repository(document: dict[str, object]) -> dict[str, object]:
    return _object(document["repository"])


def _dynamic(document: dict[str, object]) -> dict[str, object]:
    return _object(_repository(document)["dynamicCi"])


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _array(value: object) -> list[object]:
    assert isinstance(value, list)
    return value
