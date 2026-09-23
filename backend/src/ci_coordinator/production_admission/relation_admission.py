from __future__ import annotations

from dataclasses import InitVar, dataclass

from ci_coordinator.config_control import ValidatedEpochDraft
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.identity_admission import workflow_path_identity_from_ref
from ci_coordinator.production_admission.evidence_lookup import ProductionEvidenceLookup
from ci_coordinator.production_admission.limits import MAX_PRODUCTION_STAGE_BYTES
from ci_coordinator.production_admission.model import ProductionScopeGrant, ProductionScopeSubject
from ci_coordinator.production_admission.relation import ProductionRelationBinding
from ci_coordinator.target_authority_evidence.codec import decode_target_authority_evidence
from ci_coordinator.target_authority_evidence.model import (
    AdmittedTargetAuthorityEvidence,
    UnactivatedEvidenceBundle,
)
from ci_coordinator.target_authority_evidence.replay import replay_target_authority_evidence

_STAGED_EVIDENCE_TOKEN = object()


@dataclass(frozen=True, slots=True)
class StagedProductionEvidence:
    token: InitVar[object]
    scope_grant: ProductionScopeGrant
    canonical_bytes: bytes
    lookup: ProductionEvidenceLookup

    def __post_init__(self, token: object) -> None:
        if token is not _STAGED_EVIDENCE_TOKEN:
            raise TypeError("staged evidence must be admitted from its exact retained bytes")

    @property
    def relation(self) -> ProductionRelationBinding:
        return self.scope_grant.relation


def bind_production_relation(
    bundle: UnactivatedEvidenceBundle,
    *,
    subject: ProductionScopeSubject,
    active_epoch: ValidatedEpochDraft,
    generation: int,
    predecessor_generation: int,
) -> ProductionRelationBinding:
    if type(bundle) is not UnactivatedEvidenceBundle:
        raise TypeError("production relation requires the exact retained bundle")
    return _bind_replayed_relation(
        replay_target_authority_evidence(bundle),
        subject=subject,
        active_epoch=active_epoch,
        generation=generation,
        predecessor_generation=predecessor_generation,
    )


def admit_staged_production_evidence(
    content: bytes,
    *,
    scope_grant: ProductionScopeGrant,
    active_epoch: ValidatedEpochDraft,
    provider_paths: tuple[str, ...],
) -> StagedProductionEvidence:
    if type(content) is not bytes or not 1 <= len(content) <= MAX_PRODUCTION_STAGE_BYTES:
        raise ValueError("staged production evidence exceeds its byte envelope")
    if type(scope_grant) is not ProductionScopeGrant:
        raise TypeError("staged production evidence requires the exact scope grant")
    # Canonical decoding already replays all owner artifacts; do not replay it twice.
    evidence = decode_target_authority_evidence(content)
    relation = _bind_replayed_relation(
        evidence,
        subject=scope_grant.subject,
        active_epoch=active_epoch,
        generation=scope_grant.relation.generation,
        predecessor_generation=scope_grant.relation.predecessor_generation,
    )
    if relation != scope_grant.relation:
        raise ValueError("staged production evidence differs from the signed relation")
    lookup = ProductionEvidenceLookup(
        repository=evidence.values.workflow_manifest.repository,
        source_commit=evidence.values.source_binding.source_commit_id,
        provider_paths=provider_paths,
    )
    manifest_paths = {
        entry.path
        for entry in evidence.values.workflow_manifest.entries
        if entry.object_type == "blob"
    }
    if not set(provider_paths) <= manifest_paths:
        raise ValueError("production lookup path is outside the retained manifest")
    return StagedProductionEvidence(_STAGED_EVIDENCE_TOKEN, scope_grant, content, lookup)


def _bind_replayed_relation(
    evidence: AdmittedTargetAuthorityEvidence,
    *,
    subject: ProductionScopeSubject,
    active_epoch: ValidatedEpochDraft,
    generation: int,
    predecessor_generation: int,
) -> ProductionRelationBinding:
    bundle = evidence.bundle
    if type(subject) is not ProductionScopeSubject or type(active_epoch) is not ValidatedEpochDraft:
        raise TypeError("production relation requires exact subject and policy values")
    if bundle.subject.scope != subject.scope or active_epoch.scope != subject.scope:
        raise ValueError("production relation crosses repository scope")
    projection = project_dynamic_ci_planning(active_epoch)
    if projection is None:
        raise ValueError("production relation requires a dynamic policy")
    if (
        subject.config_epoch_id != projection.epoch_id
        or subject.compiled_policy_hash != projection.compiled_policy_hash
        or subject.policy_hash != projection.policy_hash
        or subject.catalog_hash != projection.validation_catalog.catalog_hash
        or bundle.epoch.policy.digest != subject.compiled_policy_hash
        or bundle.epoch.catalog.digest != subject.catalog_hash
        or bundle.epoch.registry.digest != subject.target_registry_hash
    ):
        raise ValueError("production relation differs from its exact policy and catalog")

    manifest = evidence.values.workflow_manifest
    repository = manifest.repository
    manifest_paths = {
        f"{repository.owner}/{repository.name}/{entry.path}"
        for entry in manifest.entries
        if entry.object_type == "blob" and entry.path.endswith((".yml", ".yaml"))
    }
    admitted_paths = (*subject.workflow_paths, *subject.job_workflow_paths)
    admitted_refs = (*subject.workflow_refs, *subject.job_workflow_refs)
    if any(path not in manifest_paths for path in admitted_paths) or any(
        workflow_path_identity_from_ref(ref) not in manifest_paths for ref in admitted_refs
    ):
        raise ValueError("production workflow identity is outside the retained manifest")
    provider = bundle.epoch.provider_governance.digest
    owner = bundle.epoch.owner.digest
    if provider is None or owner is None:
        raise ValueError("production relation requires provider and owner epochs")
    return ProductionRelationBinding(
        generation=generation,
        predecessor_generation=predecessor_generation,
        evidence_bundle_digest=bundle.bundle_digest,
        relation_subject_digest=bundle.subject.subject_digest,
        relation_epoch_digest=bundle.epoch.epoch_digest,
        relation_closure_digest=evidence.values.relation_closure.closure_digest,
        workflow_manifest_digest=manifest.manifest_digest,
        source_binding_digest=evidence.values.source_binding.binding_digest,
        provider_authority_digest=provider,
        owner_epoch_digest=owner,
    )
