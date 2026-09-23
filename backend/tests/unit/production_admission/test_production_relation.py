from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest
from production_admission_support import make_production_admission_fixture
from target_authority_evidence.factories import EvidenceFixture, evidence_fixture
from target_authority_producers.factories import WORKFLOW_PATH

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.production_admission.limits import MAX_PRODUCTION_STAGE_BYTES
from ci_coordinator.production_admission.model import ProductionScopeGrant, ProductionScopeSubject
from ci_coordinator.production_admission.relation import (
    MAX_PRODUCTION_GENERATION,
    PRODUCTION_RELATION_BINDING_SCHEMA,
    ProductionRelationBinding,
)
from ci_coordinator.production_admission.relation_admission import (
    admit_staged_production_evidence,
    bind_production_relation,
)
from ci_coordinator.target_authority_evidence.model import (
    AdmittedTargetAuthorityEvidence,
    EvidenceArtifact,
    UnactivatedEvidenceBundle,
)
from ci_coordinator.target_authority_evidence.replay import replay_target_authority_evidence
from ci_coordinator.target_authority_relation import encode_unactivated_closure


@pytest.fixture(scope="module")
def evidence_seed() -> EvidenceFixture:
    return evidence_fixture()


@pytest.fixture
def retained(evidence_seed: EvidenceFixture) -> EvidenceFixture:
    return deepcopy(evidence_seed)


@pytest.fixture
def subject(retained: EvidenceFixture) -> ProductionScopeSubject:
    draft = retained.producer.target_artifacts.policy
    projection = project_dynamic_ci_planning(draft)
    assert projection is not None
    assert projection.compiled_policy_hash != projection.policy_hash
    repository = retained.values.workflow_manifest.repository
    return ProductionScopeSubject(
        scope=draft.scope,
        config_epoch_id=projection.epoch_id,
        compiled_policy_hash=projection.compiled_policy_hash,
        policy_hash=projection.policy_hash,
        catalog_hash=projection.validation_catalog.catalog_hash,
        target_registry_hash=retained.producer.target_artifacts.registry.registry_hash,
        workflow_refs=(),
        job_workflow_refs=(),
        workflow_paths=(f"{repository.owner}/{repository.name}/{WORKFLOW_PATH}",),
    )


def _bind(
    retained: EvidenceFixture,
    subject: ProductionScopeSubject,
    *,
    bundle: UnactivatedEvidenceBundle | None = None,
    active_epoch: ValidatedEpochDraft | None = None,
) -> ProductionRelationBinding:
    return bind_production_relation(
        retained.admitted.bundle if bundle is None else bundle,
        subject=subject,
        active_epoch=retained.producer.target_artifacts.policy
        if active_epoch is None
        else active_epoch,
        generation=1,
        predecessor_generation=0,
    )


def _signed_scope(
    subject: ProductionScopeSubject, relation: ProductionRelationBinding
) -> ProductionScopeGrant:
    return make_production_admission_fixture(
        subject, relation=relation, now=datetime(2026, 9, 6, tzinfo=UTC)
    ).receipt.scope_grants[0]


def test_staged_bytes_replay_exactly_once_before_matching_signed_relation(
    retained: EvidenceFixture, subject: ProductionScopeSubject, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = _bind(retained, subject)
    calls: list[str] = []

    def replay(bundle: UnactivatedEvidenceBundle) -> AdmittedTargetAuthorityEvidence:
        calls.append(bundle.bundle_digest)
        return replay_target_authority_evidence(bundle)

    monkeypatch.setattr(
        "ci_coordinator.target_authority_evidence.codec.replay_target_authority_evidence", replay
    )
    assert (
        admit_staged_production_evidence(
            retained.admitted.bundle.canonical_bytes,
            scope_grant=_signed_scope(subject, binding),
            active_epoch=retained.producer.target_artifacts.policy,
            provider_paths=(WORKFLOW_PATH,),
        ).relation
        == binding
    )
    assert calls == [retained.admitted.bundle.bundle_digest]


@pytest.mark.parametrize(
    "paths",
    [
        (),
        (WORKFLOW_PATH, WORKFLOW_PATH),
        (".github/workflows/missing.yml",),
        ("../workflow.yml",),
        tuple(f".github/workflows/job-{index:02}.yml" for index in range(65)),
    ],
)
def test_staging_lookup_cannot_expand_the_retained_manifest(
    retained: EvidenceFixture, subject: ProductionScopeSubject, paths: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        admit_staged_production_evidence(
            retained.admitted.bundle.canonical_bytes,
            scope_grant=_signed_scope(subject, _bind(retained, subject)),
            active_epoch=retained.producer.target_artifacts.policy,
            provider_paths=paths,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: replace(value, evidence_bundle_digest="0" * 64),
        lambda value: replace(value, relation_subject_digest="0" * 64),
        lambda value: replace(value, relation_epoch_digest="0" * 64),
        lambda value: replace(value, relation_closure_digest="0" * 64),
        lambda value: replace(value, workflow_manifest_digest="0" * 64),
        lambda value: replace(value, source_binding_digest="0" * 64),
        lambda value: replace(value, provider_authority_digest="0" * 64),
        lambda value: replace(value, owner_epoch_digest="0" * 64),
    ],
    ids=["bundle", "subject", "epoch", "closure", "manifest", "source", "provider", "owner"],
)
def test_staging_rejects_every_independently_resigned_relation_mismatch(
    retained: EvidenceFixture,
    subject: ProductionScopeSubject,
    mutate: Callable[[ProductionRelationBinding], ProductionRelationBinding],
) -> None:
    binding = mutate(_bind(retained, subject))
    scope_grant = _signed_scope(subject, binding)
    with pytest.raises(ValueError, match="differs from the signed relation"):
        admit_staged_production_evidence(
            retained.admitted.bundle.canonical_bytes,
            scope_grant=scope_grant,
            active_epoch=retained.producer.target_artifacts.policy,
            provider_paths=(WORKFLOW_PATH,),
        )


@pytest.mark.parametrize(
    "content", [b"", b" " * (MAX_PRODUCTION_STAGE_BYTES + 1)], ids=("empty", "over-limit")
)
def test_staging_rejects_byte_overflow_before_decoding(
    retained: EvidenceFixture, subject: ProductionScopeSubject, content: bytes
) -> None:
    with pytest.raises(ValueError, match="byte envelope"):
        admit_staged_production_evidence(
            content,
            scope_grant=_signed_scope(subject, _bind(retained, subject)),
            active_epoch=retained.producer.target_artifacts.policy,
            provider_paths=(WORKFLOW_PATH,),
        )


@pytest.mark.parametrize("mutation", ["noncanonical", "incomplete", "forged-closure"])
def test_staging_cannot_skip_canonical_owner_replay(
    retained: EvidenceFixture, subject: ProductionScopeSubject, mutation: str
) -> None:
    bundle = retained.admitted.bundle
    content = bundle.canonical_bytes
    if mutation == "noncanonical":
        content += b"\n"
    elif mutation == "incomplete":
        content = b"{}"
    else:
        forged = replace(
            retained.values.relation_closure,
            row_count=retained.values.relation_closure.row_count + 1,
        )
        artifacts = tuple(
            EvidenceArtifact(item.role, item.owner_schema, encode_unactivated_closure(forged))
            if item.role == "relation_closure"
            else item
            for item in bundle.artifacts
        )
        content = replace(bundle, artifacts=artifacts).canonical_bytes
    with pytest.raises(ValueError):
        admit_staged_production_evidence(
            content,
            scope_grant=_signed_scope(subject, _bind(retained, subject)),
            active_epoch=retained.producer.target_artifacts.policy,
            provider_paths=(WORKFLOW_PATH,),
        )


def test_relation_binding_projects_replayed_owner_identities(
    retained: EvidenceFixture, subject: ProductionScopeSubject
) -> None:
    binding = _bind(retained, subject)
    bundle = retained.admitted.bundle
    assert binding.to_mapping() == {
        "schemaVersion": PRODUCTION_RELATION_BINDING_SCHEMA,
        "generation": 1,
        "predecessorGeneration": 0,
        "evidenceBundleDigest": bundle.bundle_digest,
        "relationSubjectDigest": bundle.subject.subject_digest,
        "relationEpochDigest": bundle.epoch.epoch_digest,
        "relationClosureDigest": retained.values.relation_closure.closure_digest,
        "workflowManifestDigest": retained.values.workflow_manifest.manifest_digest,
        "sourceBindingDigest": retained.values.source_binding.binding_digest,
        "providerAuthorityDigest": retained.values.observation_candidates.provider_authority_digest,
        "ownerEpochDigest": bundle.epoch.owner.digest,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: replace(value, scope=RepositoryScope(11, 23)),
        lambda value: replace(value, config_epoch_id="0" * 64),
        lambda value: replace(value, compiled_policy_hash="0" * 64),
        lambda value: replace(value, policy_hash="0" * 64),
        lambda value: replace(value, catalog_hash="0" * 64),
        lambda value: replace(value, target_registry_hash="0" * 64),
        lambda value: replace(value, compiled_policy_hash=value.policy_hash),
        lambda value: replace(value, workflow_paths=("other/repository/.github/workflows/ci.yml",)),
        lambda value: replace(
            value, job_workflow_paths=("other/repository/.github/workflows/ci.yml",)
        ),
        lambda value: replace(
            value, workflow_refs=("other/repository/.github/workflows/ci.yml@refs/heads/main",)
        ),
        lambda value: replace(
            value, job_workflow_refs=("other/repository/.github/workflows/ci.yml@refs/heads/main",)
        ),
    ],
    ids=[
        "scope",
        "config",
        "compiled-policy",
        "dynamic-policy",
        "catalog",
        "registry",
        "policy-hash-domain",
        "workflow-path",
        "job-path",
        "workflow-ref",
        "job-ref",
    ],
)
def test_relation_binding_rejects_each_independent_subject_mismatch(
    retained: EvidenceFixture,
    subject: ProductionScopeSubject,
    mutate: Callable[[ProductionScopeSubject], ProductionScopeSubject],
) -> None:
    assert _bind(retained, subject).generation == 1
    with pytest.raises(ValueError):
        _bind(retained, mutate(subject))


def test_relation_binding_accepts_refs_for_the_retained_workflow(
    retained: EvidenceFixture, subject: ProductionScopeSubject
) -> None:
    ref = subject.workflow_paths[0] + "@refs/heads/main"
    refs_subject = replace(
        subject, workflow_paths=(), workflow_refs=(ref,), job_workflow_refs=(ref,)
    )
    assert _bind(retained, refs_subject) == _bind(retained, subject)


def test_relation_binding_does_not_trust_a_forged_closure_value(
    retained: EvidenceFixture, subject: ProductionScopeSubject
) -> None:
    forged_closure = replace(
        retained.values.relation_closure, row_count=retained.values.relation_closure.row_count + 1
    )
    artifacts = tuple(
        EvidenceArtifact(item.role, item.owner_schema, encode_unactivated_closure(forged_closure))
        if item.role == "relation_closure"
        else item
        for item in retained.admitted.bundle.artifacts
    )
    forged_bundle = replace(retained.admitted.bundle, artifacts=artifacts)
    assert forged_bundle.bundle_digest != retained.admitted.bundle.bundle_digest
    with pytest.raises(ValueError, match="relation evidence does not reproduce"):
        _bind(retained, subject, bundle=forged_bundle)


def test_relation_binding_rejects_the_admitted_value_as_an_input_shortcut(
    retained: EvidenceFixture, subject: ProductionScopeSubject
) -> None:
    with pytest.raises(TypeError, match="exact retained bundle"):
        _bind(retained, subject, bundle=cast(UnactivatedEvidenceBundle, retained.admitted))


def test_relation_binding_rechecks_the_active_policy_bytes(
    retained: EvidenceFixture, subject: ProductionScopeSubject
) -> None:
    draft = retained.producer.target_artifacts.policy
    with pytest.raises(ValueError, match="compiled policy bytes"):
        _bind(retained, subject, active_epoch=replace(draft, compiled_policy_bytes=b"{}"))


@pytest.mark.parametrize(
    "generation,predecessor",
    [
        (0, 0),
        (-1, 0),
        (1, 1),
        (2, 0),
        (True, 0),
        (2, True),
        (MAX_PRODUCTION_GENERATION + 1, MAX_PRODUCTION_GENERATION),
    ],
)
def test_relation_generation_requires_one_exact_bounded_successor(
    retained: EvidenceFixture, subject: ProductionScopeSubject, generation: int, predecessor: int
) -> None:
    with pytest.raises(ValueError, match="bounded successor"):
        replace(_bind(retained, subject), generation=generation, predecessor_generation=predecessor)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: replace(value, generation=2, predecessor_generation=1),
        lambda value: replace(value, evidence_bundle_digest="0" * 64),
        lambda value: replace(value, relation_subject_digest="0" * 64),
        lambda value: replace(value, relation_epoch_digest="0" * 64),
        lambda value: replace(value, relation_closure_digest="0" * 64),
        lambda value: replace(value, workflow_manifest_digest="0" * 64),
        lambda value: replace(value, source_binding_digest="0" * 64),
        lambda value: replace(value, provider_authority_digest="0" * 64),
        lambda value: replace(value, owner_epoch_digest="0" * 64),
    ],
    ids=[
        "generation",
        "bundle",
        "subject",
        "epoch",
        "closure",
        "manifest",
        "source",
        "provider",
        "owner",
    ],
)
def test_binding_identity_covers_every_independent_operand(
    retained: EvidenceFixture,
    subject: ProductionScopeSubject,
    mutate: Callable[[ProductionRelationBinding], ProductionRelationBinding],
) -> None:
    binding = _bind(retained, subject)
    assert mutate(binding).binding_digest != binding.binding_digest
