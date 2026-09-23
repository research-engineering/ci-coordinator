"""Complete raw-domain enumeration without expected or registration keys."""

from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.kernel.ordering import utf16_sort_key
from ci_coordinator.workflow_authority.evidence import WorkflowAuthorityEvidence
from ci_coordinator.workflow_discovery.report import DiscoveryReport

from .control_candidates import enumerate_control_candidates
from .model import ObservationCandidateSet, TargetAuthorityProducerError
from .sources import ProviderAuthoritySources, TargetArtifactSources
from .workflow_candidates import enumerate_workflow_candidates


@dataclass(frozen=True, slots=True)
class ObservationSources:
    workflow_evidence: WorkflowAuthorityEvidence
    discovery_report: DiscoveryReport
    provider_authority: ProviderAuthoritySources
    target_artifacts: TargetArtifactSources

    def __post_init__(self) -> None:
        exact_types = (
            (self.workflow_evidence, WorkflowAuthorityEvidence),
            (self.discovery_report, DiscoveryReport),
            (self.provider_authority, ProviderAuthoritySources),
            (self.target_artifacts, TargetArtifactSources),
        )
        if any(type(value) is not expected for value, expected in exact_types):
            raise TypeError("observation sources require exact admitted values")


def enumerate_observation_candidates(sources: ObservationSources) -> ObservationCandidateSet:
    if type(sources) is not ObservationSources:
        raise TypeError("candidate enumeration requires exact observation sources")
    evidence = sources.workflow_evidence
    manifest = evidence.manifest
    binding = evidence.source_binding
    repository = manifest.repository
    report = sources.discovery_report
    provider = sources.provider_authority
    target = sources.target_artifacts
    governance = provider.governance
    provider_workflows = provider.workflows.inventory
    if binding.repository != repository or binding.manifest_digest != manifest.manifest_digest:
        raise TargetAuthorityProducerError(
            "source_binding_mismatch",
            "workflow source binding does not match its stable manifest",
        )
    if (
        report.repository.scope != repository.scope
        or report.repository.owner != repository.owner
        or report.repository.name != repository.name
        or report.repository.default_branch != repository.default_branch
        or report.revision != binding.source_commit_id
    ):
        raise TargetAuthorityProducerError(
            "discovery_source_mismatch",
            "workflow discovery report crosses repository or source identity",
        )
    if not report.complete or not report.local_graph_closed:
        raise TargetAuthorityProducerError(
            "discovery_incomplete",
            "workflow discovery must be parse-complete with a closed local call graph",
        )
    _require_discovery_sources_match_manifest(sources)
    if (
        provider.repository != repository
        or provider.workflows.api_version != binding.api_version
        or provider_workflows.revision_sha != binding.source_commit_id
    ):
        raise TargetAuthorityProducerError(
            "provider_workflow_revision_mismatch",
            "provider workflow evidence crosses repository, API, or source identity",
        )
    if target.policy.scope != repository.scope:
        raise TargetAuthorityProducerError(
            "policy_scope_mismatch",
            "target policy crosses the workflow authority repository scope",
        )
    lab_repository = target.lab_profile.repository
    if (
        lab_repository.installation_id != repository.scope.installation_id
        or lab_repository.repository_id != repository.scope.repository_id
        or lab_repository.owner != repository.owner
        or lab_repository.name != repository.name
        or lab_repository.default_branch != repository.default_branch
    ):
        raise TargetAuthorityProducerError(
            "consumer_lab_source_mismatch",
            "consumer laboratory declarations cross repository identity",
        )
    catalog = target.validation_catalog

    candidates = (
        *enumerate_workflow_candidates(
            evidence=evidence,
            report=report,
            provider_inventory=provider_workflows,
            registry=target.registry,
        ),
        *enumerate_control_candidates(
            policy=target.policy,
            catalog=catalog,
            registry=target.registry,
            lab_profile=target.lab_profile,
            scenarios=target.scenarios,
            governance=governance,
        ),
    )
    return ObservationCandidateSet(
        scope=repository.scope,
        source_commit_id=binding.source_commit_id,
        source_binding_digest=binding.binding_digest,
        workflow_manifest_digest=manifest.manifest_digest,
        discovery_report_digest=report.inventory_digest,
        provider_authority_digest=provider.authority_digest,
        target_artifact_epoch_digest=target.artifact_epoch_digest,
        target_policy_digest=target.policy.epoch_hash,
        validation_catalog_digest=catalog.catalog_hash,
        target_registry_digest=target.registry.registry_hash,
        candidates=tuple(sorted(candidates, key=lambda item: utf16_sort_key(item.candidate_id))),
    )


def _require_discovery_sources_match_manifest(sources: ObservationSources) -> None:
    manifest = sources.workflow_evidence.manifest
    workflow_entries = {
        entry.path: entry
        for entry in manifest.entries
        if entry.object_type == "blob" and _is_direct_workflow(entry.path)
    }
    report_sources = {source.path: source for source in sources.discovery_report.sources}
    if workflow_entries.keys() != report_sources.keys() or any(
        entry.object_id != report_sources[path].blob_sha
        or entry.observed_size != report_sources[path].size
        for path, entry in workflow_entries.items()
    ):
        raise TargetAuthorityProducerError(
            "discovery_manifest_mismatch",
            "workflow discovery sources do not exactly match stable manifest blobs",
        )


def _is_direct_workflow(path: str) -> bool:
    prefix = ".github/workflows/"
    relative = path.removeprefix(prefix)
    return path.startswith(prefix) and "/" not in relative and relative.endswith((".yml", ".yaml"))
