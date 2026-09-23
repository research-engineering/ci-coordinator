"""Owner-intended declaration enumeration independent from provider observation."""

from __future__ import annotations

from ci_coordinator.kernel.hashing import hash_object
from ci_coordinator.kernel.ordering import utf16_sort_key
from ci_coordinator.kernel.strict_json import load_strict_json
from ci_coordinator.validation_contract.model import ExecutionProfile

from ._fields import authority_fields, present
from .model import ObservedCandidate, RegistrationCandidateSet
from .sources import TargetArtifactSources


def enumerate_registration_candidates(
    sources: TargetArtifactSources,
) -> RegistrationCandidateSet:
    if type(sources) is not TargetArtifactSources:
        raise TypeError("registration enumeration requires exact target artifact sources")
    catalog = sources.validation_catalog
    candidates = [
        ObservedCandidate(
            "target_policy:dynamic-ci",
            "target_policy",
            f"config-epoch:{sources.policy.epoch_id}",
            authority_fields(
                "target_policy",
                {
                    "compiledPolicy": present(
                        load_strict_json(
                            sources.policy.compiled_policy_bytes,
                            max_bytes=len(sources.policy.compiled_policy_bytes),
                        )
                    )
                },
            ),
        ),
        ObservedCandidate(
            "target_registry_metadata:registry",
            "target_registry_metadata",
            "target-registry:metadata",
            authority_fields(
                "target_registry_metadata",
                {"definition": present(_registry_metadata(sources))},
            ),
        ),
    ]
    candidates.extend(
        ObservedCandidate(
            f"validation_obligation:{item.obligation_id}",
            "validation_obligation",
            f"validation-catalog:obligations/{item.obligation_id}",
            authority_fields(
                "validation_obligation",
                {"definition": present(item.to_identity_mapping())},
            ),
        )
        for item in catalog.obligations
    )
    candidates.extend(
        ObservedCandidate(
            f"validation_witness:{item.witness_id}",
            "validation_witness",
            f"validation-catalog:witnesses/{item.witness_id}",
            authority_fields(
                "validation_witness",
                {"definition": present(item.to_identity_mapping())},
            ),
        )
        for item in catalog.witnesses
    )
    candidates.extend(_profile_candidate(item) for item in catalog.execution_profiles)
    candidates.extend(
        ObservedCandidate(
            f"target_registry_adapter:{item.path}",
            "target_registry_adapter",
            f"target-registry:adapterFiles/{item.path}",
            authority_fields(
                "target_registry_adapter",
                {"definition": present(item.to_identity_mapping())},
            ),
        )
        for item in sources.registry.adapter_files
    )
    candidates.extend(
        ObservedCandidate(
            f"target_registry_workflow:{item.workflow_path}",
            "target_registry_workflow",
            f"target-registry:workflows/{item.workflow_path}",
            authority_fields(
                "target_registry_workflow",
                {"definition": present(item.to_identity_mapping())},
            ),
        )
        for item in sources.registry.workflows
    )
    candidates.extend(
        ObservedCandidate(
            f"target_registry_profile:{item.profile_id}",
            "target_registry_profile",
            f"target-registry:profiles/{item.profile_id}",
            authority_fields(
                "target_registry_profile",
                {"definition": present(item.to_identity_mapping())},
            ),
        )
        for item in sources.registry.profiles
    )
    source_identity = {
        "profile": sources.lab_profile.to_mapping(),
        "profileId": sources.scenarios.profile_id,
    }
    candidates.extend(
        ObservedCandidate(
            f"consumer_contract_scenario:{sources.scenarios.profile_id}#{item.scenario_id}",
            "consumer_contract_scenario",
            f"consumer-lab:{sources.scenarios.profile_id}/scenarios/{item.scenario_id}",
            authority_fields(
                "consumer_contract_scenario",
                {
                    "definition": present(item.to_mapping()),
                    "sourceIdentity": present(source_identity),
                },
            ),
        )
        for item in sources.scenarios.scenarios
    )
    return RegistrationCandidateSet(
        scope=sources.policy.scope,
        target_artifact_epoch_digest=sources.artifact_epoch_digest,
        target_policy_digest=sources.policy.epoch_hash,
        validation_catalog_digest=catalog.catalog_hash,
        target_registry_digest=sources.registry.registry_hash,
        candidates=tuple(sorted(candidates, key=lambda item: utf16_sort_key(item.candidate_id))),
    )


def _profile_candidate(profile: ExecutionProfile) -> ObservedCandidate:
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


def _registry_metadata(sources: TargetArtifactSources) -> dict[str, object]:
    registry = sources.registry
    return {
        "schemaVersion": "dynamic-ci-target-execution-registry/v1",
        "generator": {"id": registry.generator_id, "version": registry.generator_version},
        "registryHash": registry.registry_hash,
    }
