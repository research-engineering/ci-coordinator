"""Independent candidate, owner-classification, and producer result values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.kernel.canonical_json import JsonResourceLimits, bounded_canonical_json
from ci_coordinator.kernel.hashing import sha256_hex
from ci_coordinator.kernel.ordering import utf16_sort_key
from ci_coordinator.target_authority_relation import (
    AuthorityFieldEntry,
    CandidateProjection,
    ProjectionLedger,
    RawCandidate,
    RawCandidateDomain,
    TargetAuthorityInventory,
    TargetAuthorityKey,
    TargetAuthorityRow,
    TargetAuthorityRowFamily,
    TargetAuthoritySubject,
    row_field_names,
)
from ci_coordinator.target_authority_relation.limits import (
    MAX_RAW_CANDIDATES,
    MAX_SOURCE_LOCATOR_BYTES,
)

type ObservedCandidateKind = TargetAuthorityRowFamily | Literal["workflow_blob"]
type AuthorityDisposition = Literal["authority", "owner_approved_non_authority"]

OBSERVATION_CANDIDATE_SET_SCHEMA: Final = "ci-coordinator.observation-candidate-set/v1"
OBSERVATION_AUTHORITY_DOMAIN_SCHEMA: Final = "ci-coordinator.observation-authority-domain/v1"
OWNER_PROJECTION_POLICY_SCHEMA: Final = "ci-coordinator.owner-projection-policy/v1"
REGISTRATION_CANDIDATE_SET_SCHEMA: Final = "ci-coordinator.registration-candidate-set/v1"

_MAX_CANDIDATE_DOCUMENT_BYTES = 67_108_864
_CANDIDATE_JSON_LIMITS: Final = JsonResourceLimits(
    max_depth=20,
    max_nodes=2_097_152,
)


class TargetAuthorityProducerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ObservedCandidate:
    candidate_id: str
    kind: ObservedCandidateKind
    source_locator: str
    fields: tuple[AuthorityFieldEntry, ...]

    def __post_init__(self) -> None:
        _require_text(self.candidate_id, "candidate id", maximum_bytes=2_048)
        _require_text(
            self.source_locator,
            "candidate source locator",
            maximum_bytes=MAX_SOURCE_LOCATOR_BYTES,
        )
        if type(self.fields) is not tuple or any(
            type(field) is not AuthorityFieldEntry for field in self.fields
        ):
            raise TypeError("observed candidate fields must be an exact tuple")
        names = tuple(field.name for field in self.fields)
        if names != row_field_names(self.projected_family):
            raise ValueError("observed candidate fields do not match its row family")
        _ = self.evidence_digest

    @property
    def projected_family(self) -> TargetAuthorityRowFamily:
        return "workflow" if self.kind == "workflow_blob" else self.kind

    @property
    def evidence_digest(self) -> str:
        return sha256_hex(
            bounded_canonical_json(
                self.to_evidence_mapping(),
                max_bytes=8_388_608,
                resource_limits=_CANDIDATE_JSON_LIMITS,
            )
        )

    @property
    def raw_candidate(self) -> RawCandidate:
        return RawCandidate(
            candidate_id=self.candidate_id,
            kind=self.kind,
            source_locator=self.source_locator,
            state="present",
            evidence_digest=self.evidence_digest,
        )

    def to_evidence_mapping(self) -> dict[str, object]:
        return {
            "candidateId": self.candidate_id,
            "kind": self.kind,
            "sourceLocator": self.source_locator,
            "fields": [field.to_mapping() for field in self.fields],
        }


@dataclass(frozen=True, slots=True)
class ObservationCandidateSet:
    scope: RepositoryScope
    source_commit_id: str
    source_binding_digest: str
    workflow_manifest_digest: str
    discovery_report_digest: str
    provider_authority_digest: str
    target_artifact_epoch_digest: str
    target_policy_digest: str
    validation_catalog_digest: str
    target_registry_digest: str
    candidates: tuple[ObservedCandidate, ...]

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("observation candidate set requires an exact repository scope")
        if (
            type(self.source_commit_id) is not str
            or len(self.source_commit_id) != 40
            or any(character not in "0123456789abcdef" for character in self.source_commit_id)
        ):
            raise ValueError("observation candidate set requires a lowercase SHA-1 commit id")
        for value, label in (
            (self.source_binding_digest, "source binding"),
            (self.workflow_manifest_digest, "workflow manifest"),
            (self.discovery_report_digest, "workflow discovery report"),
            (self.provider_authority_digest, "provider authority"),
            (self.target_artifact_epoch_digest, "target artifact epoch"),
            (self.target_policy_digest, "target policy"),
            (self.validation_catalog_digest, "validation catalog"),
            (self.target_registry_digest, "target registry"),
        ):
            _require_digest(value, label)
        if (
            type(self.candidates) is not tuple
            or not 1 <= len(self.candidates) <= MAX_RAW_CANDIDATES
            or any(type(candidate) is not ObservedCandidate for candidate in self.candidates)
        ):
            raise TypeError("observation candidates must be a bounded non-empty exact tuple")
        ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if ids != tuple(sorted(set(ids), key=utf16_sort_key)):
            raise ValueError("observation candidate ids must be canonical and unique")
        _ = self.candidate_set_digest

    @property
    def candidate_set_digest(self) -> str:
        return sha256_hex(
            OBSERVATION_CANDIDATE_SET_SCHEMA.encode("ascii") + b"\0" + self.canonical_bytes
        )

    @property
    def authority_domain_digest(self) -> str:
        return sha256_hex(
            OBSERVATION_AUTHORITY_DOMAIN_SCHEMA.encode("ascii")
            + b"\0"
            + bounded_canonical_json(
                {
                    "schemaVersion": OBSERVATION_AUTHORITY_DOMAIN_SCHEMA,
                    "scope": {
                        "installationId": self.scope.installation_id,
                        "repositoryId": self.scope.repository_id,
                    },
                    "workflowManifestDigest": self.workflow_manifest_digest,
                    "providerAuthorityDigest": self.provider_authority_digest,
                    "targetArtifactEpochDigest": self.target_artifact_epoch_digest,
                    "targetPolicyDigest": self.target_policy_digest,
                    "validationCatalogDigest": self.validation_catalog_digest,
                    "targetRegistryDigest": self.target_registry_digest,
                    "candidates": [
                        candidate.to_evidence_mapping() for candidate in self.candidates
                    ],
                },
                max_bytes=_MAX_CANDIDATE_DOCUMENT_BYTES,
                resource_limits=_CANDIDATE_JSON_LIMITS,
            )
        )

    @property
    def canonical_bytes(self) -> bytes:
        return bounded_canonical_json(
            self.to_mapping(),
            max_bytes=_MAX_CANDIDATE_DOCUMENT_BYTES,
            resource_limits=_CANDIDATE_JSON_LIMITS,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": OBSERVATION_CANDIDATE_SET_SCHEMA,
            "scope": {
                "installationId": self.scope.installation_id,
                "repositoryId": self.scope.repository_id,
            },
            "sourceCommitId": self.source_commit_id,
            "sourceBindingDigest": self.source_binding_digest,
            "workflowManifestDigest": self.workflow_manifest_digest,
            "discoveryReportDigest": self.discovery_report_digest,
            "providerAuthorityDigest": self.provider_authority_digest,
            "targetArtifactEpochDigest": self.target_artifact_epoch_digest,
            "targetPolicyDigest": self.target_policy_digest,
            "validationCatalogDigest": self.validation_catalog_digest,
            "targetRegistryDigest": self.target_registry_digest,
            "candidates": [candidate.to_evidence_mapping() for candidate in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class RegistrationCandidateSet:
    scope: RepositoryScope
    target_artifact_epoch_digest: str
    target_policy_digest: str
    validation_catalog_digest: str
    target_registry_digest: str
    candidates: tuple[ObservedCandidate, ...]

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("registration candidate set requires an exact repository scope")
        for value, label in (
            (self.target_artifact_epoch_digest, "target artifact epoch"),
            (self.target_policy_digest, "target policy"),
            (self.validation_catalog_digest, "validation catalog"),
            (self.target_registry_digest, "target registry"),
        ):
            _require_digest(value, label)
        if (
            type(self.candidates) is not tuple
            or not 1 <= len(self.candidates) <= MAX_RAW_CANDIDATES
            or any(type(candidate) is not ObservedCandidate for candidate in self.candidates)
        ):
            raise TypeError("registration candidates must be a bounded non-empty exact tuple")
        ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if ids != tuple(sorted(set(ids), key=utf16_sort_key)):
            raise ValueError("registration candidate ids must be canonical and unique")
        _ = self.candidate_set_digest

    @property
    def candidate_set_digest(self) -> str:
        return sha256_hex(
            REGISTRATION_CANDIDATE_SET_SCHEMA.encode("ascii") + b"\0" + self.canonical_bytes
        )

    @property
    def canonical_bytes(self) -> bytes:
        return bounded_canonical_json(
            self.to_mapping(),
            max_bytes=_MAX_CANDIDATE_DOCUMENT_BYTES,
            resource_limits=_CANDIDATE_JSON_LIMITS,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": REGISTRATION_CANDIDATE_SET_SCHEMA,
            "scope": {
                "installationId": self.scope.installation_id,
                "repositoryId": self.scope.repository_id,
            },
            "targetArtifactEpochDigest": self.target_artifact_epoch_digest,
            "targetPolicyDigest": self.target_policy_digest,
            "validationCatalogDigest": self.validation_catalog_digest,
            "targetRegistryDigest": self.target_registry_digest,
            "candidates": [candidate.to_evidence_mapping() for candidate in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class ProjectionRule:
    candidate_id: str
    evidence_digest: str
    row_key: TargetAuthorityKey
    disposition: AuthorityDisposition
    semantic_owner: str
    source_locator: str

    def __post_init__(self) -> None:
        _require_text(self.candidate_id, "projection candidate id", maximum_bytes=2_048)
        _require_digest(self.evidence_digest, "projection candidate evidence")
        if type(self.row_key) is not TargetAuthorityKey:
            raise TypeError("projection rule requires an exact target-authority key")
        if self.disposition not in {"authority", "owner_approved_non_authority"}:
            raise ValueError("projection rule disposition is not admitted")
        _require_text(self.semantic_owner, "projection semantic owner", maximum_bytes=512)
        _require_text(
            self.source_locator,
            "projection source locator",
            maximum_bytes=MAX_SOURCE_LOCATOR_BYTES,
        )

    @property
    def sort_key(self) -> bytes:
        return utf16_sort_key(self.candidate_id)

    def to_mapping(self) -> dict[str, object]:
        return {
            "candidateId": self.candidate_id,
            "evidenceDigest": self.evidence_digest,
            "rowKey": self.row_key.to_mapping(),
            "disposition": self.disposition,
            "semanticOwner": self.semantic_owner,
            "sourceLocator": self.source_locator,
        }


@dataclass(frozen=True, slots=True)
class OwnerProjectionPolicy:
    subject: TargetAuthoritySubject
    workflow_manifest_digest: str
    target_artifact_epoch_digest: str
    observation_authority_domain_digest: str
    registration_declaration_domain_digest: str
    rules: tuple[ProjectionRule, ...]

    def __post_init__(self) -> None:
        if type(self.subject) is not TargetAuthoritySubject:
            raise TypeError("owner projection policy requires an exact subject")
        _require_digest(self.workflow_manifest_digest, "owner policy workflow manifest")
        _require_digest(self.target_artifact_epoch_digest, "owner policy target artifact epoch")
        _require_digest(
            self.observation_authority_domain_digest,
            "owner policy observation authority domain",
        )
        _require_digest(
            self.registration_declaration_domain_digest,
            "owner policy registration declaration domain",
        )
        if (
            type(self.rules) is not tuple
            or not 1 <= len(self.rules) <= MAX_RAW_CANDIDATES
            or any(type(rule) is not ProjectionRule for rule in self.rules)
        ):
            raise TypeError("owner projection rules must be a bounded non-empty exact tuple")
        keys = tuple(rule.sort_key for rule in self.rules)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("owner projection rules must classify candidates canonically once")
        _ = self.policy_digest

    @property
    def policy_digest(self) -> str:
        return sha256_hex(
            OWNER_PROJECTION_POLICY_SCHEMA.encode("ascii") + b"\0" + self.canonical_bytes
        )

    @property
    def canonical_bytes(self) -> bytes:
        return bounded_canonical_json(
            self.to_mapping(),
            max_bytes=_MAX_CANDIDATE_DOCUMENT_BYTES,
            resource_limits=_CANDIDATE_JSON_LIMITS,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": OWNER_PROJECTION_POLICY_SCHEMA,
            "subject": self.subject.to_mapping(),
            "workflowManifestDigest": self.workflow_manifest_digest,
            "targetArtifactEpochDigest": self.target_artifact_epoch_digest,
            "observationAuthorityDomainDigest": self.observation_authority_domain_digest,
            "registrationDeclarationDomainDigest": (self.registration_declaration_domain_digest),
            "rules": [rule.to_mapping() for rule in self.rules],
        }


@dataclass(frozen=True, slots=True)
class ObservationProduction:
    raw_domain: RawCandidateDomain
    inventory: TargetAuthorityInventory
    projection_ledger: ProjectionLedger

    def __post_init__(self) -> None:
        if type(self.raw_domain) is not RawCandidateDomain:
            raise TypeError("observation production requires an exact raw domain")
        if type(self.inventory) is not TargetAuthorityInventory or self.inventory.kind != (
            "observation"
        ):
            raise TypeError("observation production requires an exact observation inventory")
        if type(self.projection_ledger) is not ProjectionLedger:
            raise TypeError("observation production requires an exact projection ledger")


@dataclass(frozen=True, slots=True)
class RegistrationProduction:
    inventory: TargetAuthorityInventory
    declaration_domain_digest: str
    declaration_count: int
    target_artifact_epoch_digest: str

    def __post_init__(self) -> None:
        if type(self.inventory) is not TargetAuthorityInventory or self.inventory.kind != (
            "registration"
        ):
            raise TypeError("registration production requires an exact registration inventory")
        _require_digest(self.declaration_domain_digest, "registration declaration domain")
        _require_digest(self.target_artifact_epoch_digest, "registration target artifact epoch")
        if type(self.declaration_count) is not int or self.declaration_count < 1:
            raise ValueError("registration declaration count must be positive")


@dataclass(frozen=True, slots=True)
class _ProjectedRow:
    candidate: ObservedCandidate
    row: TargetAuthorityRow

    @property
    def projection(self) -> CandidateProjection:
        return CandidateProjection(
            self.candidate.candidate_id,
            self.row.key,
            self.row.row_digest,
        )


def _require_digest(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_text(value: object, label: str, *, maximum_bytes: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\0" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{label} must be bounded canonical Unicode scalar text")
    return value
