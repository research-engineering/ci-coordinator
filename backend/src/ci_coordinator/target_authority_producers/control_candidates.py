"""Independent policy, catalog, registry, lab, and provider candidates."""

from __future__ import annotations

from typing import cast

from ci_coordinator.config_control.contracts import ValidatedEpochDraft
from ci_coordinator.consumer_contract_lab.model import ConsumerLabProfile, ScenarioCorpus
from ci_coordinator.execution_orchestration.target_registry import TargetExecutionRegistry
from ci_coordinator.governance_observation.model import EffectiveGovernanceRule, GovernanceState
from ci_coordinator.kernel.hashing import hash_object
from ci_coordinator.kernel.strict_json import load_strict_json
from ci_coordinator.target_authority_relation import AuthorityField
from ci_coordinator.validation_contract.catalog import ValidationCatalog
from ci_coordinator.validation_contract.model import ExecutionProfile

from ._fields import authority_fields, not_applicable, present, unknown
from .model import ObservedCandidate


def enumerate_control_candidates(
    *,
    policy: ValidatedEpochDraft,
    catalog: ValidationCatalog,
    registry: TargetExecutionRegistry,
    lab_profile: ConsumerLabProfile,
    scenarios: ScenarioCorpus,
    governance: GovernanceState,
) -> tuple[ObservedCandidate, ...]:
    candidates = [
        _policy_candidate(policy),
        _registry_metadata_candidate(registry),
        _provider_repository_candidate(governance),
    ]
    candidates.extend(_catalog_candidates(catalog))
    candidates.extend(_registry_candidates(registry))
    candidates.extend(_scenario_candidates(lab_profile, scenarios))
    candidates.extend(_provider_gate_candidates(governance))
    return tuple(candidates)


def _policy_candidate(policy: ValidatedEpochDraft) -> ObservedCandidate:
    compiled = load_strict_json(
        policy.compiled_policy_bytes,
        max_bytes=len(policy.compiled_policy_bytes),
    )
    return ObservedCandidate(
        "target_policy:dynamic-ci",
        "target_policy",
        f"config-epoch:{policy.epoch_id}",
        authority_fields("target_policy", {"compiledPolicy": present(compiled)}),
    )


def _catalog_candidates(catalog: ValidationCatalog) -> tuple[ObservedCandidate, ...]:
    obligations = tuple(
        ObservedCandidate(
            f"validation_obligation:{obligation.obligation_id}",
            "validation_obligation",
            f"validation-catalog:obligations/{obligation.obligation_id}",
            authority_fields(
                "validation_obligation",
                {"definition": present(obligation.to_identity_mapping())},
            ),
        )
        for obligation in catalog.obligations
    )
    witnesses = tuple(
        ObservedCandidate(
            f"validation_witness:{witness.witness_id}",
            "validation_witness",
            f"validation-catalog:witnesses/{witness.witness_id}",
            authority_fields(
                "validation_witness",
                {"definition": present(witness.to_identity_mapping())},
            ),
        )
        for witness in catalog.witnesses
    )
    profiles = tuple(
        _validation_profile_candidate(profile) for profile in catalog.execution_profiles
    )
    return (*obligations, *witnesses, *profiles)


def _validation_profile_candidate(profile: ExecutionProfile) -> ObservedCandidate:
    definition = profile.to_identity_mapping()
    return ObservedCandidate(
        f"validation_profile:{profile.profile_id}",
        "validation_profile",
        f"validation-catalog:executionProfiles/{profile.profile_id}",
        authority_fields(
            "validation_profile",
            {
                "definition": present(definition),
                "evidenceIdentity": present(hash_object(definition)),
            },
        ),
    )


def _registry_metadata_candidate(registry: TargetExecutionRegistry) -> ObservedCandidate:
    return ObservedCandidate(
        "target_registry_metadata:registry",
        "target_registry_metadata",
        "target-registry:metadata",
        authority_fields(
            "target_registry_metadata",
            {
                "definition": present(
                    {
                        "schemaVersion": "dynamic-ci-target-execution-registry/v1",
                        "generator": {
                            "id": registry.generator_id,
                            "version": registry.generator_version,
                        },
                        "registryHash": registry.registry_hash,
                    }
                )
            },
        ),
    )


def _registry_candidates(registry: TargetExecutionRegistry) -> tuple[ObservedCandidate, ...]:
    adapters = tuple(
        ObservedCandidate(
            f"target_registry_adapter:{adapter.path}",
            "target_registry_adapter",
            f"target-registry:adapterFiles/{adapter.path}",
            authority_fields(
                "target_registry_adapter",
                {"definition": present(adapter.to_identity_mapping())},
            ),
        )
        for adapter in registry.adapter_files
    )
    workflows = tuple(
        ObservedCandidate(
            f"target_registry_workflow:{workflow.workflow_path}",
            "target_registry_workflow",
            f"target-registry:workflows/{workflow.workflow_path}",
            authority_fields(
                "target_registry_workflow",
                {"definition": present(workflow.to_identity_mapping())},
            ),
        )
        for workflow in registry.workflows
    )
    profiles = tuple(
        ObservedCandidate(
            f"target_registry_profile:{profile.profile_id}",
            "target_registry_profile",
            f"target-registry:profiles/{profile.profile_id}",
            authority_fields(
                "target_registry_profile",
                {"definition": present(profile.to_identity_mapping())},
            ),
        )
        for profile in registry.profiles
    )
    return (*adapters, *workflows, *profiles)


def _scenario_candidates(
    profile: ConsumerLabProfile,
    corpus: ScenarioCorpus,
) -> tuple[ObservedCandidate, ...]:
    source_identity = {
        "profile": profile.to_mapping(),
        "profileId": corpus.profile_id,
    }
    return tuple(
        ObservedCandidate(
            f"consumer_contract_scenario:{corpus.profile_id}#{scenario.scenario_id}",
            "consumer_contract_scenario",
            f"consumer-lab:{corpus.profile_id}/scenarios/{scenario.scenario_id}",
            authority_fields(
                "consumer_contract_scenario",
                {
                    "definition": present(scenario.to_mapping()),
                    "sourceIdentity": present(source_identity),
                },
            ),
        )
        for scenario in corpus.scenarios
    )


def _provider_repository_candidate(governance: GovernanceState) -> ObservedCandidate:
    repository = governance.repository
    return ObservedCandidate(
        f"provider_repository:{repository.full_name}",
        "provider_repository",
        f"github:repositories/{repository.scope.repository_id}",
        authority_fields(
            "provider_repository",
            {
                "apiVersion": present(governance.api_version),
                "defaultBranch": present(repository.default_branch),
                "repositoryIdentity": present(
                    {
                        "installationId": repository.scope.installation_id,
                        "repositoryId": repository.scope.repository_id,
                        "ownerId": repository.owner_id,
                        "owner": repository.owner,
                        "name": repository.name,
                        "fullName": repository.full_name,
                    }
                ),
            },
        ),
    )


def _provider_gate_candidates(governance: GovernanceState) -> tuple[ObservedCandidate, ...]:
    return tuple(_provider_gate_candidate(governance, rule) for rule in governance.rules)


def _provider_gate_candidate(
    governance: GovernanceState,
    rule: EffectiveGovernanceRule,
) -> ObservedCandidate:
    value = load_strict_json(rule.canonical_json, max_bytes=len(rule.canonical_json))
    mapping = cast(dict[str, object], value)
    parameters = mapping.get("parameters")
    required_check_field, required_checks = _required_checks(rule, parameters)
    check_apps = _check_app_bindings(required_checks)
    member_id = (
        f"{rule.ruleset_source_type}:{rule.ruleset_source}:{rule.ruleset_id}:{rule.rule_type}"
    )
    return ObservedCandidate(
        f"provider_gate:{member_id}",
        "provider_gate",
        f"github:effective-rules/{member_id}",
        authority_fields(
            "provider_gate",
            {
                "bypassActors": unknown(
                    "effective branch rules do not disclose ruleset bypass actors"
                ),
                "checkAppBinding": (
                    not_applicable()
                    if required_checks is None and rule.rule_type != "required_status_checks"
                    else (
                        unknown("required-status-check app bindings are not closed")
                        if check_apps is None
                        else present(check_apps)
                    )
                ),
                "enforcementState": present(
                    {
                        "state": "effective",
                        "defaultBranch": governance.repository.default_branch,
                    }
                ),
                "mergeQueueRelation": _merge_queue_relation(rule, parameters),
                "providerProjection": present(mapping),
                "requiredCheckIdentity": required_check_field,
            },
        ),
    )


def _required_checks(
    rule: EffectiveGovernanceRule,
    parameters: object,
) -> tuple[AuthorityField, list[object] | None]:
    if rule.rule_type != "required_status_checks":
        return not_applicable(), None
    if type(parameters) is not dict:
        return unknown("required-status-check parameters are not an object"), None
    checks = parameters.get("required_status_checks")
    if type(checks) is not list:
        return unknown("required-status-check identities are absent"), None
    typed_checks = cast(list[object], checks)
    return present(typed_checks), typed_checks


def _check_app_bindings(required_checks: list[object] | None) -> list[int] | None:
    if required_checks is None:
        return None
    if any(
        type(check) is not dict
        or type(check.get("context")) is not str
        or (
            check.get("integration_id") is not None and type(check.get("integration_id")) is not int
        )
        for check in required_checks
    ):
        return None
    bindings = [
        check["integration_id"]
        for check in required_checks
        if type(check) is dict and type(check.get("integration_id")) is int
    ]
    return sorted(set(bindings))


def _merge_queue_relation(
    rule: EffectiveGovernanceRule,
    parameters: object,
) -> AuthorityField:
    if rule.rule_type != "merge_queue":
        return not_applicable()
    if type(parameters) is not dict:
        return unknown("merge-queue parameters are not an object")
    return present(parameters)
