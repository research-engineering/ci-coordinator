from __future__ import annotations

from dataclasses import InitVar, dataclass
from datetime import datetime, timedelta
from typing import Final

from ci_coordinator.identity_admission import workflow_path_identity_from_ref
from ci_coordinator.kernel import hash_object
from ci_coordinator.production_admission.evidence_lookup import ProductionEvidenceLookup
from ci_coordinator.production_admission.model import (
    ProductionCandidateSubject,
    ProductionScopeGrant,
    _require_aware_utc,
)
from ci_coordinator.target_authority_producers.sources import ProviderAuthoritySources
from ci_coordinator.workflow_authority.evidence import WorkflowAuthorityEvidence

CURRENT_PRODUCTION_OBSERVATION_SECONDS: Final = 30
_ADMISSION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class CurrentProductionEvidence:
    """Pure observation binding, not proof of live acquisition or durable authority."""

    candidate: ProductionCandidateSubject
    scope_grant: ProductionScopeGrant
    scope_revision: int
    database_started_at: datetime
    source_binding_digests: tuple[str, ...]
    provider_evidence_digest: str
    token: InitVar[object]

    def __post_init__(self, token: object) -> None:
        if token is not _ADMISSION_TOKEN:
            raise TypeError("current production evidence requires owner admission")

    def is_current_at(self, database_now: datetime) -> bool:
        return _is_current_at(self.database_started_at, database_now)


@dataclass(frozen=True, slots=True)
class CurrentActivationEvidence:
    """Current retained-source/provider evidence, not an authenticated CI run."""

    scope_grant: ProductionScopeGrant
    scope_revision: int
    database_started_at: datetime
    source_binding_digest: str
    provider_evidence_digest: str
    lookup_digest: str
    token: InitVar[object]

    def __post_init__(self, token: object) -> None:
        if token is not _ADMISSION_TOKEN:
            raise TypeError("activation evidence requires owner admission")

    def is_current_at(self, database_now: datetime) -> bool:
        return _is_current_at(self.database_started_at, database_now)


def _is_current_at(started_at: datetime, database_now: datetime) -> bool:
    _require_aware_utc(database_now, "current production database time")
    return (
        timedelta(0)
        <= database_now - started_at
        < timedelta(seconds=CURRENT_PRODUCTION_OBSERVATION_SECONDS)
    )


def admit_current_production_evidence(
    *,
    candidate: ProductionCandidateSubject,
    scope_grant: ProductionScopeGrant,
    scope_revision: int,
    database_started_at: datetime,
    workflow_sources: tuple[WorkflowAuthorityEvidence, ...],
    provider_sources: ProviderAuthoritySources,
) -> CurrentProductionEvidence:
    if type(scope_grant) is not ProductionScopeGrant or not scope_grant.subject.admits(candidate):
        raise ValueError("current production evidence does not match the signed scope")
    if type(scope_revision) is not int or not 1 <= scope_revision <= 9_007_199_254_740_991:
        raise ValueError("current production scope revision is invalid")
    _require_aware_utc(database_started_at, "current production acquisition start")
    if (
        type(workflow_sources) is not tuple
        or not 1 <= len(workflow_sources) <= 2
        or any(type(item) is not WorkflowAuthorityEvidence for item in workflow_sources)
        or type(provider_sources) is not ProviderAuthoritySources
    ):
        raise ValueError("current production sources must be exact bounded owner evidence")

    coordinates = tuple(
        (ref, sha)
        for ref, sha in (
            (candidate.workflow_ref, candidate.workflow_sha),
            (candidate.job_workflow_ref, candidate.job_workflow_sha),
        )
        if ref is not None
    )
    sources = {item.source_binding.source_commit_id: item for item in workflow_sources}
    if len(sources) != len(workflow_sources) or set(sources) != {sha for _, sha in coordinates}:
        raise ValueError("current production sources must cover every authenticated workflow SHA")

    repository = provider_sources.repository
    _require_current_sources(scope_grant, workflow_sources, provider_sources)
    for ref, sha in coordinates:
        if sha is None:
            raise ValueError("current production workflow SHA is absent")
        path = workflow_path_identity_from_ref(ref)
        if path is None or path not in {
            f"{repository.full_name}/{entry.path}" for entry in sources[sha].manifest.entries
        }:
            raise ValueError("current production reference is outside its complete manifest")

    return CurrentProductionEvidence(
        candidate=candidate,
        scope_grant=scope_grant,
        scope_revision=scope_revision,
        database_started_at=database_started_at,
        source_binding_digests=tuple(
            sorted(item.source_binding.binding_digest for item in workflow_sources)
        ),
        provider_evidence_digest=provider_sources.evidence_digest,
        token=_ADMISSION_TOKEN,
    )


def admit_current_activation_evidence(
    *,
    scope_grant: ProductionScopeGrant,
    scope_revision: int,
    database_started_at: datetime,
    lookup: ProductionEvidenceLookup,
    workflow_source: WorkflowAuthorityEvidence,
    provider_sources: ProviderAuthoritySources,
) -> CurrentActivationEvidence:
    if (
        type(scope_grant) is not ProductionScopeGrant
        or type(lookup) is not ProductionEvidenceLookup
        or type(workflow_source) is not WorkflowAuthorityEvidence
        or type(provider_sources) is not ProviderAuthoritySources
        or type(scope_revision) is not int
        or not 1 <= scope_revision <= 9_007_199_254_740_991
    ):
        raise ValueError("activation evidence requires exact source and revision values")
    _require_aware_utc(database_started_at, "activation production acquisition start")
    if (
        workflow_source.manifest.repository != lookup.repository
        or workflow_source.source_binding.source_commit_id != lookup.source_commit
    ):
        raise ValueError("activation source differs from its retained lookup")
    _require_current_sources(scope_grant, (workflow_source,), provider_sources)
    return CurrentActivationEvidence(
        scope_grant=scope_grant,
        scope_revision=scope_revision,
        database_started_at=database_started_at,
        source_binding_digest=workflow_source.source_binding.binding_digest,
        provider_evidence_digest=provider_sources.evidence_digest,
        lookup_digest=hash_object(lookup.to_mapping()),
        token=_ADMISSION_TOKEN,
    )


def _require_current_sources(
    scope_grant: ProductionScopeGrant,
    sources: tuple[WorkflowAuthorityEvidence, ...],
    provider: ProviderAuthoritySources,
) -> None:
    relation = scope_grant.relation
    if (
        provider.repository.scope != scope_grant.subject.scope
        or provider.authority_digest != relation.provider_authority_digest
        or provider.workflows.inventory.revision_sha
        not in {source.source_binding.source_commit_id for source in sources}
    ):
        raise ValueError("current production provider authority differs from the admitted epoch")
    for source in sources:
        if (
            source.manifest.repository != provider.repository
            or source.manifest.manifest_digest != relation.workflow_manifest_digest
            or source.source_binding.provider_request.api_version != provider.workflows.api_version
        ):
            raise ValueError("current production workflow source differs from the admitted epoch")
