from __future__ import annotations

from copy import deepcopy

import pytest

from ci_coordinator.config_control._resources import ContractResource, contract_document
from ci_coordinator.config_control._semantics import validate_policy_semantics
from ci_coordinator.config_control.contracts import PolicyDiagnostic

EXPECTED_POINTERS = {
    "string.canonical-whitespace": "/repository/owner",
    "string.ascii-case-key": "/repository/rules/0/expectedSignals/0/workflowFile",
    "identity.valid-nominal-id": "/repository/dynamicCi/obligations/0/obligationId",
    "rule.unique-name": "/repository/rules/1/name",
    "rule.unique-branch": "/repository/rules/0/on/branches/1",
    "rule.default-branch-covered": "/repository/rules/0/on/branches",
    "rule.dispatch-disabled": "/repository/rules/0/mode",
    "timing.mutable-after-absence": "/repository/rules/0/timing/mutableDecisionWindowSeconds",
    "timing.late-after-mutable": "/repository/rules/0/timing/lateFindingWindowSeconds",
    "signal.required": "/repository/rules/0/expectedSignals/0/required",
    "signal.success-conclusion": "/repository/rules/0/expectedSignals/0/requiredConclusion",
    "signal.expected-omitted-disjoint": "/repository/rules/0/omittedSignals/0",
    "signal.hybrid-verifies-absence": "/repository/rules/0/omittedSignals/0/verifyAbsence",
    "list.unique-value": "/repository/dynamicCi/riskClasses/1",
    "agent.enabled-model-required": "/repository/dynamicCi/agentAdvice/modelIdAllowlist",
    "agent.enabled-prompt-required": "/repository/dynamicCi/agentAdvice/promptHashAllowlist",
    "agent.injection-eval-required": (
        "/repository/dynamicCi/agentAdvice/promptInjectionEvalRequired"
    ),
    "path.valid-pattern": "/repository/dynamicCi/dependencyGraph/globalRiskPaths/0",
    "obligation.unique-id": "/repository/dynamicCi/obligations/1/obligationId",
    "witness.unique-id": "/repository/dynamicCi/witnesses/1/witnessId",
    "profile.unique-id": "/repository/dynamicCi/executionProfiles/1/profileId",
    "obligation.depth-monotonic": "/repository/dynamicCi/obligations/0/fullDepth",
    "obligation.omission-has-responsibility": (
        "/repository/dynamicCi/obligations/0/responsibility"
    ),
    "obligation.known-risk-class": (
        "/repository/dynamicCi/obligations/0/responsibility/riskClasses/0"
    ),
    "obligation.known-witness": ("/repository/dynamicCi/obligations/0/requiredWitnessIds/0"),
    "obligation.default-depth-supported": ("/repository/dynamicCi/obligations/0/defaultDepth"),
    "obligation.full-depth-supported": "/repository/dynamicCi/obligations/0/fullDepth",
    "witness.known-execution-profile": ("/repository/dynamicCi/witnesses/0/executionProfileId"),
    "catalog.no-orphan-risk-class": "/repository/dynamicCi/riskClasses/1",
    "catalog.no-orphan-witness": "/repository/dynamicCi/witnesses/1/witnessId",
    "catalog.no-orphan-profile": "/repository/dynamicCi/executionProfiles/1/profileId",
    "profile.parallelism-bounded": (
        "/repository/dynamicCi/executionProfiles/0/shardingPolicy/maxParallel"
    ),
    "profile.objective-nonzero": ("/repository/dynamicCi/executionProfiles/0/shardingPolicy"),
}


def test_semantic_case_inventory_equals_the_machine_profile() -> None:
    profile = contract_document(ContractResource.SEMANTIC_PROFILE)

    assert set(EXPECTED_POINTERS) == set(_object(profile["rules"]))


@pytest.mark.parametrize("rule_id", tuple(EXPECTED_POINTERS))
def test_every_semantic_rule_has_an_exact_falsifier(rule_id: str) -> None:
    document = valid_semantic_document()
    apply_violation(document, rule_id)

    failure = validate_policy_semantics(document)

    assert isinstance(failure, PolicyDiagnostic)
    assert failure.rule_id == rule_id
    assert failure.instance_pointer == EXPECTED_POINTERS[rule_id]
    assert failure.code == (
        "semantics.non_canonical_string"
        if rule_id == "string.canonical-whitespace"
        else "semantics.invalid"
    )
    assert dict(failure.parameters) == {}


def test_valid_and_null_dynamic_policy_are_semantically_admitted() -> None:
    assert validate_policy_semantics(valid_semantic_document()) is None
    document = valid_semantic_document()
    _repository(document)["dynamicCi"] = None

    assert validate_policy_semantics(document) is None


def test_signal_collision_key_is_ascii_case_insensitive() -> None:
    document = valid_semantic_document()
    rule = _object(_array(_repository(document)["rules"])[0])
    omitted = _omitted_signal(name="full check")
    omitted["workflowFile"] = "FULL-CHECK.YML"
    rule["omittedSignals"] = [omitted]

    failure = validate_policy_semantics(document)

    assert isinstance(failure, PolicyDiagnostic)
    assert failure.rule_id == "signal.expected-omitted-disjoint"
    assert failure.instance_pointer == "/repository/rules/0/omittedSignals/0"


@pytest.mark.parametrize(
    ("paths", "risk_classes", "omit_allowed"),
    [
        ([], ["source"], True),
        (["backend/**"], [], True),
        ([], [], False),
    ],
)
def test_responsibility_surface_admits_exactly_the_nonempty_or_mandatory_cases(
    paths: list[str],
    risk_classes: list[str],
    omit_allowed: bool,
) -> None:
    document = valid_semantic_document()
    dynamic = _dynamic(document)
    dynamic["riskClasses"] = risk_classes.copy()
    obligation = _obligation(document)
    obligation["responsibility"] = {
        "paths": paths.copy(),
        "riskClasses": risk_classes.copy(),
    }
    obligation["omitAllowed"] = omit_allowed

    assert validate_policy_semantics(document) is None


def test_semantic_validation_does_not_mutate_the_normalized_document() -> None:
    document = valid_semantic_document()
    apply_violation(document, "obligation.known-witness")
    snapshot = deepcopy(document)

    validate_policy_semantics(document)

    assert document == snapshot


def test_semantic_failure_selection_prefers_the_earliest_pointer() -> None:
    document = valid_semantic_document()
    _repository(document)["defaultBranch"] = " main"
    _object(_profile(document)["shardingPolicy"])["maxParallel"] = 9

    failure = validate_policy_semantics(document)

    assert isinstance(failure, PolicyDiagnostic)
    assert failure.rule_id == "string.canonical-whitespace"
    assert failure.instance_pointer == "/repository/defaultBranch"


def test_semantic_failure_selection_prefers_code_order_at_one_pointer() -> None:
    document = valid_semantic_document()
    _global_risk_paths(document)[:] = ["../private "]

    failure = validate_policy_semantics(document)

    assert isinstance(failure, PolicyDiagnostic)
    assert failure.rule_id == "string.canonical-whitespace"
    assert failure.instance_pointer == "/repository/dynamicCi/dependencyGraph/globalRiskPaths/0"


def test_semantic_pointer_order_compares_array_indices_numerically() -> None:
    document = valid_semantic_document()
    obligations = _array(_dynamic(document)["obligations"])
    template = _object(obligations[0])
    for index in range(1, 11):
        obligation = deepcopy(template)
        obligation["obligationId"] = f"repository-quality-{index}"
        obligations.append(obligation)
    for index in (2, 10):
        responsibility = _object(_object(obligations[index])["responsibility"])
        responsibility["paths"] = ["../private"]

    failure = validate_policy_semantics(document)

    assert isinstance(failure, PolicyDiagnostic)
    assert failure.rule_id == "path.valid-pattern"
    assert failure.instance_pointer == "/repository/dynamicCi/obligations/2/responsibility/paths/0"


@pytest.mark.parametrize(
    "identifier",
    ["a", "python_3.14", "ubuntu-24.04", "a--b", "a."],
)
def test_nominal_identifier_language_admits_the_shared_domain(identifier: str) -> None:
    document = valid_semantic_document()
    _obligation(document)["obligationId"] = identifier

    assert validate_policy_semantics(document) is None


@pytest.mark.parametrize(
    "identifier",
    ["Upper", "1runner", "runner/name", "runn\u00e9r", "a\u0661", "a" * 65],
)
def test_nominal_identifier_language_rejects_values_outside_the_shared_domain(
    identifier: str,
) -> None:
    document = valid_semantic_document()
    _obligation(document)["obligationId"] = identifier

    failure = validate_policy_semantics(document)

    assert isinstance(failure, PolicyDiagnostic)
    assert failure.rule_id == "identity.valid-nominal-id"


@pytest.mark.parametrize(
    "pattern",
    ["backend/**", "**/*.py", "src/{api,app}/?.py", "prefix*suffix", "literal,comma"],
)
def test_path_language_admits_every_operator_family(pattern: str) -> None:
    document = valid_semantic_document()
    _global_risk_paths(document)[:] = [pattern]

    assert validate_policy_semantics(document) is None


@pytest.mark.parametrize(
    "pattern",
    [
        "/absolute",
        "./relative",
        "parent/../private",
        "windows\\path",
        "class/[abc]",
        "negated/!value",
        "group/(a)",
        "plus/a+b",
        "extglob/@value",
        "brace/{a",
        "brace/a}",
        "brace/{a}",
        "brace/{,b}",
        "brace/{a,}",
        "brace/{a,,b}",
        "brace/{a,b*}",
        "brace/{a,{b,c}}",
        "brace/{a,[b]}",
        "brace/{a,!b}",
        "brace/{a,(b)}",
        "brace/{a,b+c}",
        "brace/{a,@b}",
    ],
)
def test_path_language_rejects_forbidden_grammar(pattern: str) -> None:
    document = valid_semantic_document()
    _global_risk_paths(document)[:] = [pattern]

    failure = validate_policy_semantics(document)

    assert isinstance(failure, PolicyDiagnostic)
    assert failure.rule_id == "path.valid-pattern"


def test_path_language_applies_to_obligation_responsibility() -> None:
    document = valid_semantic_document()
    responsibility = _object(_obligation(document)["responsibility"])
    responsibility["paths"] = ["brace/{a,[b]}"]

    failure = validate_policy_semantics(document)

    assert isinstance(failure, PolicyDiagnostic)
    assert failure.rule_id == "path.valid-pattern"
    assert failure.instance_pointer == (
        "/repository/dynamicCi/obligations/0/responsibility/paths/0"
    )


@pytest.mark.parametrize(
    "code_point",
    [
        *range(0x0009, 0x000E),
        0x0020,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
        0xFEFF,
    ],
)
def test_canonical_string_rejects_every_profiled_trim_endpoint(code_point: int) -> None:
    document = valid_semantic_document()
    _repository(document)["owner"] = f"{chr(code_point)}owner"

    failure = validate_policy_semantics(document)

    assert isinstance(failure, PolicyDiagnostic)
    assert failure.rule_id == "string.canonical-whitespace"
    assert failure.instance_pointer == "/repository/owner"


def test_canonical_string_allows_profiled_trim_code_points_in_the_interior() -> None:
    document = valid_semantic_document()
    _repository(document)["owner"] = "owner\u00a0name"

    assert validate_policy_semantics(document) is None


def valid_semantic_document() -> dict[str, object]:
    return {
        "schemaVersion": "ci-repository-policy/v1",
        "repository": {
            "installationId": 1,
            "repositoryId": 2,
            "owner": "owner",
            "name": "repository",
            "defaultBranch": "main",
            "rules": [_rule()],
            "dynamicCi": _dynamic_policy(),
        },
    }


def apply_violation(document: dict[str, object], rule_id: str) -> None:
    repository = _repository(document)
    rules = _array(repository["rules"])
    rule = _object(rules[0])
    dynamic = _dynamic(document)
    obligation = _obligation(document)
    witness = _witness(document)
    profile = _profile(document)
    if rule_id == "string.canonical-whitespace":
        repository["owner"] = " owner"
    elif rule_id == "string.ascii-case-key":
        _expected_signal(rule)["workflowFile"] = "qualit\u00e9.yml"
    elif rule_id == "identity.valid-nominal-id":
        obligation["obligationId"] = "Invalid"
    elif rule_id == "rule.unique-name":
        rules.append(deepcopy(rule))
    elif rule_id == "rule.unique-branch":
        _array(_object(rule["on"])["branches"]).append("main")
    elif rule_id == "rule.default-branch-covered":
        _object(rule["on"])["branches"] = ["other"]
    elif rule_id == "rule.dispatch-disabled":
        rule["mode"] = "dispatch"
    elif rule_id == "timing.mutable-after-absence":
        _object(rule["timing"])["mutableDecisionWindowSeconds"] = 1
    elif rule_id == "timing.late-after-mutable":
        _object(rule["timing"])["lateFindingWindowSeconds"] = 1
    elif rule_id == "signal.required":
        _expected_signal(rule)["required"] = False
    elif rule_id == "signal.success-conclusion":
        _expected_signal(rule)["requiredConclusion"] = "failure"
    elif rule_id == "signal.expected-omitted-disjoint":
        rule["omittedSignals"] = [_omitted_signal()]
    elif rule_id == "signal.hybrid-verifies-absence":
        rule["mode"] = "hybrid"
        rule["omittedSignals"] = [_omitted_signal(name="Other", verify=False)]
    elif rule_id == "list.unique-value":
        _array(dynamic["riskClasses"]).append("source")
    elif rule_id.startswith("agent."):
        advice = _object(dynamic["agentAdvice"])
        advice["enabled"] = True
        if rule_id == "agent.enabled-model-required":
            advice["modelIdAllowlist"] = []
        elif rule_id == "agent.enabled-prompt-required":
            advice["promptHashAllowlist"] = []
        else:
            advice["promptInjectionEvalRequired"] = False
    elif rule_id == "path.valid-pattern":
        _global_risk_paths(document)[:] = ["../private"]
    elif rule_id == "obligation.unique-id":
        _array(dynamic["obligations"]).append(deepcopy(obligation))
    elif rule_id == "witness.unique-id":
        _array(dynamic["witnesses"]).append(deepcopy(witness))
    elif rule_id == "profile.unique-id":
        _array(dynamic["executionProfiles"]).append(deepcopy(profile))
    elif rule_id == "obligation.depth-monotonic":
        obligation["defaultDepth"], obligation["fullDepth"] = "full", "standard"
    elif rule_id == "obligation.omission-has-responsibility":
        obligation["responsibility"] = {"paths": [], "riskClasses": []}
    elif rule_id == "obligation.known-risk-class":
        _object(obligation["responsibility"])["riskClasses"] = ["unknown"]
    elif rule_id == "obligation.known-witness":
        obligation["requiredWitnessIds"] = ["unknown"]
    elif rule_id == "obligation.default-depth-supported":
        obligation["defaultDepth"] = "targeted"
    elif rule_id == "obligation.full-depth-supported":
        obligation["fullDepth"] = "exhaustive"
    elif rule_id == "witness.known-execution-profile":
        sibling = deepcopy(witness)
        sibling["witnessId"] = "auxiliary"
        _array(dynamic["witnesses"]).append(sibling)
        _array(obligation["requiredWitnessIds"]).append("auxiliary")
        witness["executionProfileId"] = "unknown"
    elif rule_id == "catalog.no-orphan-risk-class":
        _array(dynamic["riskClasses"]).append("unused")
    elif rule_id == "catalog.no-orphan-witness":
        orphan = deepcopy(witness)
        orphan["witnessId"] = "unused"
        _array(dynamic["witnesses"]).append(orphan)
    elif rule_id == "catalog.no-orphan-profile":
        orphan = deepcopy(profile)
        orphan["profileId"] = "unused"
        _array(dynamic["executionProfiles"]).append(orphan)
    elif rule_id == "profile.parallelism-bounded":
        _object(profile["shardingPolicy"])["maxParallel"] = 9
    elif rule_id == "profile.objective-nonzero":
        sharding = _object(profile["shardingPolicy"])
        for field in ("cpuWeight", "wallWeight", "operatorWeight"):
            sharding[field] = 0
    else:
        raise AssertionError(f"missing falsifier for {rule_id}")


def _dynamic_policy() -> dict[str, object]:
    return {
        "planningEnabled": True,
        "policyVersion": "policy-v1",
        "riskClasses": ["source"],
        "agentAdvice": {
            "enabled": False,
            "modelIdAllowlist": ["model"],
            "promptHashAllowlist": ["e" * 64],
            "minConfidence": 0.7,
            "promptInjectionEvalRequired": True,
        },
        "dependencyGraph": {"source": "configured", "globalRiskPaths": []},
        "fallbackTimeoutSeconds": 60,
        "obligations": [
            {
                "obligationId": "repository-quality",
                "responsibility": {"paths": ["backend/**"], "riskClasses": ["source"]},
                "requiredWitnessIds": ["python-quality"],
                "defaultDepth": "standard",
                "fullDepth": "full",
                "omitAllowed": True,
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


def _rule() -> dict[str, object]:
    return {
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


def _omitted_signal(*, name: str = "Full Check", verify: bool = True) -> dict[str, object]:
    return {
        "kind": "workflow",
        "name": name,
        "workflowFile": "full-check.yml",
        "verifyAbsence": verify,
        "reason": "not expected",
    }


def _repository(document: dict[str, object]) -> dict[str, object]:
    return _object(document["repository"])


def _dynamic(document: dict[str, object]) -> dict[str, object]:
    return _object(_repository(document)["dynamicCi"])


def _obligation(document: dict[str, object]) -> dict[str, object]:
    return _object(_array(_dynamic(document)["obligations"])[0])


def _witness(document: dict[str, object]) -> dict[str, object]:
    return _object(_array(_dynamic(document)["witnesses"])[0])


def _profile(document: dict[str, object]) -> dict[str, object]:
    return _object(_array(_dynamic(document)["executionProfiles"])[0])


def _global_risk_paths(document: dict[str, object]) -> list[object]:
    return _array(_object(_dynamic(document)["dependencyGraph"])["globalRiskPaths"])


def _expected_signal(rule: dict[str, object]) -> dict[str, object]:
    return _object(_array(rule["expectedSignals"])[0])


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _array(value: object) -> list[object]:
    assert isinstance(value, list)
    return value
