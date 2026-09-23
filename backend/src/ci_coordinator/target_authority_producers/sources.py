"""Subject- and epoch-closed source groups for independent producers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ci_coordinator.config_control.contracts import ValidatedEpochDraft
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.consumer_contract_lab.model import ConsumerLabProfile, ScenarioCorpus
from ci_coordinator.execution_orchestration.target_registry import TargetExecutionRegistry
from ci_coordinator.governance_observation.model import GovernanceState
from ci_coordinator.kernel.canonical_json import canonical_json
from ci_coordinator.kernel.hashing import hash_object, sha256_hex
from ci_coordinator.kernel.strict_json import load_strict_json
from ci_coordinator.repo_context.workflow_inventory import ProviderWorkflowInventoryEvidence
from ci_coordinator.validation_contract.catalog import ValidationCatalog
from ci_coordinator.workflow_authority.model import WorkflowAuthorityRepository

from .model import TargetAuthorityProducerError


@dataclass(frozen=True, slots=True)
class TargetArtifactSources:
    """One value-closed target policy, registry, and consumer-lab epoch."""

    policy: ValidatedEpochDraft
    registry: TargetExecutionRegistry
    lab_profile: ConsumerLabProfile
    scenarios: ScenarioCorpus

    def __post_init__(self) -> None:
        exact_types = (
            (self.policy, ValidatedEpochDraft),
            (self.registry, TargetExecutionRegistry),
            (self.lab_profile, ConsumerLabProfile),
            (self.scenarios, ScenarioCorpus),
        )
        if any(type(value) is not expected for value, expected in exact_types):
            raise TypeError("target artifact sources require exact admitted values")
        projection = project_dynamic_ci_planning(self.policy)
        if projection is None:
            raise TargetAuthorityProducerError(
                "dynamic_policy_absent",
                "target authority requires an admitted dynamic CI policy",
            )
        projection.assert_integrity()
        if not self.registry.admits(projection.validation_catalog):
            raise TargetAuthorityProducerError(
                "registry_catalog_mismatch",
                "target registry does not exactly bind the validation catalog",
            )
        _require_repository_identity(self.policy, self.lab_profile)
        _require_consumer_contract_bindings(self)
        if (
            self.registry.generator_version != self.lab_profile.expected_coordinator_commit
            or self.registry.workflow(self.lab_profile.workflow_path) is None
        ):
            raise TargetAuthorityProducerError(
                "consumer_registry_mismatch",
                "consumer profile and registry do not share one generator and workflow epoch",
            )

    @property
    def validation_catalog(self) -> ValidationCatalog:
        projection = project_dynamic_ci_planning(self.policy)
        if projection is None:
            raise AssertionError("validated target artifact epoch lost its dynamic policy")
        return projection.validation_catalog

    @property
    def artifact_epoch_digest(self) -> str:
        return hash_object(self.to_identity_mapping())

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-coordinator.target-artifact-sources/v1",
            "policy": {
                "sourceHash": self.policy.source_hash,
                "documentHash": self.policy.document_hash,
                "epochHash": self.policy.epoch_hash,
                "epochId": self.policy.epoch_id,
            },
            "validationCatalogDigest": self.validation_catalog.catalog_hash,
            "targetRegistryDigest": self.registry.registry_hash,
            "consumerProfile": self.lab_profile.to_mapping(),
            "scenarioCorpus": self.scenarios.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class ProviderAuthoritySources:
    """Subject-bound provider state with an explicit best-effort evidence class."""

    workflows: ProviderWorkflowInventoryEvidence
    governance: GovernanceState

    def __post_init__(self) -> None:
        if type(self.workflows) is not ProviderWorkflowInventoryEvidence:
            raise TypeError("provider authority requires exact workflow inventory evidence")
        if type(self.governance) is not GovernanceState:
            raise TypeError("provider authority requires an exact governance state")
        repository = self.governance.repository
        workflow = self.workflows
        if (
            repository.scope != workflow.scope
            or repository.owner != workflow.owner
            or repository.name != workflow.name
            or repository.default_branch != workflow.default_branch
            or self.governance.api_version != workflow.api_version
        ):
            raise TargetAuthorityProducerError(
                "provider_authority_subject_mismatch",
                "provider workflow and governance evidence cross subject or API epoch",
            )

    @property
    def repository(self) -> WorkflowAuthorityRepository:
        return WorkflowAuthorityRepository(
            self.workflows.scope,
            self.workflows.owner,
            self.workflows.name,
            self.workflows.default_branch,
        )

    @property
    def authority_digest(self) -> str:
        return hash_object(self.to_stable_mapping())

    @property
    def evidence_digest(self) -> str:
        return hash_object(self.to_identity_mapping())

    def to_stable_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-coordinator.provider-authority-sources/v1",
            "evidenceClass": "best_effort_unbaselined",
            "workflowInventory": self.workflows.to_stable_mapping(),
            "governance": self.governance.identity_mapping(),
        }

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            **self.to_stable_mapping(),
            "workflowInventory": self.workflows.to_identity_mapping(),
        }


def _require_repository_identity(
    policy: ValidatedEpochDraft,
    profile: ConsumerLabProfile,
) -> None:
    value = load_strict_json(
        policy.normalized_document_bytes,
        max_bytes=len(policy.normalized_document_bytes),
    )
    if type(value) is not dict:
        raise TargetAuthorityProducerError(
            "policy_repository_missing",
            "normalized target policy lacks its repository identity",
        )
    repository_value = value.get("repository")
    if type(repository_value) is not dict:
        raise TargetAuthorityProducerError(
            "policy_repository_missing",
            "normalized target policy lacks its repository identity",
        )
    repository = cast(dict[str, object], repository_value)
    consumer = profile.repository
    if (
        policy.scope.installation_id != consumer.installation_id
        or policy.scope.repository_id != consumer.repository_id
        or repository.get("owner") != consumer.owner
        or repository.get("name") != consumer.name
        or repository.get("defaultBranch") != consumer.default_branch
    ):
        raise TargetAuthorityProducerError(
            "target_artifact_repository_mismatch",
            "target policy and consumer profile cross repository identity",
        )


def _require_consumer_contract_bindings(sources: TargetArtifactSources) -> None:
    profile = sources.lab_profile
    corpus = sources.scenarios
    corpus_bytes = canonical_json(corpus.to_mapping()) + b"\n"
    if (
        sha256_hex(sources.policy.source_bytes) != profile.dynamic_policy.sha256
        or sha256_hex(corpus_bytes) != profile.scenario_corpus.sha256
        or corpus.profile_id != profile.profile_id
        or {scenario.event_name for scenario in corpus.scenarios} != set(profile.event_surface)
        or {scenario.expected_outcome.mode for scenario in corpus.scenarios}
        != {"fallback", "selected"}
    ):
        raise TargetAuthorityProducerError(
            "consumer_contract_epoch_mismatch",
            "consumer profile, policy, and scenario corpus do not form one closed epoch",
        )
