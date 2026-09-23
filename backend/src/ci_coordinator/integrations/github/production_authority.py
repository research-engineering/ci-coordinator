"""Bounded current GitHub inputs; no source cache supplies mutable authority."""

from ci_coordinator.governance_observation.model import GovernanceState
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.governance_observation import (
    GitHubGovernanceObservationReader,
)
from ci_coordinator.integrations.github.workflow_authority import GitHubWorkflowAuthorityReader
from ci_coordinator.integrations.github.workflow_catalog_client import WorkflowCatalogClient
from ci_coordinator.integrations.github.workflow_inventory import GitHubWorkflowInventoryLoader
from ci_coordinator.production_admission.evidence_lookup import ProductionEvidenceLookup
from ci_coordinator.production_admission.ports import CurrentProductionSources
from ci_coordinator.target_authority_producers.sources import ProviderAuthoritySources
from ci_coordinator.workflow_authority import WorkflowAuthorityEvidence


class GitHubCurrentProductionSourcesReader:
    def __init__(self, factory: GitHubAppTransportFactory) -> None:
        self._factory = factory
        self._workflows = GitHubWorkflowAuthorityReader(factory)
        self._governance = GitHubGovernanceObservationReader(factory)

    async def read(
        self, lookup: ProductionEvidenceLookup, *, revisions: tuple[str, ...]
    ) -> CurrentProductionSources | None:
        if (
            type(lookup) is not ProductionEvidenceLookup
            or type(revisions) is not tuple
            or not 1 <= len(revisions) <= 2
            or any(type(revision) is not str for revision in revisions)
            or tuple(sorted(set(revisions))) != revisions
        ):
            return None
        scope = lookup.repository.scope
        workflows: list[WorkflowAuthorityEvidence] = []
        for revision in revisions:
            result = await self._workflows.read(scope=scope, revision=revision)
            if (
                type(result) is not WorkflowAuthorityEvidence
                or result.manifest.repository != lookup.repository
            ):
                return None
            workflows.append(result)
        governance = await self._governance.read(scope=scope)
        if type(governance) is not GovernanceState:
            return None
        inventory = await GitHubWorkflowInventoryLoader(
            WorkflowCatalogClient(
                self._factory.for_installation(scope.installation_id),
                api_version=GITHUB_API_VERSION,
            )
        ).load(lookup.repository, revision_sha=revisions[0], required_paths=lookup.provider_paths)
        if inventory is None:
            return None
        return CurrentProductionSources(
            tuple(workflows), ProviderAuthoritySources(inventory, governance)
        )
