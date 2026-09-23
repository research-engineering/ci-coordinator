"""Closed Phase-0 baseline to expected-relation transition."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from ._canonical import domain_digest, encode_mapping
from .limits import MAX_REASON_BYTES, MAX_RELATION_ROWS
from .model import (
    ProducerIdentity,
    TargetAuthorityEpoch,
    TargetAuthorityKey,
    TargetAuthorityRow,
    TargetAuthoritySubject,
    require_canonical_rows,
    require_digest,
    require_text,
)
from .outcomes import RelationFinding, RelationRejected, canonical_findings

type BaselineDispositionKind = Literal["retained", "replaced", "retired"]

PHASE_ZERO_BASELINE_SCHEMA = "ci-coordinator.phase-zero-target-authority-baseline/v1"
AUTHORITY_TRANSITION_DELTA_SCHEMA = "ci-coordinator.target-authority-transition-delta/v1"
EXPECTED_TARGET_RELATION_SCHEMA = "ci-coordinator.expected-target-authority-relation/v1"


@dataclass(frozen=True, slots=True)
class PhaseZeroBaseline:
    subject: TargetAuthoritySubject
    epoch: TargetAuthorityEpoch
    producer: ProducerIdentity
    owner_approval_digest: str
    rows: tuple[TargetAuthorityRow, ...]

    def __post_init__(self) -> None:
        if type(self.subject) is not TargetAuthoritySubject:
            raise TypeError("Phase-0 baseline requires an exact subject")
        if (
            type(self.epoch) is not TargetAuthorityEpoch
            or self.epoch.phase != "native_baseline"
            or self.epoch.has_unknown
        ):
            raise ValueError("Phase-0 baseline requires one complete native epoch")
        if type(self.producer) is not ProducerIdentity:
            raise TypeError("Phase-0 baseline requires an exact producer")
        require_digest(self.owner_approval_digest, "Phase-0 owner approval")
        if self.epoch.owner.digest != self.owner_approval_digest:
            raise ValueError("Phase-0 owner approval must equal the native owner epoch")
        require_canonical_rows(self.rows, allow_empty=False)
        if any(row.has_unknown for row in self.rows):
            raise ValueError("Phase-0 baseline cannot contain unknown row fields")
        encode_mapping(self.to_mapping())

    @property
    def baseline_digest(self) -> str:
        return domain_digest(PHASE_ZERO_BASELINE_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": PHASE_ZERO_BASELINE_SCHEMA,
            "subject": self.subject.to_mapping(),
            "epoch": self.epoch.to_mapping(),
            "producer": self.producer.to_mapping(),
            "ownerApprovalDigest": self.owner_approval_digest,
            "rows": [row.to_mapping() for row in self.rows],
        }


@dataclass(frozen=True, slots=True)
class BaselineDisposition:
    predecessor: TargetAuthorityKey
    kind: BaselineDispositionKind
    successors: tuple[TargetAuthorityRow, ...]
    reason: str

    def __post_init__(self) -> None:
        if type(self.predecessor) is not TargetAuthorityKey:
            raise TypeError("baseline disposition requires an exact predecessor key")
        if self.kind not in {"retained", "replaced", "retired"}:
            raise ValueError("baseline disposition kind is not admitted")
        require_text(self.reason, "baseline disposition reason", maximum_bytes=MAX_REASON_BYTES)
        require_canonical_rows(self.successors, allow_empty=True)
        if self.kind == "retained" and len(self.successors) != 1:
            raise ValueError("retained baseline member requires exactly one successor")
        if self.kind == "replaced" and not self.successors:
            raise ValueError("replaced baseline member requires a successor")
        if self.kind == "retired" and self.successors:
            raise ValueError("retired baseline member cannot have a successor")
        if any(row.has_unknown for row in self.successors):
            raise ValueError("transition successors cannot contain unknown fields")

    @property
    def sort_key(self) -> tuple[bytes, bytes]:
        return self.predecessor.sort_key

    def to_mapping(self) -> dict[str, object]:
        return {
            "predecessor": self.predecessor.to_mapping(),
            "kind": self.kind,
            "successors": [row.to_mapping() for row in self.successors],
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AuthorityIntroduction:
    row: TargetAuthorityRow
    reason: str

    def __post_init__(self) -> None:
        if type(self.row) is not TargetAuthorityRow or self.row.has_unknown:
            raise ValueError("authority introduction requires one complete exact row")
        require_text(self.reason, "authority introduction reason", maximum_bytes=MAX_REASON_BYTES)

    @property
    def sort_key(self) -> tuple[bytes, bytes]:
        return self.row.key.sort_key

    def to_mapping(self) -> dict[str, object]:
        return {"row": self.row.to_mapping(), "reason": self.reason}


@dataclass(frozen=True, slots=True)
class AuthorityTransitionDelta:
    subject: TargetAuthoritySubject
    baseline_digest: str
    target_epoch: TargetAuthorityEpoch
    producer: ProducerIdentity
    owner_approval_digest: str
    dispositions: tuple[BaselineDisposition, ...]
    introductions: tuple[AuthorityIntroduction, ...] = ()

    def __post_init__(self) -> None:
        if type(self.subject) is not TargetAuthoritySubject:
            raise TypeError("authority transition requires an exact subject")
        require_digest(self.baseline_digest, "authority transition baseline")
        if (
            type(self.target_epoch) is not TargetAuthorityEpoch
            or self.target_epoch.phase != "adapted_target"
            or self.target_epoch.has_unknown
        ):
            raise ValueError("authority transition requires one complete adapted-target epoch")
        if type(self.producer) is not ProducerIdentity:
            raise TypeError("authority transition requires an exact producer")
        require_digest(self.owner_approval_digest, "authority transition owner approval")
        if self.target_epoch.owner.digest != self.owner_approval_digest:
            raise ValueError("transition owner approval must equal the adapted owner epoch")
        if type(self.dispositions) is not tuple or any(
            type(item) is not BaselineDisposition for item in self.dispositions
        ):
            raise TypeError("authority transition dispositions must be an exact tuple")
        if len(self.dispositions) > MAX_RELATION_ROWS:
            raise ValueError("authority transition dispositions exceed the relation bound")
        disposition_keys = tuple(item.sort_key for item in self.dispositions)
        if disposition_keys != tuple(sorted(set(disposition_keys))):
            raise ValueError("authority transition dispositions must be canonical and unique")
        if type(self.introductions) is not tuple or any(
            type(item) is not AuthorityIntroduction for item in self.introductions
        ):
            raise TypeError("authority introductions must be an exact tuple")
        if len(self.introductions) > MAX_RELATION_ROWS:
            raise ValueError("authority introductions exceed the relation bound")
        introduction_keys = tuple(item.sort_key for item in self.introductions)
        if introduction_keys != tuple(sorted(set(introduction_keys))):
            raise ValueError("authority introductions must be canonical and unique")
        successor_count = sum(len(item.successors) for item in self.dispositions) + len(
            self.introductions
        )
        if successor_count > MAX_RELATION_ROWS:
            raise ValueError("authority transition successors exceed the relation bound")
        encode_mapping(self.to_mapping())

    @property
    def delta_digest(self) -> str:
        return domain_digest(AUTHORITY_TRANSITION_DELTA_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": AUTHORITY_TRANSITION_DELTA_SCHEMA,
            "subject": self.subject.to_mapping(),
            "baselineDigest": self.baseline_digest,
            "targetEpoch": self.target_epoch.to_mapping(),
            "producer": self.producer.to_mapping(),
            "ownerApprovalDigest": self.owner_approval_digest,
            "dispositions": [item.to_mapping() for item in self.dispositions],
            "introductions": [item.to_mapping() for item in self.introductions],
        }


@dataclass(frozen=True, slots=True)
class ExpectedTargetAuthorityRelation:
    subject: TargetAuthoritySubject
    epoch: TargetAuthorityEpoch
    baseline_digest: str
    delta_digest: str
    rows: tuple[TargetAuthorityRow, ...]

    def __post_init__(self) -> None:
        if type(self.subject) is not TargetAuthoritySubject:
            raise TypeError("expected relation requires an exact subject")
        if (
            type(self.epoch) is not TargetAuthorityEpoch
            or self.epoch.phase != "adapted_target"
            or self.epoch.has_unknown
        ):
            raise ValueError("expected relation requires one complete adapted-target epoch")
        require_digest(self.baseline_digest, "expected relation baseline")
        require_digest(self.delta_digest, "expected relation delta")
        require_canonical_rows(self.rows, allow_empty=False)
        if any(row.has_unknown for row in self.rows):
            raise ValueError("expected relation cannot contain unknown row fields")
        encode_mapping(self.to_mapping())

    @property
    def relation_digest(self) -> str:
        return domain_digest(EXPECTED_TARGET_RELATION_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": EXPECTED_TARGET_RELATION_SCHEMA,
            "subject": self.subject.to_mapping(),
            "epoch": self.epoch.to_mapping(),
            "baselineDigest": self.baseline_digest,
            "deltaDigest": self.delta_digest,
            "rows": [row.to_mapping() for row in self.rows],
        }


type TransitionOutcome = ExpectedTargetAuthorityRelation | RelationRejected


def apply_transition(
    baseline: PhaseZeroBaseline,
    delta: AuthorityTransitionDelta,
) -> TransitionOutcome:
    if type(baseline) is not PhaseZeroBaseline or type(delta) is not AuthorityTransitionDelta:
        raise TypeError("authority transition requires exact baseline and delta values")
    findings: list[RelationFinding] = []
    if delta.subject != baseline.subject:
        findings.append(RelationFinding("subject_mismatch"))
    if delta.baseline_digest != baseline.baseline_digest:
        findings.append(
            RelationFinding(
                "baseline_digest_mismatch",
                expected_digest=baseline.baseline_digest,
                observed_digest=delta.baseline_digest,
            )
        )

    baseline_by_key = {row.key: row for row in baseline.rows}
    dispositions_by_key = {
        disposition.predecessor: disposition for disposition in delta.dispositions
    }
    introduction_keys = {introduction.row.key for introduction in delta.introductions}
    findings.extend(
        RelationFinding("missing_baseline_disposition", _coordinate(key))
        for key in sorted(
            baseline_by_key.keys() - dispositions_by_key.keys(),
            key=lambda item: item.sort_key,
        )
    )
    findings.extend(
        RelationFinding("extra_baseline_disposition", _coordinate(key))
        for key in sorted(
            dispositions_by_key.keys() - baseline_by_key.keys(),
            key=lambda item: item.sort_key,
        )
    )
    findings.extend(
        RelationFinding("invalid_introduction", _coordinate(key))
        for key in sorted(
            baseline_by_key.keys() & introduction_keys,
            key=lambda item: item.sort_key,
        )
    )

    successors: list[TargetAuthorityRow] = []
    for key in sorted(
        baseline_by_key.keys() & dispositions_by_key.keys(),
        key=lambda item: item.sort_key,
    ):
        predecessor = baseline_by_key[key]
        disposition = dispositions_by_key[key]
        if disposition.kind == "retained":
            successor = disposition.successors[0]
            if successor.key != predecessor.key or successor.row_digest != predecessor.row_digest:
                findings.append(
                    RelationFinding(
                        "invalid_retention",
                        _coordinate(key),
                        predecessor.row_digest,
                        successor.row_digest,
                    )
                )
            successors.extend(disposition.successors)
        elif disposition.kind == "replaced":
            if any(row.row_digest == predecessor.row_digest for row in disposition.successors):
                findings.append(RelationFinding("invalid_replacement", _coordinate(key)))
            successors.extend(disposition.successors)

    successors.extend(introduction.row for introduction in delta.introductions)
    successor_keys = [row.key for row in successors]
    duplicate_keys = sorted(
        (key for key, count in Counter(successor_keys).items() if count > 1),
        key=lambda item: item.sort_key,
    )
    findings.extend(
        RelationFinding("successor_key_collision", _coordinate(key)) for key in duplicate_keys
    )
    if not successors:
        findings.append(RelationFinding("relation_became_empty"))
    if findings:
        return RelationRejected(canonical_findings(findings))

    ordered = tuple(sorted(successors, key=lambda row: row.key.sort_key))
    return ExpectedTargetAuthorityRelation(
        subject=baseline.subject,
        epoch=delta.target_epoch,
        baseline_digest=baseline.baseline_digest,
        delta_digest=delta.delta_digest,
        rows=ordered,
    )


def _coordinate(key: TargetAuthorityKey) -> str:
    return f"{key.family}:{key.member_id}"
