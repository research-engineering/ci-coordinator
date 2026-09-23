"""Independent producer inventories and total raw-domain projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.kernel.ordering import utf16_sort_key

from ._canonical import domain_digest, encode_mapping
from .limits import MAX_RAW_CANDIDATES, MAX_SOURCE_LOCATOR_BYTES
from .model import (
    ProducerIdentity,
    TargetAuthorityEpoch,
    TargetAuthorityKey,
    TargetAuthorityRow,
    TargetAuthorityRowFamily,
    TargetAuthoritySubject,
    require_canonical_rows,
    require_digest,
    require_text,
)

type AuthorityInventoryKind = Literal["observation", "registration"]
type RawCandidateState = Literal["present", "unknown"]
type RawCandidateKind = TargetAuthorityRowFamily | Literal["workflow_blob"]

TARGET_AUTHORITY_INVENTORY_SCHEMA = "ci-coordinator.target-authority-inventory/v1"
RAW_CANDIDATE_DOMAIN_SCHEMA = "ci-coordinator.target-authority-raw-domain/v1"
PROJECTION_LEDGER_SCHEMA = "ci-coordinator.target-authority-projection-ledger/v1"


@dataclass(frozen=True, slots=True)
class TargetAuthorityInventory:
    kind: AuthorityInventoryKind
    producer: ProducerIdentity
    subject: TargetAuthoritySubject
    epoch: TargetAuthorityEpoch
    workflow_manifest_digest: str
    source_domain_digest: str
    source_item_count: int
    complete: bool
    rows: tuple[TargetAuthorityRow, ...]

    def __post_init__(self) -> None:
        if self.kind not in {"observation", "registration"}:
            raise ValueError("target-authority inventory kind is not admitted")
        if type(self.producer) is not ProducerIdentity:
            raise TypeError("target-authority inventory requires an exact producer")
        if type(self.subject) is not TargetAuthoritySubject:
            raise TypeError("target-authority inventory requires an exact subject")
        if type(self.epoch) is not TargetAuthorityEpoch or self.epoch.phase != "adapted_target":
            raise ValueError("target-authority inventory requires an adapted-target epoch")
        require_digest(self.workflow_manifest_digest, "workflow manifest")
        require_digest(self.source_domain_digest, "inventory source domain")
        if (
            self.epoch.source_manifest.state == "present"
            and self.epoch.source_manifest.digest != self.workflow_manifest_digest
        ):
            raise ValueError("inventory workflow manifest contradicts its epoch")
        if (
            type(self.source_item_count) is not int
            or not 1 <= self.source_item_count <= MAX_RAW_CANDIDATES
        ):
            raise ValueError("inventory source-item count is outside its bound")
        if type(self.complete) is not bool:
            raise TypeError("inventory completeness must be an exact boolean")
        require_canonical_rows(self.rows, allow_empty=True)
        encode_mapping(self.to_mapping())

    @property
    def inventory_digest(self) -> str:
        return domain_digest(TARGET_AUTHORITY_INVENTORY_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": TARGET_AUTHORITY_INVENTORY_SCHEMA,
            "kind": self.kind,
            "producer": self.producer.to_mapping(),
            "subject": self.subject.to_mapping(),
            "epoch": self.epoch.to_mapping(),
            "workflowManifestDigest": self.workflow_manifest_digest,
            "sourceDomainDigest": self.source_domain_digest,
            "sourceItemCount": self.source_item_count,
            "complete": self.complete,
            "rows": [row.to_mapping() for row in self.rows],
        }


@dataclass(frozen=True, slots=True)
class RawCandidate:
    candidate_id: str
    kind: RawCandidateKind
    source_locator: str
    state: RawCandidateState
    evidence_digest: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        require_text(self.candidate_id, "raw candidate id", maximum_bytes=2_048)
        if self.kind not in {
            "consumer_contract_scenario",
            "job",
            "provider_gate",
            "provider_repository",
            "target_policy",
            "target_registry_adapter",
            "target_registry_metadata",
            "target_registry_profile",
            "target_registry_workflow",
            "validation_obligation",
            "validation_profile",
            "validation_witness",
            "workflow",
            "workflow_blob",
        }:
            raise ValueError("raw candidate kind is not admitted")
        require_text(
            self.source_locator,
            "raw candidate source locator",
            maximum_bytes=MAX_SOURCE_LOCATOR_BYTES,
        )
        if self.state == "present":
            require_digest(self.evidence_digest, "raw candidate evidence")
            if self.reason is not None:
                raise ValueError("present raw candidate cannot carry an unknown reason")
            return
        if self.state == "unknown":
            if self.evidence_digest is not None:
                raise ValueError("unknown raw candidate cannot carry an evidence digest")
            require_text(self.reason, "raw candidate unknown reason", maximum_bytes=512)
            return
        raise ValueError("raw candidate state is not admitted")

    @property
    def sort_key(self) -> bytes:
        return utf16_sort_key(self.candidate_id)

    @property
    def projected_family(self) -> TargetAuthorityRowFamily:
        if self.kind == "workflow_blob":
            return "workflow"
        return self.kind

    def to_mapping(self) -> dict[str, object]:
        return {
            "candidateId": self.candidate_id,
            "kind": self.kind,
            "sourceLocator": self.source_locator,
            "state": self.state,
            "evidenceDigest": self.evidence_digest,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RawCandidateDomain:
    producer: ProducerIdentity
    subject: TargetAuthoritySubject
    epoch: TargetAuthorityEpoch
    complete: bool
    candidates: tuple[RawCandidate, ...]

    def __post_init__(self) -> None:
        if type(self.producer) is not ProducerIdentity:
            raise TypeError("raw candidate domain requires an exact producer")
        if type(self.subject) is not TargetAuthoritySubject:
            raise TypeError("raw candidate domain requires an exact subject")
        if type(self.epoch) is not TargetAuthorityEpoch or self.epoch.phase != "adapted_target":
            raise ValueError("raw candidate domain requires an adapted-target epoch")
        if type(self.complete) is not bool:
            raise TypeError("raw candidate completeness must be an exact boolean")
        if (
            type(self.candidates) is not tuple
            or not 1 <= len(self.candidates) <= MAX_RAW_CANDIDATES
            or any(type(candidate) is not RawCandidate for candidate in self.candidates)
        ):
            raise TypeError("raw candidates must be a bounded non-empty exact tuple")
        ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if ids != tuple(sorted(set(ids), key=utf16_sort_key)):
            raise ValueError("raw candidate ids must be canonical and unique")
        encode_mapping(self.to_mapping())

    @property
    def domain_digest(self) -> str:
        return domain_digest(RAW_CANDIDATE_DOMAIN_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": RAW_CANDIDATE_DOMAIN_SCHEMA,
            "producer": self.producer.to_mapping(),
            "subject": self.subject.to_mapping(),
            "epoch": self.epoch.to_mapping(),
            "complete": self.complete,
            "candidates": [candidate.to_mapping() for candidate in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class CandidateProjection:
    candidate_id: str
    row_key: TargetAuthorityKey
    row_digest: str

    def __post_init__(self) -> None:
        require_text(self.candidate_id, "projected candidate id", maximum_bytes=2_048)
        if type(self.row_key) is not TargetAuthorityKey:
            raise TypeError("candidate projection requires an exact row key")
        require_digest(self.row_digest, "candidate projection row")

    @property
    def sort_key(self) -> tuple[bytes, bytes, bytes, bytes]:
        return (
            utf16_sort_key(self.candidate_id),
            *self.row_key.sort_key,
            self.row_digest.encode("ascii"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "candidateId": self.candidate_id,
            "rowKey": self.row_key.to_mapping(),
            "rowDigest": self.row_digest,
        }


@dataclass(frozen=True, slots=True)
class ProjectionLedger:
    producer: ProducerIdentity
    subject: TargetAuthoritySubject
    epoch: TargetAuthorityEpoch
    complete: bool
    projections: tuple[CandidateProjection, ...]

    def __post_init__(self) -> None:
        if type(self.producer) is not ProducerIdentity:
            raise TypeError("projection ledger requires an exact producer")
        if type(self.subject) is not TargetAuthoritySubject:
            raise TypeError("projection ledger requires an exact subject")
        if type(self.epoch) is not TargetAuthorityEpoch or self.epoch.phase != "adapted_target":
            raise ValueError("projection ledger requires an adapted-target epoch")
        if type(self.complete) is not bool:
            raise TypeError("projection completeness must be an exact boolean")
        if (
            type(self.projections) is not tuple
            or len(self.projections) > MAX_RAW_CANDIDATES
            or any(type(projection) is not CandidateProjection for projection in self.projections)
        ):
            raise TypeError("candidate projections must be a bounded exact tuple")
        keys = tuple(projection.sort_key for projection in self.projections)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("candidate projections must be canonical and unique")
        encode_mapping(self.to_mapping())

    @property
    def ledger_digest(self) -> str:
        return domain_digest(PROJECTION_LEDGER_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": PROJECTION_LEDGER_SCHEMA,
            "producer": self.producer.to_mapping(),
            "subject": self.subject.to_mapping(),
            "epoch": self.epoch.to_mapping(),
            "complete": self.complete,
            "projections": [projection.to_mapping() for projection in self.projections],
        }
