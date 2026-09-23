"""Immutable subjects and evidence carried by a production admission receipt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import (
    is_workflow_path_identity,
    workflow_path_identity_from_ref,
)
from ci_coordinator.kernel import hash_object
from ci_coordinator.production_admission.relation import ProductionRelationBinding

PRODUCTION_ADMISSION_RECEIPT_SCHEMA: Final = "ci-coordinator-production-admission/v2"
PRODUCTION_ADMISSION_ENVELOPE_SCHEMA: Final = "ci-coordinator-production-admission-envelope/v2"
PRODUCTION_ADMISSION_SUBJECT_SCHEMA: Final = "ci-coordinator-production-admission-subject/v2"
PRODUCTION_ADMISSION_ALGORITHM: Final = "Ed25519"
EVIDENCE_NAMES: Final = (
    "deployment",
    "fullCiFallback",
    "ownerApproval",
    "provider",
    "rollback",
    "shadow",
    "stableGate",
)
_ARTIFACT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ENVIRONMENT_ID = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_GIT_SHA = re.compile(r"[0-9a-f]{40,64}")


@dataclass(frozen=True, slots=True)
class ProductionScopeSubject:
    scope: RepositoryScope
    config_epoch_id: str
    compiled_policy_hash: str
    policy_hash: str
    catalog_hash: str
    target_registry_hash: str
    workflow_refs: tuple[str, ...]
    job_workflow_refs: tuple[str, ...]
    workflow_paths: tuple[str, ...] = ()
    job_workflow_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("production subject scope must be exact")
        for name, value in (
            ("config epoch", self.config_epoch_id),
            ("compiled policy", self.compiled_policy_hash),
            ("policy", self.policy_hash),
            ("catalog", self.catalog_hash),
            ("target registry", self.target_registry_hash),
        ):
            _require_digest(value, f"production subject {name}")
        _require_workflow_refs(self.workflow_refs, "production subject workflow refs")
        _require_workflow_refs(self.job_workflow_refs, "production subject job workflow refs")
        _require_workflow_paths(self.workflow_paths, "production subject workflow paths")
        _require_workflow_paths(
            self.job_workflow_paths,
            "production subject job workflow paths",
        )
        if not any(
            (
                self.workflow_refs,
                self.job_workflow_refs,
                self.workflow_paths,
                self.job_workflow_paths,
            )
        ):
            raise ValueError("production subject requires at least one workflow identity")

    @property
    def scope_digest(self) -> str:
        return hash_object(self.to_mapping())

    def admits(self, candidate: ProductionCandidateSubject) -> bool:
        if type(candidate) is not ProductionCandidateSubject:
            return False
        if (
            self.scope != candidate.scope
            or self.config_epoch_id != candidate.config_epoch_id
            or self.compiled_policy_hash != candidate.compiled_policy_hash
            or self.policy_hash != candidate.policy_hash
            or self.catalog_hash != candidate.catalog_hash
        ):
            return False
        identities = (
            (candidate.workflow_ref, self.workflow_refs, self.workflow_paths),
            (candidate.job_workflow_ref, self.job_workflow_refs, self.job_workflow_paths),
        )
        return all(
            not (refs or paths)
            or (ref is not None and ref in refs)
            or workflow_path_identity_from_ref(ref) in paths
            for ref, refs, paths in identities
        ) and any(refs or paths for _, refs, paths in identities)

    def to_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "configEpochId": self.config_epoch_id,
            "compiledPolicyHash": self.compiled_policy_hash,
            "policyHash": self.policy_hash,
            "catalogHash": self.catalog_hash,
            "targetRegistryHash": self.target_registry_hash,
            "workflowRefs": list(self.workflow_refs),
            "jobWorkflowRefs": list(self.job_workflow_refs),
            "workflowPaths": list(self.workflow_paths),
            "jobWorkflowPaths": list(self.job_workflow_paths),
        }


@dataclass(frozen=True, slots=True)
class ProductionEvidenceAttestation:
    evidence_digest: str
    subject_digest: str
    observed_at: datetime
    sample_count: int

    def __post_init__(self) -> None:
        _require_digest(self.evidence_digest, "production evidence")
        _require_digest(self.subject_digest, "production evidence subject")
        _require_aware_utc(self.observed_at, "production evidence observation")
        if type(self.sample_count) is not int or not 1 <= self.sample_count <= 1_000_000_000:
            raise ValueError("production evidence sample count is outside its bound")

    def to_mapping(self) -> dict[str, object]:
        return {
            "evidenceDigest": self.evidence_digest,
            "subjectDigest": self.subject_digest,
            "observedAt": _timestamp(self.observed_at),
            "sampleCount": self.sample_count,
        }


@dataclass(frozen=True, slots=True)
class ShadowEvidenceAttestation:
    evidence_digest: str
    subject_digest: str
    observed_at: datetime
    sample_count: int
    observation_seconds: int
    unsafe_omission_count: int

    def __post_init__(self) -> None:
        _require_digest(self.evidence_digest, "shadow evidence")
        _require_digest(self.subject_digest, "shadow evidence subject")
        _require_aware_utc(self.observed_at, "shadow evidence observation")
        if type(self.sample_count) is not int or not 1 <= self.sample_count <= 1_000_000_000:
            raise ValueError("shadow evidence sample count is outside its bound")
        if (
            type(self.observation_seconds) is not int
            or not 1 <= self.observation_seconds <= 31_536_000
        ):
            raise ValueError("shadow evidence duration is outside its bound")
        if self.unsafe_omission_count != 0:
            raise ValueError("shadow evidence must prove zero unsafe omissions")

    def to_mapping(self) -> dict[str, object]:
        return {
            "evidenceDigest": self.evidence_digest,
            "subjectDigest": self.subject_digest,
            "observedAt": _timestamp(self.observed_at),
            "sampleCount": self.sample_count,
            "observationSeconds": self.observation_seconds,
            "unsafeOmissionCount": self.unsafe_omission_count,
        }


@dataclass(frozen=True, slots=True)
class ProductionEvidenceSet:
    deployment: ProductionEvidenceAttestation
    full_ci_fallback: ProductionEvidenceAttestation
    owner_approval: ProductionEvidenceAttestation
    provider: ProductionEvidenceAttestation
    rollback: ProductionEvidenceAttestation
    shadow: ShadowEvidenceAttestation
    stable_gate: ProductionEvidenceAttestation

    def __post_init__(self) -> None:
        ordinary = (
            self.deployment,
            self.full_ci_fallback,
            self.owner_approval,
            self.provider,
            self.rollback,
            self.stable_gate,
        )
        if any(type(item) is not ProductionEvidenceAttestation for item in ordinary):
            raise TypeError("production evidence attestations must be exact")
        if type(self.shadow) is not ShadowEvidenceAttestation:
            raise TypeError("production shadow evidence must be exact")

    def assert_subject(self, subject_digest: str) -> None:
        attestations = (
            self.deployment,
            self.full_ci_fallback,
            self.owner_approval,
            self.provider,
            self.rollback,
            self.shadow,
            self.stable_gate,
        )
        if any(item.subject_digest != subject_digest for item in attestations):
            raise ValueError("production evidence does not bind its exact scope subject")

    def to_mapping(self) -> dict[str, object]:
        return {
            "deployment": self.deployment.to_mapping(),
            "fullCiFallback": self.full_ci_fallback.to_mapping(),
            "ownerApproval": self.owner_approval.to_mapping(),
            "provider": self.provider.to_mapping(),
            "rollback": self.rollback.to_mapping(),
            "shadow": self.shadow.to_mapping(),
            "stableGate": self.stable_gate.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class ProductionScopeGrant:
    subject: ProductionScopeSubject
    admission_subject_digest: str
    evidence: ProductionEvidenceSet
    relation: ProductionRelationBinding

    def __post_init__(self) -> None:
        if type(self.subject) is not ProductionScopeSubject:
            raise TypeError("production scope grant subject must be exact")
        _require_digest(self.admission_subject_digest, "production admission subject")
        if type(self.evidence) is not ProductionEvidenceSet:
            raise TypeError("production scope grant evidence must be exact")
        if type(self.relation) is not ProductionRelationBinding:
            raise TypeError("production scope grant relation must be exact")
        self.evidence.assert_subject(self.admission_subject_digest)

    def to_mapping(self) -> dict[str, object]:
        return {
            **self.subject.to_mapping(),
            "admissionSubjectDigest": self.admission_subject_digest,
            "evidence": self.evidence.to_mapping(),
            "relation": self.relation.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class ProductionAdmissionReceipt:
    artifact_digest: str
    release_identity: str
    source_commit: str
    environment_id: str
    rollout_profile_id: str
    issued_at: datetime
    expires_at: datetime
    scope_grants: tuple[ProductionScopeGrant, ...]

    def __post_init__(self) -> None:
        _require_artifact_digest(self.artifact_digest)
        _require_digest(self.release_identity, "production release identity")
        if type(self.source_commit) is not str or _GIT_SHA.fullmatch(self.source_commit) is None:
            raise ValueError("production admission source commit is invalid")
        if (
            type(self.environment_id) is not str
            or _ENVIRONMENT_ID.fullmatch(self.environment_id) is None
        ):
            raise ValueError("production admission environment id is invalid")
        _require_digest(self.rollout_profile_id, "production admission rollout profile")
        _require_aware_utc(self.issued_at, "production admission issuance")
        _require_aware_utc(self.expires_at, "production admission expiry")
        if self.expires_at <= self.issued_at:
            raise ValueError("production admission expiry must follow issuance")
        if type(self.scope_grants) is not tuple or any(
            type(item) is not ProductionScopeGrant for item in self.scope_grants
        ):
            raise TypeError("production admission scope grants must be exact")
        coordinates = tuple(
            (item.subject.scope.installation_id, item.subject.scope.repository_id)
            for item in self.scope_grants
        )
        if not coordinates or tuple(sorted(set(coordinates))) != coordinates:
            raise ValueError("production admission scope grants must be canonical")
        for grant in self.scope_grants:
            expected_digest = production_admission_subject_digest(
                artifact_digest=self.artifact_digest,
                release_identity=self.release_identity,
                source_commit=self.source_commit,
                environment_id=self.environment_id,
                rollout_profile_id=self.rollout_profile_id,
                scope_subject=grant.subject,
                relation=grant.relation,
            )
            if grant.admission_subject_digest != expected_digest:
                raise ValueError("production evidence does not bind the exact release subject")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": PRODUCTION_ADMISSION_RECEIPT_SCHEMA,
            "artifactDigest": self.artifact_digest,
            "releaseIdentity": self.release_identity,
            "sourceCommit": self.source_commit,
            "environmentId": self.environment_id,
            "rolloutProfileId": self.rollout_profile_id,
            "issuedAt": _timestamp(self.issued_at),
            "expiresAt": _timestamp(self.expires_at),
            "scopeGrants": [item.to_mapping() for item in self.scope_grants],
        }


@dataclass(frozen=True, slots=True)
class ProductionCandidateSubject:
    scope: RepositoryScope
    config_epoch_id: str
    compiled_policy_hash: str
    policy_hash: str
    catalog_hash: str
    execution_plan_id: str
    workflow_ref: str | None
    job_workflow_ref: str | None
    workflow_sha: str | None = None
    job_workflow_sha: str | None = None

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("production candidate scope must be exact")
        for name, value in (
            ("config epoch", self.config_epoch_id),
            ("compiled policy", self.compiled_policy_hash),
            ("policy", self.policy_hash),
            ("catalog", self.catalog_hash),
        ):
            _require_digest(value, f"production candidate {name}")
        if type(self.execution_plan_id) is not str or not self.execution_plan_id:
            raise ValueError("production candidate execution plan id must be non-empty")
        _require_optional_workflow_ref(self.workflow_ref, "production candidate workflow ref")
        _require_optional_workflow_ref(
            self.job_workflow_ref,
            "production candidate job workflow ref",
        )
        if self.workflow_ref is None and self.job_workflow_ref is None:
            raise ValueError("production candidate requires an authenticated workflow identity")
        for ref, sha in (
            (self.workflow_ref, self.workflow_sha),
            (self.job_workflow_ref, self.job_workflow_sha),
        ):
            if sha is not None and (
                ref is None
                or type(sha) is not str
                or len(sha) not in (40, 64)
                or _GIT_SHA.fullmatch(sha) is None
            ):
                raise ValueError("production candidate workflow SHA must bind an exact reference")


@dataclass(frozen=True, slots=True)
class ProductionPlanSubject:
    candidate: ProductionCandidateSubject
    target_registry_hash: str
    reconciliation_subject_id: str

    def __post_init__(self) -> None:
        if type(self.candidate) is not ProductionCandidateSubject:
            raise TypeError("production plan candidate subject must be exact")
        _require_digest(self.target_registry_hash, "production plan target registry")
        _require_digest(self.reconciliation_subject_id, "production plan reconciliation subject")


def production_admission_subject_digest(
    *,
    artifact_digest: str,
    release_identity: str,
    source_commit: str,
    environment_id: str,
    rollout_profile_id: str,
    scope_subject: ProductionScopeSubject,
    relation: ProductionRelationBinding,
) -> str:
    """Bind evidence to one exact release, environment, rollout, and repository scope."""

    _require_artifact_digest(artifact_digest)
    _require_digest(release_identity, "production release identity")
    if type(source_commit) is not str or _GIT_SHA.fullmatch(source_commit) is None:
        raise ValueError("production admission source commit is invalid")
    if type(environment_id) is not str or _ENVIRONMENT_ID.fullmatch(environment_id) is None:
        raise ValueError("production admission environment id is invalid")
    _require_digest(rollout_profile_id, "production admission rollout profile")
    if type(scope_subject) is not ProductionScopeSubject:
        raise TypeError("production admission digest requires an exact scope subject")
    if type(relation) is not ProductionRelationBinding:
        raise TypeError("production admission digest requires an exact relation")
    return hash_object(
        {
            "schemaVersion": PRODUCTION_ADMISSION_SUBJECT_SCHEMA,
            "artifactDigest": artifact_digest,
            "releaseIdentity": release_identity,
            "sourceCommit": source_commit,
            "environmentId": environment_id,
            "rolloutProfileId": rollout_profile_id,
            "scopeSubject": scope_subject.to_mapping(),
            "relation": relation.to_mapping(),
        }
    )


def _require_artifact_digest(value: object) -> None:
    if type(value) is not str or _ARTIFACT_DIGEST.fullmatch(value) is None:
        raise ValueError("production artifact digest must be lowercase SHA-256")


def _require_digest(value: object, name: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256 hexadecimal")


def _require_aware_utc(value: object, name: str) -> None:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError(f"{name} must be an aware UTC timestamp")
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{name} must be an aware UTC timestamp")


def _require_workflow_refs(value: object, name: str) -> tuple[str, ...]:
    if (
        type(value) is not tuple
        or len(value) > 64
        or tuple(sorted(set(value))) != value
        or any(
            type(item) is not str
            or not item
            or item != item.strip()
            or len(item.encode("utf-8")) > 512
            for item in value
        )
    ):
        raise ValueError(f"{name} must be a canonical bounded tuple")
    return cast(tuple[str, ...], value)


def _require_workflow_paths(value: object, name: str) -> None:
    if any(not is_workflow_path_identity(item) for item in _require_workflow_refs(value, name)):
        raise ValueError(f"{name} must contain exact repository workflow paths")


def _require_optional_workflow_ref(value: object, name: str) -> None:
    if value is not None and (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > 512
    ):
        raise ValueError(f"{name} must be absent or bounded text")


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
