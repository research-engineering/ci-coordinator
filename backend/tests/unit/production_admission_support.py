from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import FixedClock, canonical_json
from ci_coordinator.production_admission import (
    PRODUCTION_ADMISSION_ALGORITHM,
    PRODUCTION_ADMISSION_ENVELOPE_SCHEMA,
    ProductionAdmissionGrant,
    ProductionAdmissionReceipt,
    ProductionEvidenceAttestation,
    ProductionEvidenceSet,
    ProductionScopeGrant,
    ProductionScopeSubject,
    ShadowEvidenceAttestation,
    admit_production_admission,
    production_admission_subject_digest,
    project_candidate_subject,
)
from ci_coordinator.production_admission.relation import ProductionRelationBinding
from ci_coordinator.runner_capacity import TrustedExecutionProjection
from ci_coordinator.verification_core import VerifiedPlan

ARTIFACT_DIGEST = "sha256:" + "a" * 64
RELEASE_IDENTITY = "b" * 64
SOURCE_COMMIT = "c" * 40
ROLLOUT_PROFILE_ID = "d" * 64
PRODUCTION_KEY_ID = "production-test-key"


@dataclass(frozen=True, slots=True)
class ProductionAdmissionFixture:
    content: bytes
    public_key_pem: bytes
    grant: ProductionAdmissionGrant
    receipt: ProductionAdmissionReceipt


def make_production_grant(
    verified_plan: VerifiedPlan,
    execution_projection: TrustedExecutionProjection,
    identity: TrustedActionsRun,
    scope: RepositoryScope,
    *,
    now: datetime,
    expires_at: datetime | None = None,
    relation: ProductionRelationBinding | None = None,
) -> ProductionAdmissionGrant:
    candidate = project_candidate_subject(scope, verified_plan, identity)
    subject = ProductionScopeSubject(
        scope=scope,
        config_epoch_id=candidate.config_epoch_id,
        compiled_policy_hash=candidate.compiled_policy_hash,
        policy_hash=candidate.policy_hash,
        catalog_hash=candidate.catalog_hash,
        target_registry_hash=execution_projection.target_registry_hash,
        workflow_refs=() if candidate.workflow_ref is None else (candidate.workflow_ref,),
        job_workflow_refs=(
            () if candidate.job_workflow_ref is None else (candidate.job_workflow_ref,)
        ),
    )
    return make_production_admission_fixture(
        subject,
        now=now,
        expires_at=expires_at,
        relation=relation,
    ).grant


def make_production_admission_fixture(
    subject: ProductionScopeSubject,
    *,
    now: datetime,
    expires_at: datetime | None = None,
    minimum_remaining_seconds: int = 0,
    relation: ProductionRelationBinding | None = None,
    signing_key: Ed25519PrivateKey | None = None,
) -> ProductionAdmissionFixture:
    binding = synthetic_relation_binding() if relation is None else relation
    admission_subject_digest = production_admission_subject_digest(
        artifact_digest=ARTIFACT_DIGEST,
        release_identity=RELEASE_IDENTITY,
        source_commit=SOURCE_COMMIT,
        environment_id="production",
        rollout_profile_id=ROLLOUT_PROFILE_ID,
        scope_subject=subject,
        relation=binding,
    )
    evidence = _evidence(admission_subject_digest, now=now)
    receipt = ProductionAdmissionReceipt(
        artifact_digest=ARTIFACT_DIGEST,
        release_identity=RELEASE_IDENTITY,
        source_commit=SOURCE_COMMIT,
        environment_id="production",
        rollout_profile_id=ROLLOUT_PROFILE_ID,
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(days=1) if expires_at is None else expires_at,
        scope_grants=(ProductionScopeGrant(subject, admission_subject_digest, evidence, binding),),
    )
    key = Ed25519PrivateKey.generate() if signing_key is None else signing_key
    unsigned = {
        "schemaVersion": PRODUCTION_ADMISSION_ENVELOPE_SCHEMA,
        "keyId": PRODUCTION_KEY_ID,
        "algorithm": PRODUCTION_ADMISSION_ALGORITHM,
        "receipt": receipt.to_mapping(),
    }
    signature = base64.urlsafe_b64encode(key.sign(canonical_json(unsigned))).rstrip(b"=")
    content = canonical_json({**unsigned, "signature": signature.decode("ascii")}) + b"\n"
    public_key_pem = key.public_key().public_bytes(
        Encoding.PEM,
        PublicFormat.SubjectPublicKeyInfo,
    )
    admitted = admit_production_admission(
        content,
        public_key_pem=public_key_pem,
        expected_key_id=PRODUCTION_KEY_ID,
        expected_artifact_digest=ARTIFACT_DIGEST,
        expected_release_identity=RELEASE_IDENTITY,
        expected_source_commit=SOURCE_COMMIT,
        expected_environment_id="production",
        expected_rollout_profile_id=ROLLOUT_PROFILE_ID,
        expected_repository_scopes=(subject.scope,),
        minimum_remaining_seconds=minimum_remaining_seconds,
        clock=FixedClock(now),
    )
    assert isinstance(admitted, ProductionAdmissionGrant)
    return ProductionAdmissionFixture(content, public_key_pem, admitted, receipt)


def synthetic_relation_binding() -> ProductionRelationBinding:
    return ProductionRelationBinding(
        generation=1,
        predecessor_generation=0,
        evidence_bundle_digest="1" * 64,
        relation_subject_digest="2" * 64,
        relation_epoch_digest="3" * 64,
        relation_closure_digest="4" * 64,
        workflow_manifest_digest="5" * 64,
        source_binding_digest="6" * 64,
        provider_authority_digest="7" * 64,
        owner_epoch_digest="8" * 64,
    )


def _evidence(subject_digest: str, *, now: datetime) -> ProductionEvidenceSet:
    def ordinary(index: int) -> ProductionEvidenceAttestation:
        return ProductionEvidenceAttestation(
            evidence_digest=f"{index:x}" * 64,
            subject_digest=subject_digest,
            observed_at=now - timedelta(hours=1),
            sample_count=1,
        )

    return ProductionEvidenceSet(
        deployment=ordinary(1),
        full_ci_fallback=ordinary(2),
        owner_approval=ordinary(3),
        provider=ordinary(4),
        rollback=ordinary(5),
        shadow=ShadowEvidenceAttestation(
            evidence_digest="6" * 64,
            subject_digest=subject_digest,
            observed_at=now - timedelta(hours=1),
            sample_count=100,
            observation_seconds=86_400,
            unsafe_omission_count=0,
        ),
        stable_gate=ordinary(7),
    )
