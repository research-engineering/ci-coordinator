"""Non-persisted deterministic authority confined to one local lab scenario."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import workflow_path_identity_from_ref
from ci_coordinator.kernel import FixedClock, canonical_json, hash_object
from ci_coordinator.production_admission import (
    PRODUCTION_ADMISSION_ALGORITHM,
    PRODUCTION_ADMISSION_ENVELOPE_SCHEMA,
    AuthorizedProductionAdmission,
    ProductionAdmissionGrant,
    ProductionAdmissionReceipt,
    ProductionCandidateSubject,
    ProductionEvidenceAttestation,
    ProductionEvidenceSet,
    ProductionPlanSubject,
    ProductionScopeGrant,
    ProductionScopeSubject,
    ShadowEvidenceAttestation,
    admit_production_admission,
    production_admission_subject_digest,
)
from ci_coordinator.production_admission.relation import ProductionRelationBinding
from ci_coordinator.production_admission.request_authority import PreparedProductionAuthority

_ENVIRONMENT_ID = "consumer-lab"
_KEY_ID = "consumer-contract-lab"


class SyntheticLabAuthority:
    """Mint one real verifier-owned capability from explicitly synthetic evidence."""

    def __init__(
        self,
        *,
        scope: RepositoryScope,
        config_epoch_id: str,
        compiled_policy_hash: str,
        policy_hash: str,
        catalog_hash: str,
        workflow_path_identity: str,
        job_workflow_ref: str,
        target_registry_hash: str,
        coordinator_commit: str,
        source_epoch_id: str,
        scenario_id: str,
        now: datetime,
    ) -> None:
        self._scope = scope
        self._config_epoch_id = config_epoch_id
        self._compiled_policy_hash = compiled_policy_hash
        self._policy_hash = policy_hash
        self._catalog_hash = catalog_hash
        self._workflow_path_identity = workflow_path_identity
        self._job_workflow_ref = job_workflow_ref
        self._target_registry_hash = target_registry_hash
        self._coordinator_commit = coordinator_commit
        self._source_epoch_id = source_epoch_id
        self._scenario_id = scenario_id
        self._now = now
        self._candidate: ProductionCandidateSubject | None = None
        self._grant: ProductionAdmissionGrant | None = None

    async def prepare(
        self, candidate: ProductionCandidateSubject
    ) -> PreparedProductionAuthority | None:
        if not self.preauthorizes(candidate):
            return None
        if self._grant is None:
            self._grant = self._admit_grant()
        return PreparedProductionAuthority(self._grant, None)

    def preauthorizes(self, candidate: ProductionCandidateSubject) -> bool:
        if type(candidate) is not ProductionCandidateSubject:
            return False
        admitted = (
            candidate.scope == self._scope
            and candidate.config_epoch_id == self._config_epoch_id
            and candidate.compiled_policy_hash == self._compiled_policy_hash
            and candidate.policy_hash == self._policy_hash
            and candidate.catalog_hash == self._catalog_hash
            and workflow_path_identity_from_ref(candidate.workflow_ref)
            == self._workflow_path_identity
            and candidate.job_workflow_ref == self._job_workflow_ref
        )
        if not admitted:
            return False
        if self._candidate is None:
            self._candidate = candidate
        return candidate == self._candidate

    def authorize(
        self,
        subject: ProductionPlanSubject,
    ) -> AuthorizedProductionAdmission | None:
        if (
            type(subject) is not ProductionPlanSubject
            or self._candidate is None
            or subject.candidate != self._candidate
            or subject.target_registry_hash != self._target_registry_hash
        ):
            return None
        if self._grant is None:
            self._grant = self._admit_grant()
        return self._grant.authorize(subject)

    def _admit_grant(self) -> ProductionAdmissionGrant:
        subject = ProductionScopeSubject(
            scope=self._scope,
            config_epoch_id=self._config_epoch_id,
            compiled_policy_hash=self._compiled_policy_hash,
            policy_hash=self._policy_hash,
            catalog_hash=self._catalog_hash,
            target_registry_hash=self._target_registry_hash,
            workflow_refs=(),
            job_workflow_refs=(self._job_workflow_ref,),
            workflow_paths=(self._workflow_path_identity,),
            job_workflow_paths=(),
        )
        artifact_digest = "sha256:" + self._source_epoch_id
        release_identity = hash_object(
            {
                "kind": "consumer-contract-lab",
                "sourceEpochId": self._source_epoch_id,
                "scenarioId": self._scenario_id,
            }
        )
        rollout_profile_id = hash_object(
            {
                "environmentId": _ENVIRONMENT_ID,
                "workflowPath": self._workflow_path_identity,
            }
        )
        relation = _synthetic_relation(self._source_epoch_id, self._scenario_id)
        subject_digest = production_admission_subject_digest(
            artifact_digest=artifact_digest,
            release_identity=release_identity,
            source_commit=self._coordinator_commit,
            environment_id=_ENVIRONMENT_ID,
            rollout_profile_id=rollout_profile_id,
            scope_subject=subject,
            relation=relation,
        )
        issued_at = self._now - timedelta(minutes=1)
        receipt = ProductionAdmissionReceipt(
            artifact_digest=artifact_digest,
            release_identity=release_identity,
            source_commit=self._coordinator_commit,
            environment_id=_ENVIRONMENT_ID,
            rollout_profile_id=rollout_profile_id,
            issued_at=issued_at,
            expires_at=self._now + timedelta(minutes=30),
            scope_grants=(
                ProductionScopeGrant(
                    subject,
                    subject_digest,
                    _synthetic_evidence(subject_digest, observed_at=issued_at),
                    relation,
                ),
            ),
        )
        key = lab_private_key(
            domain="admission",
            source_epoch_id=self._source_epoch_id,
            scenario_id=self._scenario_id,
        )
        unsigned = {
            "schemaVersion": PRODUCTION_ADMISSION_ENVELOPE_SCHEMA,
            "keyId": _KEY_ID,
            "algorithm": PRODUCTION_ADMISSION_ALGORITHM,
            "receipt": receipt.to_mapping(),
        }
        signature = base64.urlsafe_b64encode(key.sign(canonical_json(unsigned))).rstrip(b"=")
        content = canonical_json({**unsigned, "signature": signature.decode("ascii")}) + b"\n"
        admitted = admit_production_admission(
            content,
            public_key_pem=key.public_key().public_bytes(
                Encoding.PEM,
                PublicFormat.SubjectPublicKeyInfo,
            ),
            expected_key_id=_KEY_ID,
            expected_artifact_digest=artifact_digest,
            expected_release_identity=release_identity,
            expected_source_commit=self._coordinator_commit,
            expected_environment_id=_ENVIRONMENT_ID,
            expected_rollout_profile_id=rollout_profile_id,
            expected_repository_scopes=(self._scope,),
            minimum_remaining_seconds=0,
            clock=FixedClock(self._now),
        )
        if not isinstance(admitted, ProductionAdmissionGrant):
            raise AssertionError("synthetic lab admission was not verifier-admitted")
        return admitted


def lab_private_key_pem(
    *,
    domain: str,
    source_epoch_id: str,
    scenario_id: str,
) -> bytes:
    return lab_private_key(
        domain=domain,
        source_epoch_id=source_epoch_id,
        scenario_id=scenario_id,
    ).private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())


def lab_private_key(
    *,
    domain: str,
    source_epoch_id: str,
    scenario_id: str,
) -> Ed25519PrivateKey:
    seed = hashlib.sha256(
        f"ci-coordinator-consumer-lab:{domain}:{source_epoch_id}:{scenario_id}".encode()
    ).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def _synthetic_relation(source_epoch_id: str, scenario_id: str) -> ProductionRelationBinding:
    def coordinate(name: str) -> str:
        return hash_object(
            {
                "authority": "lab-fixture",
                "sourceEpochId": source_epoch_id,
                "scenarioId": scenario_id,
                "coordinate": name,
            }
        )

    return ProductionRelationBinding(
        generation=1,
        predecessor_generation=0,
        evidence_bundle_digest=coordinate("bundle"),
        relation_subject_digest=coordinate("subject"),
        relation_epoch_digest=coordinate("epoch"),
        relation_closure_digest=coordinate("closure"),
        workflow_manifest_digest=coordinate("manifest"),
        source_binding_digest=coordinate("source"),
        provider_authority_digest=coordinate("provider"),
        owner_epoch_digest=coordinate("owner"),
    )


def _synthetic_evidence(
    subject_digest: str,
    *,
    observed_at: datetime,
) -> ProductionEvidenceSet:
    def ordinary(name: str) -> ProductionEvidenceAttestation:
        return ProductionEvidenceAttestation(
            evidence_digest=hash_object(
                {
                    "authority": "lab-fixture",
                    "evidence": name,
                    "subjectDigest": subject_digest,
                }
            ),
            subject_digest=subject_digest,
            observed_at=observed_at,
            sample_count=1,
        )

    return ProductionEvidenceSet(
        deployment=ordinary("deployment"),
        full_ci_fallback=ordinary("full-ci-fallback"),
        owner_approval=ordinary("owner-approval"),
        provider=ordinary("provider"),
        rollback=ordinary("rollback"),
        shadow=ShadowEvidenceAttestation(
            evidence_digest=hash_object(
                {
                    "authority": "lab-fixture",
                    "evidence": "shadow",
                    "subjectDigest": subject_digest,
                }
            ),
            subject_digest=subject_digest,
            observed_at=observed_at,
            sample_count=1,
            observation_seconds=1,
            unsafe_omission_count=0,
        ),
        stable_gate=ordinary("stable-gate"),
    )
