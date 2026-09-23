"""Typed non-authoritative outcomes for transition and relation closure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, cast, get_args

from ci_coordinator.kernel.ordering import utf16_sort_key

from ._canonical import domain_digest, encode_mapping
from .limits import (
    MAX_MEMBER_ID_BYTES,
    MAX_RAW_CANDIDATES,
    MAX_RELATION_FINDINGS,
    MAX_RELATION_ROWS,
)
from .model import (
    ProducerIdentity,
    TargetAuthorityEpoch,
    TargetAuthoritySubject,
    require_digest,
    require_text,
)

type RelationFindingCode = Literal[
    "baseline_digest_mismatch",
    "candidate_multiply_classified",
    "candidate_unclassified",
    "candidate_unknown",
    "epoch_mismatch",
    "extra_baseline_disposition",
    "extra_observation_row",
    "extra_registration_row",
    "invalid_introduction",
    "invalid_replacement",
    "invalid_retention",
    "missing_baseline_disposition",
    "missing_observation_row",
    "missing_registration_row",
    "observation_incomplete",
    "observation_row_mismatch",
    "observation_source_count_mismatch",
    "observation_source_domain_mismatch",
    "producer_identity_collision",
    "projection_incomplete",
    "projection_kind_mismatch",
    "projection_row_mismatch",
    "projection_row_missing",
    "raw_domain_incomplete",
    "registration_incomplete",
    "registration_row_mismatch",
    "registration_source_count_mismatch",
    "registration_source_domain_mismatch",
    "relation_became_empty",
    "successor_key_collision",
    "subject_mismatch",
    "unprojected_observation_row",
    "unknown_observation_row",
    "unknown_registration_row",
    "workflow_manifest_mismatch",
]

RELATION_CLOSURE_SCHEMA = "ci-coordinator.unactivated-target-authority-closure/v1"
_RELATION_FINDING_CODE_VALUES = cast(
    tuple[str, ...],
    get_args(RelationFindingCode.__value__),
)
if not _RELATION_FINDING_CODE_VALUES or any(
    type(value) is not str for value in _RELATION_FINDING_CODE_VALUES
):
    raise RuntimeError("target-authority finding code contract could not be materialized")
RELATION_FINDING_CODES: Final = frozenset(_RELATION_FINDING_CODE_VALUES)


@dataclass(frozen=True, slots=True)
class RelationFinding:
    code: RelationFindingCode
    coordinate: str | None = None
    expected_digest: str | None = None
    observed_digest: str | None = None

    def __post_init__(self) -> None:
        if self.code not in RELATION_FINDING_CODES:
            raise ValueError("target-authority finding code is not admitted")
        if self.coordinate is not None:
            require_text(
                self.coordinate,
                "target-authority finding coordinate",
                maximum_bytes=MAX_MEMBER_ID_BYTES + 256,
            )
        for name, value in (
            ("expected finding digest", self.expected_digest),
            ("observed finding digest", self.observed_digest),
        ):
            if value is not None:
                require_digest(value, name)

    @property
    def sort_key(self) -> tuple[bytes, bytes, bytes, bytes]:
        return (
            utf16_sort_key(self.code),
            _nullable_text_sort_key(self.coordinate),
            _nullable_text_sort_key(self.expected_digest),
            _nullable_text_sort_key(self.observed_digest),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "code": self.code,
            "coordinate": self.coordinate,
            "expectedDigest": self.expected_digest,
            "observedDigest": self.observed_digest,
        }


@dataclass(frozen=True, slots=True)
class RelationRejected:
    findings: tuple[RelationFinding, ...]

    def __post_init__(self) -> None:
        if (
            type(self.findings) is not tuple
            or not self.findings
            or len(self.findings) > MAX_RELATION_FINDINGS
            or any(type(finding) is not RelationFinding for finding in self.findings)
        ):
            raise TypeError("relation rejection requires non-empty exact findings")
        keys = tuple(finding.sort_key for finding in self.findings)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("relation rejection findings must be canonical and unique")


@dataclass(frozen=True, slots=True)
class UnactivatedRelationClosure:
    subject: TargetAuthoritySubject
    epoch: TargetAuthorityEpoch
    baseline_digest: str
    delta_digest: str
    expected_relation_digest: str
    registration_inventory_digest: str
    observation_inventory_digest: str
    raw_candidate_domain_digest: str
    projection_ledger_digest: str
    registration_producer: ProducerIdentity
    observation_producer: ProducerIdentity
    row_count: int
    candidate_count: int

    def __post_init__(self) -> None:
        if type(self.subject) is not TargetAuthoritySubject:
            raise TypeError("relation closure requires an exact subject")
        if (
            type(self.epoch) is not TargetAuthorityEpoch
            or self.epoch.phase != "adapted_target"
            or self.epoch.has_unknown
        ):
            raise ValueError("relation closure requires one complete adapted-target epoch")
        for name, value in (
            ("baseline", self.baseline_digest),
            ("transition delta", self.delta_digest),
            ("expected relation", self.expected_relation_digest),
            ("registration inventory", self.registration_inventory_digest),
            ("observation inventory", self.observation_inventory_digest),
            ("raw candidate domain", self.raw_candidate_domain_digest),
            ("projection ledger", self.projection_ledger_digest),
        ):
            require_digest(value, name)
        if (
            type(self.registration_producer) is not ProducerIdentity
            or type(self.observation_producer) is not ProducerIdentity
        ):
            raise TypeError("relation closure requires exact producer identities")
        if self.registration_producer == self.observation_producer:
            raise ValueError("relation closure producers must be distinct")
        if type(self.row_count) is not int or not 1 <= self.row_count <= MAX_RELATION_ROWS:
            raise ValueError("relation closure row count is outside its bound")
        if (
            type(self.candidate_count) is not int
            or not 1 <= self.candidate_count <= MAX_RAW_CANDIDATES
        ):
            raise ValueError("relation closure candidate count is outside its bound")
        encode_mapping(self.to_mapping())

    @property
    def closure_digest(self) -> str:
        return domain_digest(RELATION_CLOSURE_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": RELATION_CLOSURE_SCHEMA,
            "authorityState": "unactivated",
            "subject": self.subject.to_mapping(),
            "epoch": self.epoch.to_mapping(),
            "baselineDigest": self.baseline_digest,
            "deltaDigest": self.delta_digest,
            "expectedRelationDigest": self.expected_relation_digest,
            "registrationInventoryDigest": self.registration_inventory_digest,
            "observationInventoryDigest": self.observation_inventory_digest,
            "rawCandidateDomainDigest": self.raw_candidate_domain_digest,
            "projectionLedgerDigest": self.projection_ledger_digest,
            "registrationProducer": self.registration_producer.to_mapping(),
            "observationProducer": self.observation_producer.to_mapping(),
            "rowCount": self.row_count,
            "candidateCount": self.candidate_count,
        }


type RelationComparisonOutcome = UnactivatedRelationClosure | RelationRejected


def canonical_findings(findings: list[RelationFinding]) -> tuple[RelationFinding, ...]:
    return tuple(sorted(set(findings), key=lambda finding: finding.sort_key))


def _nullable_text_sort_key(value: str | None) -> bytes:
    return b"\0" if value is None else b"\1" + utf16_sort_key(value)
