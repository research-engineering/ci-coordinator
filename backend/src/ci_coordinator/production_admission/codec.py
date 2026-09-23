"""Strict decoding and cryptographic admission of production receipts."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_public_key,
)

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import (
    Clock,
    StrictJsonError,
    canonical_json,
    hash_object,
    load_strict_json,
)
from ci_coordinator.production_admission._fields import (
    _exact_object,
    _nonnegative_integer,
    _positive_integer,
    _text,
    _timestamp,
)
from ci_coordinator.production_admission.authority import (
    ProductionAdmissionGrant,
    _issue_production_admission_grant,
)
from ci_coordinator.production_admission.model import (
    EVIDENCE_NAMES,
    PRODUCTION_ADMISSION_ALGORITHM,
    PRODUCTION_ADMISSION_ENVELOPE_SCHEMA,
    PRODUCTION_ADMISSION_RECEIPT_SCHEMA,
    ProductionAdmissionReceipt,
    ProductionEvidenceAttestation,
    ProductionEvidenceSet,
    ProductionScopeGrant,
    ProductionScopeSubject,
    ShadowEvidenceAttestation,
)
from ci_coordinator.production_admission.registration import (
    _issue_production_admission_registration,
)
from ci_coordinator.production_admission.relation import (
    PRODUCTION_RELATION_BINDING_SCHEMA,
    ProductionRelationBinding,
)

MAX_PRODUCTION_ADMISSION_BYTES = 262_144
MAX_RECEIPT_LIFETIME = timedelta(days=7)
MAX_EVIDENCE_AGE = timedelta(days=30)
MAX_CLOCK_SKEW = timedelta(minutes=5)
_KEY_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_SIGNATURE = re.compile(r"[A-Za-z0-9_-]{85}[AQgw]")


@dataclass(frozen=True, slots=True)
class ProductionAdmissionRejection:
    code: Literal[
        "production_admission_binding_mismatch",
        "production_admission_expired",
        "production_admission_invalid",
        "production_admission_key_mismatch",
        "production_admission_not_yet_valid",
        "production_admission_signature_invalid",
    ]


def admit_production_admission(
    content: bytes,
    *,
    public_key_pem: bytes,
    expected_key_id: str,
    expected_artifact_digest: str,
    expected_release_identity: str,
    expected_source_commit: str,
    expected_environment_id: str,
    expected_rollout_profile_id: str,
    expected_repository_scopes: tuple[RepositoryScope, ...],
    minimum_remaining_seconds: int,
    clock: Clock,
) -> ProductionAdmissionGrant | ProductionAdmissionRejection:
    try:
        root = _canonical_envelope(content)
    except (StrictJsonError, TypeError, ValueError):
        return ProductionAdmissionRejection("production_admission_invalid")
    if root["keyId"] != expected_key_id:
        return ProductionAdmissionRejection("production_admission_key_mismatch")
    public_key = _admitted_public_key(public_key_pem)
    if public_key is None or not _signature_valid(root, public_key):
        return ProductionAdmissionRejection("production_admission_signature_invalid")
    try:
        receipt = _receipt(root["receipt"])
    except (KeyError, TypeError, ValueError):
        return ProductionAdmissionRejection("production_admission_invalid")
    now = clock.now()
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        return ProductionAdmissionRejection("production_admission_invalid")
    now = now.astimezone(UTC)
    if receipt.issued_at > now + MAX_CLOCK_SKEW:
        return ProductionAdmissionRejection("production_admission_not_yet_valid")
    if now + timedelta(seconds=minimum_remaining_seconds) >= receipt.expires_at:
        return ProductionAdmissionRejection("production_admission_expired")
    if receipt.expires_at - receipt.issued_at > MAX_RECEIPT_LIFETIME:
        return ProductionAdmissionRejection("production_admission_invalid")
    if any(
        observed_at > receipt.issued_at or receipt.issued_at - observed_at > MAX_EVIDENCE_AGE
        for grant in receipt.scope_grants
        for observed_at in _evidence_observation_times(grant.evidence)
    ):
        return ProductionAdmissionRejection("production_admission_invalid")
    scopes = tuple(item.subject.scope for item in receipt.scope_grants)
    if (
        receipt.artifact_digest != expected_artifact_digest
        or receipt.release_identity != expected_release_identity
        or receipt.source_commit != expected_source_commit
        or receipt.environment_id != expected_environment_id
        or receipt.rollout_profile_id != expected_rollout_profile_id
        or scopes != expected_repository_scopes
    ):
        return ProductionAdmissionRejection("production_admission_binding_mismatch")
    try:
        authority_id = "production_admission_" + hash_object(root)[:32]
        registration = _issue_production_admission_registration(
            authority_id=authority_id,
            key_id=_text(root["keyId"]),
            public_key_spki_der=public_key.public_bytes(
                Encoding.DER,
                PublicFormat.SubjectPublicKeyInfo,
            ),
            envelope_canonical_json=content,
            issued_at=receipt.issued_at,
            expires_at=receipt.expires_at,
            scope_bindings=tuple(
                (
                    grant.subject.scope,
                    grant.admission_subject_digest,
                    grant.subject.config_epoch_id,
                    grant.subject.target_registry_hash,
                )
                for grant in receipt.scope_grants
            ),
        )
        return _issue_production_admission_grant(
            authority_id=authority_id,
            receipt=receipt,
            registration=registration,
            clock=clock,
            minimum_remaining_seconds=minimum_remaining_seconds,
        )
    except (TypeError, ValueError):
        return ProductionAdmissionRejection("production_admission_invalid")


def _canonical_envelope(content: bytes) -> dict[str, object]:
    value = load_strict_json(content, max_bytes=MAX_PRODUCTION_ADMISSION_BYTES)
    root = _exact_object(
        value,
        {"algorithm", "keyId", "receipt", "schemaVersion", "signature"},
    )
    if canonical_json(root) + b"\n" != content:
        raise ValueError("production admission envelope is not canonical")
    if (
        root["schemaVersion"] != PRODUCTION_ADMISSION_ENVELOPE_SCHEMA
        or root["algorithm"] != PRODUCTION_ADMISSION_ALGORITHM
        or type(root["keyId"]) is not str
        or _KEY_ID.fullmatch(root["keyId"]) is None
        or type(root["receipt"]) is not dict
        or type(root["signature"]) is not str
        or _SIGNATURE.fullmatch(root["signature"]) is None
    ):
        raise ValueError("production admission envelope identity is invalid")
    return root


def _admitted_public_key(public_key_pem: bytes) -> Ed25519PublicKey | None:
    if type(public_key_pem) is not bytes or not 1 <= len(public_key_pem) <= 16_384:
        return None
    try:
        public_key = load_pem_public_key(public_key_pem)
        return public_key if isinstance(public_key, Ed25519PublicKey) else None
    except (TypeError, ValueError):
        return None


def _signature_valid(root: dict[str, object], public_key: Ed25519PublicKey) -> bool:
    try:
        signature_text = cast(str, root["signature"])
        signature = base64.b64decode(signature_text + "==", altchars=b"-_", validate=True)
        canonical_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        if len(signature) != 64 or canonical_signature != signature_text:
            return False
        public_key.verify(signature, canonical_json(_unsigned_mapping(root)))
    except (InvalidSignature, TypeError, ValueError):
        return False
    return True


def _unsigned_mapping(root: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": root["schemaVersion"],
        "keyId": root["keyId"],
        "algorithm": root["algorithm"],
        "receipt": root["receipt"],
    }


def _receipt(value: object) -> ProductionAdmissionReceipt:
    record = _exact_object(
        value,
        {
            "artifactDigest",
            "environmentId",
            "expiresAt",
            "issuedAt",
            "releaseIdentity",
            "rolloutProfileId",
            "schemaVersion",
            "scopeGrants",
            "sourceCommit",
        },
    )
    if record["schemaVersion"] != PRODUCTION_ADMISSION_RECEIPT_SCHEMA:
        raise ValueError("production admission receipt schema is unsupported")
    return ProductionAdmissionReceipt(
        artifact_digest=_text(record["artifactDigest"]),
        release_identity=_text(record["releaseIdentity"]),
        source_commit=_text(record["sourceCommit"]),
        environment_id=_text(record["environmentId"]),
        rollout_profile_id=_text(record["rolloutProfileId"]),
        issued_at=_timestamp(record["issuedAt"]),
        expires_at=_timestamp(record["expiresAt"]),
        scope_grants=_scope_grants(record["scopeGrants"]),
    )


def _scope_grants(value: object) -> tuple[ProductionScopeGrant, ...]:
    if type(value) is not list or not 1 <= len(value) <= 1_024:
        raise ValueError("production admission scope grant count is invalid")
    return tuple(_scope_grant(item) for item in value)


def _scope_grant(value: object) -> ProductionScopeGrant:
    record = _exact_object(
        value,
        {
            "admissionSubjectDigest",
            "catalogHash",
            "compiledPolicyHash",
            "configEpochId",
            "evidence",
            "installationId",
            "jobWorkflowPaths",
            "jobWorkflowRefs",
            "policyHash",
            "repositoryId",
            "relation",
            "targetRegistryHash",
            "workflowPaths",
            "workflowRefs",
        },
    )
    subject = ProductionScopeSubject(
        scope=RepositoryScope(
            _positive_integer(record["installationId"]),
            _positive_integer(record["repositoryId"]),
        ),
        config_epoch_id=_text(record["configEpochId"]),
        compiled_policy_hash=_text(record["compiledPolicyHash"]),
        policy_hash=_text(record["policyHash"]),
        catalog_hash=_text(record["catalogHash"]),
        target_registry_hash=_text(record["targetRegistryHash"]),
        workflow_refs=_workflow_refs(record["workflowRefs"]),
        job_workflow_refs=_workflow_refs(record["jobWorkflowRefs"]),
        workflow_paths=_workflow_refs(record["workflowPaths"]),
        job_workflow_paths=_workflow_refs(record["jobWorkflowPaths"]),
    )
    return ProductionScopeGrant(
        subject=subject,
        admission_subject_digest=_text(record["admissionSubjectDigest"]),
        evidence=_evidence_set(record["evidence"]),
        relation=_relation_binding(record["relation"]),
    )


def _relation_binding(value: object) -> ProductionRelationBinding:
    record = _exact_object(
        value,
        {
            "schemaVersion",
            "generation",
            "predecessorGeneration",
            "evidenceBundleDigest",
            "relationSubjectDigest",
            "relationEpochDigest",
            "relationClosureDigest",
            "workflowManifestDigest",
            "sourceBindingDigest",
            "providerAuthorityDigest",
            "ownerEpochDigest",
        },
    )
    if record["schemaVersion"] != PRODUCTION_RELATION_BINDING_SCHEMA:
        raise ValueError("production relation schema is unsupported")
    return ProductionRelationBinding(
        generation=_positive_integer(record["generation"]),
        predecessor_generation=_nonnegative_integer(record["predecessorGeneration"]),
        evidence_bundle_digest=_text(record["evidenceBundleDigest"]),
        relation_subject_digest=_text(record["relationSubjectDigest"]),
        relation_epoch_digest=_text(record["relationEpochDigest"]),
        relation_closure_digest=_text(record["relationClosureDigest"]),
        workflow_manifest_digest=_text(record["workflowManifestDigest"]),
        source_binding_digest=_text(record["sourceBindingDigest"]),
        provider_authority_digest=_text(record["providerAuthorityDigest"]),
        owner_epoch_digest=_text(record["ownerEpochDigest"]),
    )


def _evidence_set(value: object) -> ProductionEvidenceSet:
    record = _exact_object(value, set(EVIDENCE_NAMES))
    return ProductionEvidenceSet(
        deployment=_ordinary_evidence(record["deployment"]),
        full_ci_fallback=_ordinary_evidence(record["fullCiFallback"]),
        owner_approval=_ordinary_evidence(record["ownerApproval"]),
        provider=_ordinary_evidence(record["provider"]),
        rollback=_ordinary_evidence(record["rollback"]),
        shadow=_shadow_evidence(record["shadow"]),
        stable_gate=_ordinary_evidence(record["stableGate"]),
    )


def _ordinary_evidence(value: object) -> ProductionEvidenceAttestation:
    record = _exact_object(
        value,
        {"evidenceDigest", "observedAt", "sampleCount", "subjectDigest"},
    )
    return ProductionEvidenceAttestation(
        evidence_digest=_text(record["evidenceDigest"]),
        subject_digest=_text(record["subjectDigest"]),
        observed_at=_timestamp(record["observedAt"]),
        sample_count=_positive_integer(record["sampleCount"]),
    )


def _shadow_evidence(value: object) -> ShadowEvidenceAttestation:
    record = _exact_object(
        value,
        {
            "evidenceDigest",
            "observationSeconds",
            "observedAt",
            "sampleCount",
            "subjectDigest",
            "unsafeOmissionCount",
        },
    )
    return ShadowEvidenceAttestation(
        evidence_digest=_text(record["evidenceDigest"]),
        subject_digest=_text(record["subjectDigest"]),
        observed_at=_timestamp(record["observedAt"]),
        sample_count=_positive_integer(record["sampleCount"]),
        observation_seconds=_positive_integer(record["observationSeconds"]),
        unsafe_omission_count=_nonnegative_integer(record["unsafeOmissionCount"]),
    )


def _evidence_observation_times(evidence: ProductionEvidenceSet) -> tuple[datetime, ...]:
    return (
        evidence.deployment.observed_at,
        evidence.full_ci_fallback.observed_at,
        evidence.owner_approval.observed_at,
        evidence.provider.observed_at,
        evidence.rollback.observed_at,
        evidence.shadow.observed_at,
        evidence.stable_gate.observed_at,
    )


def _workflow_refs(value: object) -> tuple[str, ...]:
    if type(value) is not list or len(value) > 64:
        raise ValueError("production workflow reference count is invalid")
    result = tuple(_text(item) for item in value)
    if tuple(sorted(set(result))) != result:
        raise ValueError("production workflow references are noncanonical")
    return result


def _bounded_text(value: object, *, maximum: int) -> bool:
    return type(value) is str and 1 <= len(value.encode("utf-8")) <= maximum
