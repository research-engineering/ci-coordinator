from __future__ import annotations

import pytest

import ci_coordinator.config_control._rules as rules_module
from ci_coordinator.config_control._rules import normalize_policy_document

from ._document_admission_support import (
    assert_failure,
    normalization_failure,
    valid_policy_document,
)


def test_invalid_document_is_rejected_before_default_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_projection(*_: object) -> object:
        raise AssertionError("invalid input reached default projection")

    monkeypatch.setattr(rules_module, "_project_defaults", forbidden_projection)

    assert_failure(
        normalization_failure({}),
        "structure.invalid",
        parameters={"schemaKeyword": "required", "schemaPointer": "/required"},
    )


def test_nested_structural_diagnostic_has_resolved_schema_pointer() -> None:
    policy = valid_policy_document()
    policy["repository"]["owner"] = ""  # type: ignore[index]

    assert_failure(
        normalization_failure(policy),
        "structure.invalid",
        pointer="/repository/owner",
        parameters={
            "schemaKeyword": "minLength",
            "schemaPointer": "/$defs/nonEmptyString/minLength",
        },
    )


def test_json_schema_integer_domain_includes_integral_number_tokens() -> None:
    policy = valid_policy_document()
    policy["repository"]["installationId"] = 1.0  # type: ignore[index]

    result = normalize_policy_document(policy)

    assert isinstance(result, dict)


def test_recursive_defaults_are_complete_and_do_not_mutate_input() -> None:
    policy = valid_policy_document()
    result = normalize_policy_document(policy)

    assert isinstance(result, dict)
    repository = result["repository"]
    assert isinstance(repository, dict)
    assert repository["dynamicCi"] is None
    rules = repository["rules"]
    assert isinstance(rules, list)
    rule = rules[0]
    assert isinstance(rule, dict)
    assert rule["timing"] == {
        "absencePollLookbackSeconds": 3600,
        "absenceVerificationWindowSeconds": 300,
        "expectedSignalTimeoutSeconds": 3600,
        "lateFindingWindowSeconds": 86400,
        "mutableDecisionWindowSeconds": 300,
    }
    assert rule["omittedSignals"] == []
    assert "dynamicCi" not in policy["repository"]  # type: ignore[operator]
    assert "timing" not in policy["repository"]["rules"][0]  # type: ignore[index]


def test_normalization_revalidates_the_post_default_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rules_module, "_project_defaults", lambda *_: {})

    assert_failure(
        normalization_failure(valid_policy_document()),
        "structure.invalid",
        parameters={"schemaKeyword": "required", "schemaPointer": "/required"},
    )


def test_normalization_enforces_profile_required_pointer_patterns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        rules_module,
        "contract_document",
        lambda _: {"normalization": {"requiredNormalizedPointers": ["/missing"]}},
    )

    with pytest.raises(
        RuntimeError,
        match="normalized document is missing required pointer pattern: /missing",
    ):
        normalize_policy_document(valid_policy_document())


def test_default_lookup_follows_a_terminal_local_reference() -> None:
    assert rules_module._default_annotation(
        {"$ref": "#/$defs/timing"},
        "/synthetic",
    ) == {
        "expectedSignalTimeoutSeconds": 3600,
        "absenceVerificationWindowSeconds": 300,
        "absencePollLookbackSeconds": 3600,
        "lateFindingWindowSeconds": 86400,
        "mutableDecisionWindowSeconds": 300,
    }


def test_dynamic_policy_defaults_recurse_through_objects_and_arrays() -> None:
    policy = valid_policy_document()
    policy["repository"]["dynamicCi"] = {  # type: ignore[index]
        "policyVersion": "v1",
        "obligations": [
            {
                "obligationId": "backend-tests",
                "responsibility": {},
                "requiredWitnessIds": ["python-tests"],
                "defaultDepth": "standard",
                "fullDepth": "full",
                "omitAllowed": False,
            }
        ],
        "witnesses": [
            {
                "witnessId": "python-tests",
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
                "capacityClassId": "hosted-standard",
                "shardingPolicy": {
                    "maxShards": 8,
                    "maxParallel": 4,
                    "maxItemsPerShard": 1000,
                    "setupSecondsPerShard": 15,
                },
            }
        ],
    }

    result = normalize_policy_document(policy)

    assert isinstance(result, dict)
    dynamic = result["repository"]["dynamicCi"]  # type: ignore[index]
    assert dynamic == {
        "agentAdvice": {
            "enabled": False,
            "minConfidence": 0.7,
            "modelIdAllowlist": [],
            "promptHashAllowlist": [],
            "promptInjectionEvalRequired": False,
        },
        "obligations": [
            {
                "obligationId": "backend-tests",
                "defaultDepth": "standard",
                "fullDepth": "full",
                "omitAllowed": False,
                "requiredWitnessIds": ["python-tests"],
                "responsibility": {"paths": [], "riskClasses": []},
            }
        ],
        "witnesses": [
            {
                "executionProfileId": "python-linux",
                "supportedDepths": ["standard", "full"],
                "witnessId": "python-tests",
            }
        ],
        "executionProfiles": [
            {
                "capacityClassId": "hosted-standard",
                "credentialProfileId": "none",
                "fixtureProfileId": "none",
                "permissionProfileId": "contents-read",
                "profileId": "python-linux",
                "runnerProfileId": "ubuntu-24.04",
                "serviceProfileIds": [],
                "shardingPolicy": {
                    "cpuWeight": 1,
                    "maxItemsPerShard": 1000,
                    "maxParallel": 4,
                    "maxShards": 8,
                    "operatorWeight": 1,
                    "setupSecondsPerShard": 15,
                    "wallWeight": 1,
                },
            }
        ],
        "dependencyGraph": {"globalRiskPaths": [], "source": "configured"},
        "fallbackTimeoutSeconds": 60,
        "planningEnabled": False,
        "policyVersion": "v1",
        "riskClasses": [],
    }


def test_default_projection_is_idempotent() -> None:
    first = normalize_policy_document(valid_policy_document())
    assert isinstance(first, dict)

    second = normalize_policy_document(first)

    assert second == first


def test_default_projection_does_not_share_aliases_between_calls() -> None:
    first = normalize_policy_document(valid_policy_document())
    second = normalize_policy_document(valid_policy_document())
    assert isinstance(first, dict)
    assert isinstance(second, dict)

    first_repository = first["repository"]
    second_repository = second["repository"]
    assert isinstance(first_repository, dict)
    assert isinstance(second_repository, dict)
    first_rules = first_repository["rules"]
    second_rules = second_repository["rules"]
    assert isinstance(first_rules, list)
    assert isinstance(second_rules, list)
    first_rule = first_rules[0]
    second_rule = second_rules[0]
    assert isinstance(first_rule, dict)
    assert isinstance(second_rule, dict)
    first_omitted = first_rule["omittedSignals"]
    second_omitted = second_rule["omittedSignals"]
    assert isinstance(first_omitted, list)
    assert isinstance(second_omitted, list)

    first_omitted.append("mutated")

    assert second_omitted == []
