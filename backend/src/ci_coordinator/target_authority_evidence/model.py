"""Closed values for one replayable unactivated authority-evidence bundle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, cast

from ci_coordinator.kernel.canonical_json import JsonResourceLimits, bounded_canonical_json
from ci_coordinator.kernel.hashing import sha256_hex
from ci_coordinator.kernel.strict_json import StrictJsonError, load_strict_json
from ci_coordinator.target_authority_producers.model import (
    ObservationCandidateSet,
    OwnerProjectionPolicy,
    RegistrationCandidateSet,
)
from ci_coordinator.target_authority_relation import (
    AuthorityTransitionDelta,
    ExpectedTargetAuthorityRelation,
    PhaseZeroBaseline,
    ProjectionLedger,
    RawCandidateDomain,
    TargetAuthorityEpoch,
    TargetAuthorityInventory,
    TargetAuthoritySubject,
    UnactivatedRelationClosure,
)
from ci_coordinator.workflow_authority import WorkflowAuthorityManifest, WorkflowSourceBinding

type EvidenceRole = Literal[
    "phase_zero_baseline",
    "transition_delta",
    "expected_relation",
    "workflow_manifest",
    "source_binding",
    "registration_candidates",
    "observation_candidates",
    "owner_projection_policy",
    "registration_inventory",
    "observation_inventory",
    "raw_candidate_domain",
    "projection_ledger",
    "relation_closure",
]

TARGET_AUTHORITY_EVIDENCE_SCHEMA: Final = "ci-coordinator.unactivated-target-authority-evidence/v1"
TARGET_AUTHORITY_EVIDENCE_ARTIFACT_SCHEMA: Final = (
    "ci-coordinator.target-authority-evidence-artifact/v1"
)
EVIDENCE_ROLES: Final[tuple[EvidenceRole, ...]] = (
    "phase_zero_baseline",
    "transition_delta",
    "expected_relation",
    "workflow_manifest",
    "source_binding",
    "registration_candidates",
    "observation_candidates",
    "owner_projection_policy",
    "registration_inventory",
    "observation_inventory",
    "raw_candidate_domain",
    "projection_ledger",
    "relation_closure",
)

MAX_ARTIFACT_DOCUMENT_BYTES: Final = 67_108_864
MAX_AGGREGATE_ARTIFACT_BYTES: Final = 100_663_296
MAX_EVIDENCE_BUNDLE_BYTES: Final = 134_217_728
EVIDENCE_JSON_LIMITS: Final = JsonResourceLimits(max_depth=24, max_nodes=8_388_608)


class TargetAuthorityEvidenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    role: EvidenceRole
    owner_schema: str
    content: bytes

    def __post_init__(self) -> None:
        if self.role not in EVIDENCE_ROLES:
            raise ValueError("evidence artifact role is not admitted")
        if (
            type(self.owner_schema) is not str
            or not self.owner_schema
            or not self.owner_schema.isascii()
            or "\0" in self.owner_schema
            or len(self.owner_schema) > 256
        ):
            raise ValueError("evidence artifact owner schema is not bounded ASCII")
        if (
            type(self.content) is not bytes
            or not self.content
            or len(self.content) > MAX_ARTIFACT_DOCUMENT_BYTES
        ):
            raise ValueError("evidence artifact content is not bounded exact bytes")
        try:
            document = load_strict_json(
                self.content,
                max_bytes=MAX_ARTIFACT_DOCUMENT_BYTES,
                resource_limits=EVIDENCE_JSON_LIMITS,
            )
            if type(document) is not dict:
                raise TypeError("evidence artifact document must be an exact object")
            mapping = cast(dict[str, object], document)
            if mapping.get("schemaVersion") != self.owner_schema:
                raise ValueError("evidence artifact schema does not match its document")
            canonical = bounded_canonical_json(
                mapping,
                max_bytes=MAX_ARTIFACT_DOCUMENT_BYTES,
                resource_limits=EVIDENCE_JSON_LIMITS,
            )
        except (StrictJsonError, TypeError, ValueError) as error:
            raise ValueError("evidence artifact is not admitted canonical JSON") from error
        if canonical != self.content:
            raise ValueError("evidence artifact content must already be canonical")

    @property
    def sha256(self) -> str:
        return sha256_hex(self.content)

    @property
    def artifact_digest(self) -> str:
        return sha256_hex(
            TARGET_AUTHORITY_EVIDENCE_ARTIFACT_SCHEMA.encode("ascii")
            + b"\0"
            + self.role.encode("ascii")
            + b"\0"
            + self.owner_schema.encode("ascii")
            + b"\0"
            + self.content
        )

    @property
    def document(self) -> dict[str, object]:
        value = load_strict_json(
            self.content,
            max_bytes=MAX_ARTIFACT_DOCUMENT_BYTES,
            resource_limits=EVIDENCE_JSON_LIMITS,
        )
        if type(value) is not dict:
            raise AssertionError("validated evidence artifact lost its object document")
        return cast(dict[str, object], value)

    def to_mapping(self) -> dict[str, object]:
        return {
            "role": self.role,
            "ownerSchema": self.owner_schema,
            "byteCount": len(self.content),
            "sha256": self.sha256,
            "artifactDigest": self.artifact_digest,
            "document": self.document,
        }


@dataclass(frozen=True, slots=True)
class UnactivatedEvidenceBundle:
    subject: TargetAuthoritySubject
    epoch: TargetAuthorityEpoch
    artifacts: tuple[EvidenceArtifact, ...]

    def __post_init__(self) -> None:
        if type(self.subject) is not TargetAuthoritySubject:
            raise TypeError("evidence bundle requires an exact target-authority subject")
        if (
            type(self.epoch) is not TargetAuthorityEpoch
            or self.epoch.phase != "adapted_target"
            or self.epoch.has_unknown
        ):
            raise ValueError("evidence bundle requires one complete adapted-target epoch")
        if type(self.artifacts) is not tuple or any(
            type(artifact) is not EvidenceArtifact for artifact in self.artifacts
        ):
            raise TypeError("evidence bundle artifacts must be an exact tuple")
        roles = tuple(artifact.role for artifact in self.artifacts)
        if roles != EVIDENCE_ROLES:
            raise ValueError("evidence bundle artifact roles are not exactly closed and canonical")
        if sum(len(artifact.content) for artifact in self.artifacts) > (
            MAX_AGGREGATE_ARTIFACT_BYTES
        ):
            raise ValueError("evidence bundle artifact bytes exceed the aggregate bound")
        _ = self.canonical_bytes

    @property
    def bundle_digest(self) -> str:
        return sha256_hex(
            TARGET_AUTHORITY_EVIDENCE_SCHEMA.encode("ascii")
            + b"\0"
            + bounded_canonical_json(
                self.body_mapping(),
                max_bytes=MAX_EVIDENCE_BUNDLE_BYTES,
                resource_limits=EVIDENCE_JSON_LIMITS,
            )
        )

    @property
    def canonical_bytes(self) -> bytes:
        return bounded_canonical_json(
            self.to_mapping(),
            max_bytes=MAX_EVIDENCE_BUNDLE_BYTES,
            resource_limits=EVIDENCE_JSON_LIMITS,
        )

    def body_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": TARGET_AUTHORITY_EVIDENCE_SCHEMA,
            "authorityState": "unactivated",
            "subject": self.subject.to_mapping(),
            "epoch": self.epoch.to_mapping(),
            "artifacts": [artifact.to_mapping() for artifact in self.artifacts],
        }

    def to_mapping(self) -> dict[str, object]:
        return {**self.body_mapping(), "bundleDigest": self.bundle_digest}


@dataclass(frozen=True, slots=True)
class TargetAuthorityEvidenceValues:
    phase_zero_baseline: PhaseZeroBaseline
    transition_delta: AuthorityTransitionDelta
    expected_relation: ExpectedTargetAuthorityRelation
    workflow_manifest: WorkflowAuthorityManifest
    source_binding: WorkflowSourceBinding
    registration_candidates: RegistrationCandidateSet
    observation_candidates: ObservationCandidateSet
    owner_projection_policy: OwnerProjectionPolicy
    registration_inventory: TargetAuthorityInventory
    observation_inventory: TargetAuthorityInventory
    raw_candidate_domain: RawCandidateDomain
    projection_ledger: ProjectionLedger
    relation_closure: UnactivatedRelationClosure

    def __post_init__(self) -> None:
        expected_types = (
            (self.phase_zero_baseline, PhaseZeroBaseline),
            (self.transition_delta, AuthorityTransitionDelta),
            (self.expected_relation, ExpectedTargetAuthorityRelation),
            (self.workflow_manifest, WorkflowAuthorityManifest),
            (self.source_binding, WorkflowSourceBinding),
            (self.registration_candidates, RegistrationCandidateSet),
            (self.observation_candidates, ObservationCandidateSet),
            (self.owner_projection_policy, OwnerProjectionPolicy),
            (self.registration_inventory, TargetAuthorityInventory),
            (self.observation_inventory, TargetAuthorityInventory),
            (self.raw_candidate_domain, RawCandidateDomain),
            (self.projection_ledger, ProjectionLedger),
            (self.relation_closure, UnactivatedRelationClosure),
        )
        if any(type(value) is not expected for value, expected in expected_types):
            raise TypeError("target-authority evidence values require exact owner types")


@dataclass(frozen=True, slots=True)
class AdmittedTargetAuthorityEvidence:
    bundle: UnactivatedEvidenceBundle
    values: TargetAuthorityEvidenceValues

    def __post_init__(self) -> None:
        if type(self.bundle) is not UnactivatedEvidenceBundle:
            raise TypeError("admitted evidence requires an exact bundle")
        if type(self.values) is not TargetAuthorityEvidenceValues:
            raise TypeError("admitted evidence requires exact decoded owner values")
        if (
            self.bundle.subject != self.values.expected_relation.subject
            or self.bundle.epoch != self.values.expected_relation.epoch
        ):
            raise ValueError("admitted evidence bundle crosses its decoded relation identity")
