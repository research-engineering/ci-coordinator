from __future__ import annotations

import asyncio
from unittest.mock import Mock, create_autospec

import pytest
from production_cutover_support import production_cutover_fixture

from ci_coordinator.integrations.github import production_authority as adapter
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.governance_observation import (
    GitHubGovernanceObservationReader,
)
from ci_coordinator.integrations.github.workflow_authority import GitHubWorkflowAuthorityReader
from ci_coordinator.integrations.github.workflow_inventory import GitHubWorkflowInventoryLoader


@pytest.mark.parametrize("unavailable", (None, "workflow", "governance", "inventory"))
def test_composite_current_sources_require_every_owner_and_exact_request_coordinates(
    monkeypatch: pytest.MonkeyPatch, unavailable: str | None
) -> None:
    fixture = production_cutover_fixture(local_requester=True)
    lookup = fixture.staged.lookup
    factory = create_autospec(GitHubAppTransportFactory, instance=True)
    workflows = create_autospec(GitHubWorkflowAuthorityReader, instance=True)
    governance = create_autospec(GitHubGovernanceObservationReader, instance=True)
    inventory = create_autospec(GitHubWorkflowInventoryLoader, instance=True)
    workflows.read.return_value = (
        None if unavailable == "workflow" else fixture.sources.workflows[0]
    )
    governance.read.return_value = (
        None if unavailable == "governance" else fixture.sources.provider.governance
    )
    inventory.load.return_value = (
        None if unavailable == "inventory" else fixture.sources.provider.workflows
    )
    workflow_factory = Mock(return_value=workflows)
    governance_factory = Mock(return_value=governance)
    inventory_factory = Mock(return_value=inventory)
    catalog_factory = Mock()
    monkeypatch.setattr(adapter, "GitHubWorkflowAuthorityReader", workflow_factory)
    monkeypatch.setattr(adapter, "GitHubGovernanceObservationReader", governance_factory)
    monkeypatch.setattr(adapter, "GitHubWorkflowInventoryLoader", inventory_factory)
    monkeypatch.setattr(adapter, "WorkflowCatalogClient", catalog_factory)
    reader = adapter.GitHubCurrentProductionSourcesReader(factory)
    result = asyncio.run(reader.read(lookup, revisions=(lookup.source_commit,)))
    assert (result == fixture.sources) == (unavailable is None)
    workflows.read.assert_awaited_once_with(
        scope=lookup.repository.scope, revision=lookup.source_commit
    )
    if unavailable == "workflow":
        governance.read.assert_not_awaited()
        inventory.load.assert_not_awaited()
    else:
        governance.read.assert_awaited_once_with(scope=lookup.repository.scope)
        if unavailable == "governance":
            inventory.load.assert_not_awaited()
        else:
            catalog_factory.assert_called_once_with(
                factory.for_installation.return_value, api_version=GITHUB_API_VERSION
            )
            inventory.load.assert_awaited_once_with(
                lookup.repository,
                revision_sha=lookup.source_commit,
                required_paths=lookup.provider_paths,
            )


@pytest.mark.parametrize(
    "revisions", ((), ("a" * 40, "a" * 40), ("b" * 40, "a" * 40), ("a" * 40, "b" * 40, "c" * 40))
)
def test_invalid_current_revision_set_makes_no_provider_request(revisions: tuple[str, ...]) -> None:
    fixture = production_cutover_fixture()
    factory = create_autospec(GitHubAppTransportFactory, instance=True)
    reader = adapter.GitHubCurrentProductionSourcesReader(factory)
    assert asyncio.run(reader.read(fixture.staged.lookup, revisions=revisions)) is None
    factory.for_installation.assert_not_called()
