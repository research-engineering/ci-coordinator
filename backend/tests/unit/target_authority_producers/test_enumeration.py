from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field, replace

import pytest
from workflow_authority.factories import evidence as workflow_evidence

from ci_coordinator.governance_observation import GovernanceRepository, GovernanceState
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubRequest,
    GitHubResponse,
)
from ci_coordinator.integrations.github.workflow_catalog_client import WorkflowCatalogClient
from ci_coordinator.integrations.github.workflow_inventory import GitHubWorkflowInventoryLoader
from ci_coordinator.repo_context import ProviderWorkflowInventory
from ci_coordinator.target_authority_producers import (
    ProviderAuthoritySources,
    TargetArtifactSources,
    TargetAuthorityProducerError,
    enumerate_observation_candidates,
    enumerate_registration_candidates,
)

from .factories import REVISION, SCOPE, WORKFLOW_DOCUMENT, WORKFLOW_PATH, observation_sources


def test_observation_enumerates_the_complete_source_domain_without_expected_keys() -> None:
    sources = observation_sources()

    result = enumerate_observation_candidates(sources)

    expected_ids = {
        "workflow_blob:.github/workflows/ci.yml",
        "workflow:.github/workflows/ci.yml",
        "job:.github/workflows/ci.yml#ci-invocation",
        "job:.github/workflows/ci.yml#gate",
        "job:.github/workflows/ci.yml#plan",
        "job:.github/workflows/ci.yml#plan-request",
        "job:.github/workflows/ci.yml#test",
        "target_policy:dynamic-ci",
        "validation_obligation:repository-quality",
        "validation_witness:python-quality",
        "validation_profile:python-linux",
        "target_registry_metadata:registry",
        "target_registry_workflow:.github/workflows/ci.yml",
        "target_registry_profile:python-linux",
        "consumer_contract_scenario:native-consumer#fallback-change",
        "consumer_contract_scenario:native-consumer#source-change",
        "provider_repository:example-org/consumer",
        ("provider_gate:Repository:example-org/consumer:41:required_status_checks"),
        *(
            f"target_registry_adapter:{item.path}"
            for item in sources.target_artifacts.registry.adapter_files
        ),
    }
    observed_ids = tuple(candidate.candidate_id for candidate in result.candidates)

    assert set(observed_ids) == expected_ids
    assert len(observed_ids) == len(expected_ids)
    assert result.source_commit_id == REVISION
    assert result.workflow_manifest_digest == sources.workflow_evidence.manifest.manifest_digest


def test_enumeration_rejects_provider_workflow_domain_omission() -> None:
    sources = observation_sources()
    workflow_evidence = sources.provider_authority.workflows
    provider = ProviderWorkflowInventory(
        workflow_evidence.inventory.revision_sha,
        (),
        workflow_evidence.inventory.revision_capabilities,
    )
    provider_authority = replace(
        sources.provider_authority,
        workflows=replace(workflow_evidence, inventory=provider),
    )

    with pytest.raises(TargetAuthorityProducerError) as raised:
        enumerate_observation_candidates(replace(sources, provider_authority=provider_authority))

    assert raised.value.code == "provider_workflow_domain_mismatch"


def test_enumeration_rejects_discovery_and_manifest_content_disagreement() -> None:
    sources = observation_sources()
    changed = workflow_evidence(
        revision=REVISION,
        workflow_content=WORKFLOW_DOCUMENT.replace("name: CI", "name: Changed CI").encode(),
        workflow_path=WORKFLOW_PATH,
        scope=SCOPE,
        owner="example-org",
        name="consumer",
        default_branch="master",
    )

    with pytest.raises(TargetAuthorityProducerError) as raised:
        enumerate_observation_candidates(replace(sources, workflow_evidence=changed))

    assert raised.value.code == "discovery_manifest_mismatch"


def test_enumeration_rejects_cross_repository_governance() -> None:
    sources = observation_sources()
    provider = sources.provider_authority
    governance = GovernanceState(
        GovernanceRepository(
            SCOPE,
            101,
            "example-org",
            "other",
            "example-org/other",
            "master",
        ),
        provider.governance.api_version,
        provider.governance.rules,
    )

    with pytest.raises(TargetAuthorityProducerError) as raised:
        ProviderAuthoritySources(provider.workflows, governance)

    assert raised.value.code == "provider_authority_subject_mismatch"


def test_same_revision_sha_cannot_mix_provider_evidence_across_repositories() -> None:
    sources = observation_sources()
    provider = sources.provider_authority
    workflows = replace(provider.workflows, name="other")
    governance = GovernanceState(
        GovernanceRepository(
            SCOPE,
            101,
            "example-org",
            "other",
            "example-org/other",
            "master",
        ),
        provider.governance.api_version,
        provider.governance.rules,
    )
    other_repository = ProviderAuthoritySources(workflows, governance)

    with pytest.raises(TargetAuthorityProducerError) as raised:
        enumerate_observation_candidates(replace(sources, provider_authority=other_repository))

    assert raised.value.code == "provider_workflow_revision_mismatch"


def test_provider_workflow_evidence_rejects_unsafe_subject_text() -> None:
    evidence = observation_sources().provider_authority.workflows

    with pytest.raises(ValueError):
        replace(evidence, owner=".")
    with pytest.raises(ValueError):
        replace(evidence, name="bad/name")
    with pytest.raises(ValueError):
        replace(evidence, default_branch="bad..branch")
    with pytest.raises(ValueError):
        replace(evidence, api_version="v1\0x")


def test_target_artifact_epoch_rejects_same_profile_with_changed_corpus() -> None:
    sources = observation_sources()
    target = sources.target_artifacts
    first = target.scenarios.scenarios[0]
    changed = replace(first, scenario_id=f"{first.scenario_id}-changed")
    corpus = replace(
        target.scenarios,
        scenarios=(changed, *target.scenarios.scenarios[1:]),
    )

    with pytest.raises(TargetAuthorityProducerError) as raised:
        TargetArtifactSources(target.policy, target.registry, target.lab_profile, corpus)

    assert raised.value.code == "consumer_contract_epoch_mismatch"


def test_safety_unknowns_remain_unknown_in_authority_fields() -> None:
    document = WORKFLOW_DOCUMENT.replace("on:\n  push:", "on: ${{ inputs.event }}")

    result = enumerate_observation_candidates(observation_sources(workflow_document=document))

    workflow = next(
        candidate
        for candidate in result.candidates
        if candidate.candidate_id == "workflow:.github/workflows/ci.yml"
    )
    semantic_projection = next(
        entry.field for entry in workflow.fields if entry.name == "semanticProjection"
    )
    trigger_surface = next(
        entry.field for entry in workflow.fields if entry.name == "triggerSurface"
    )
    assert semantic_projection.state == "unknown"
    assert trigger_surface.state == "unknown"


def test_provider_undisclosed_bypass_authority_remains_unknown() -> None:
    result = enumerate_observation_candidates(observation_sources())
    gate = next(
        candidate
        for candidate in result.candidates
        if candidate.projected_family == "provider_gate"
    )
    fields = {entry.name: entry.field for entry in gate.fields}

    assert fields["bypassActors"].state == "unknown"
    assert fields["checkAppBinding"].state == "present"
    assert fields["requiredCheckIdentity"].state == "present"


def test_non_ci_revision_changes_provenance_but_not_authority_rows() -> None:
    first = enumerate_observation_candidates(observation_sources(revision="a" * 40))
    second = enumerate_observation_candidates(observation_sources(revision="b" * 40))

    assert first.source_binding_digest != second.source_binding_digest
    assert first.discovery_report_digest != second.discovery_report_digest
    assert first.candidate_set_digest != second.candidate_set_digest
    assert first.authority_domain_digest == second.authority_domain_digest
    assert first.workflow_manifest_digest == second.workflow_manifest_digest
    assert first.provider_authority_digest == second.provider_authority_digest
    assert tuple(item.evidence_digest for item in first.candidates) == tuple(
        item.evidence_digest for item in second.candidates
    )


def test_registration_and_observation_enumerate_target_declarations_independently() -> None:
    sources = observation_sources()

    registration = enumerate_registration_candidates(sources.target_artifacts)
    observation = enumerate_observation_candidates(sources)

    observed = {candidate.candidate_id: candidate for candidate in observation.candidates}
    assert registration.target_artifact_epoch_digest == (observation.target_artifact_epoch_digest)
    assert all(
        candidate.candidate_id in observed
        and candidate.evidence_digest == observed[candidate.candidate_id].evidence_digest
        for candidate in registration.candidates
    )


@pytest.mark.parametrize("include_platforms", [False, True])
@pytest.mark.parametrize("extra_source", [False, True])
def test_real_inventory_loader_preserves_the_complete_producer_authority_domain(
    include_platforms: bool, extra_source: bool
) -> None:
    sources = observation_sources()
    repository = sources.workflow_evidence.manifest.repository
    transport = _WorkflowPopulationTransport(include_platforms, extra_source)
    loader = GitHubWorkflowInventoryLoader(
        WorkflowCatalogClient(transport, api_version=GITHUB_API_VERSION)
    )
    inventory = asyncio.run(
        loader.load(repository, revision_sha=REVISION, required_paths=(WORKFLOW_PATH,))
    )
    assert inventory is not None, "actual catalogue and content decoding must succeed"
    assert [request.path for request in transport.requests] == [
        "/repos/example-org/consumer/actions/workflows",
        f"/repos/example-org/consumer/contents/{WORKFLOW_PATH}",
    ]
    assert [
        [(item.name, item.value) for item in request.query] for request in transport.requests
    ] == [[("page", "1"), ("per_page", "100")], [("ref", REVISION)]]
    actual_sources = replace(
        sources,
        provider_authority=ProviderAuthoritySources(
            inventory, sources.provider_authority.governance
        ),
    )
    if extra_source:
        assert len(inventory.inventory.default_branch_workflows) == 2
        with pytest.raises(TargetAuthorityProducerError) as raised:
            enumerate_observation_candidates(actual_sources)
        assert raised.value.code == "provider_workflow_domain_mismatch"
    else:
        assert inventory == sources.provider_authority.workflows
        expected = enumerate_observation_candidates(sources)
        actual = enumerate_observation_candidates(actual_sources)
        assert actual == expected
        assert actual.candidate_set_digest == expected.candidate_set_digest
        assert actual.authority_domain_digest == expected.authority_domain_digest
        assert actual.provider_authority_digest == expected.provider_authority_digest


@dataclass
class _WorkflowPopulationTransport:
    include_platforms: bool
    extra_source: bool
    requests: list[GitHubRequest] = field(default_factory=list)

    async def send(self, request: GitHubRequest) -> GitHubResponse:
        self.requests.append(request)
        total: int | None = None
        payload: dict[str, object]
        if request.operation == "workflow_catalog.list_workflows":
            rows = [{"id": 101, "path": WORKFLOW_PATH, "state": "active"}]
            if self.include_platforms:
                rows.extend(
                    {"id": identity, "path": path, "state": "active"}
                    for identity, path in (
                        (901, "dynamic/dependabot/update-graph"),
                        (902, "dynamic/github-code-scanning/codeql"),
                    )
                )
            if self.extra_source:
                rows.append({"id": 201, "path": ".github/workflows/extra.yml", "state": "active"})
            total = len(rows)
            payload = {"total_count": total, "workflows": rows}
        elif request.operation == "workflow_catalog.get_content":
            content = WORKFLOW_DOCUMENT.encode()
            payload = {
                "type": "file",
                "path": WORKFLOW_PATH,
                "encoding": "base64",
                "size": len(content),
                "content": base64.b64encode(content).decode("ascii"),
            }
        else:
            raise AssertionError(f"unexpected provider operation: {request.operation}")
        return GitHubResponse(
            status=200,
            api_version=GITHUB_API_VERSION,
            headers=(),
            body=json.dumps(payload, separators=(",", ":")).encode(),
            pagination=GitHubPaginationEvidence(True, 1, total, None, "not_paginated"),
        )
