from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from itertools import islice
from typing import Final, Literal

from ci_coordinator.audit_replay.chain import verify_audit_event_integrity
from ci_coordinator.audit_replay.event import (
    AUDIT_EVENT_SCHEMA_VERSION,
    AuditEventError,
    AuditEventRecord,
    AuditEventRecordLike,
    audit_event_from_mapping,
)
from ci_coordinator.audit_replay.paged_replay import AuditReplayScan
from ci_coordinator.audit_replay.replay import ValidAuditReplayReport
from ci_coordinator.kernel import hash_object, is_safe_json_integer

AUDIT_CHECKPOINT_STATEMENT_SCHEMA: Final = "ci-audit-checkpoint-statement/v1"
AUDIT_SUCCESSOR_CHAIN_SCHEMA: Final = "ci-audit-successor-chain/v1"
MAX_AUDIT_SUCCESSOR_EVENTS: Final = 4_096

_ARTIFACT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_EVENT_ID = re.compile(r"audit_[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class AuditChainTipStatement:
    database_identity_digest: str
    artifact_digest: str
    retained_sequence: int
    last_event_id: str
    last_event_hash: str
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_digest(self.database_identity_digest, "audit checkpoint database identity")
        if _ARTIFACT_DIGEST.fullmatch(self.artifact_digest) is None:
            raise ValueError("audit checkpoint artifact digest is invalid")
        if (
            type(self.retained_sequence) is not int
            or not is_safe_json_integer(self.retained_sequence)
            or self.retained_sequence < 1
        ):
            raise ValueError("audit checkpoint retained sequence is invalid")
        if _EVENT_ID.fullmatch(self.last_event_id) is None:
            raise ValueError("audit checkpoint event id is invalid")
        _require_digest(self.last_event_hash, "audit checkpoint event")
        if self.last_event_id != f"audit_{self.last_event_hash[:32]}":
            raise ValueError("audit checkpoint event id does not match event hash")
        _require_utc_millisecond(self.observed_at, "audit checkpoint observation")

    @property
    def statement_digest(self) -> str:
        return hash_object(self.identity_mapping())

    def identity_mapping(self) -> dict[str, object]:
        return {
            "artifactDigest": self.artifact_digest,
            "databaseIdentityDigest": self.database_identity_digest,
            "lastEventHash": self.last_event_hash,
            "lastEventId": self.last_event_id,
            "ledgerSchema": AUDIT_EVENT_SCHEMA_VERSION,
            "observedAt": _timestamp(self.observed_at),
            "retainedSequence": self.retained_sequence,
            "schemaVersion": AUDIT_CHECKPOINT_STATEMENT_SCHEMA,
        }

    def to_mapping(self) -> dict[str, object]:
        return {**self.identity_mapping(), "statementDigest": self.statement_digest}


@dataclass(frozen=True, slots=True)
class ValidAuditSuccessor:
    event_count: int
    last_sequence: int
    last_event_hash: str
    successor_chain_digest: str
    valid: Literal[True] = True


@dataclass(frozen=True, slots=True)
class InvalidAuditSuccessor:
    reason: str
    audit_event_id: str | None
    valid: Literal[False] = False


type AuditSuccessorVerification = ValidAuditSuccessor | InvalidAuditSuccessor


def build_audit_chain_tip_statement(
    scan: AuditReplayScan,
    *,
    database_identity_digest: str,
    artifact_digest: str,
    observed_at: datetime,
) -> AuditChainTipStatement:
    if type(scan) is not AuditReplayScan or not isinstance(scan.report, ValidAuditReplayReport):
        raise ValueError("audit checkpoint requires a complete valid replay scan")
    event = scan.last_event
    if (
        event is None
        or event.sequence != scan.snapshot.last_sequence
        or event.event_hash != scan.snapshot.last_event_hash
    ):
        raise ValueError("audit checkpoint requires a non-empty exact replay head")
    return AuditChainTipStatement(
        database_identity_digest=database_identity_digest,
        artifact_digest=artifact_digest,
        retained_sequence=event.sequence,
        last_event_id=event.audit_event_id,
        last_event_hash=event.event_hash,
        observed_at=observed_at,
    )


def verify_audit_successor_chain(
    checkpoint: AuditChainTipStatement,
    records: Sequence[AuditEventRecordLike],
) -> AuditSuccessorVerification:
    if type(checkpoint) is not AuditChainTipStatement:
        return InvalidAuditSuccessor("audit checkpoint is not exact", None)
    bounded_records = tuple(islice(records, MAX_AUDIT_SUCCESSOR_EVENTS + 1))
    if not 1 <= len(bounded_records) <= MAX_AUDIT_SUCCESSOR_EVENTS:
        return InvalidAuditSuccessor("audit successor count is outside its bound", None)

    expected_sequence = checkpoint.retained_sequence + 1
    expected_previous_hash = checkpoint.last_event_hash
    idempotency_keys: set[str] = set()
    digest = sha256()
    digest.update(AUDIT_SUCCESSOR_CHAIN_SCHEMA.encode("ascii") + b"\x00")
    digest.update(checkpoint.statement_digest.encode("ascii") + b"\x00")
    previous: AuditEventRecord | None = None

    for raw_record in bounded_records:
        try:
            record = audit_event_from_mapping(raw_record)
        except AuditEventError as error:
            return InvalidAuditSuccessor(str(error), error.audit_event_id)
        if record.sequence != expected_sequence:
            return InvalidAuditSuccessor(
                f"expected successor sequence {expected_sequence}, got {record.sequence}",
                record.audit_event_id,
            )
        if record.previous_event_hash != expected_previous_hash:
            return InvalidAuditSuccessor(
                "audit successor previous hash does not match",
                record.audit_event_id,
            )
        invalid = verify_audit_event_integrity(record)
        if invalid is not None:
            return InvalidAuditSuccessor(invalid.reason, invalid.audit_event_id)
        if record.idempotency_key in idempotency_keys:
            return InvalidAuditSuccessor(
                "duplicate idempotency key inside audit successor",
                record.audit_event_id,
            )
        idempotency_keys.add(record.idempotency_key)
        digest.update(str(record.sequence).encode("ascii") + b"\x00")
        digest.update(record.event_hash.encode("ascii") + b"\x00")
        expected_sequence += 1
        expected_previous_hash = record.event_hash
        previous = record

    if previous is None:
        return InvalidAuditSuccessor("audit successor is empty", None)
    return ValidAuditSuccessor(
        event_count=len(bounded_records),
        last_sequence=previous.sequence,
        last_event_hash=previous.event_hash,
        successor_chain_digest=digest.hexdigest(),
    )


def _require_digest(value: object, name: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} digest is invalid")


def _require_utc_millisecond(value: object, name: str) -> None:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.astimezone(UTC).utcoffset() != value.utcoffset()
        or value.microsecond % 1_000 != 0
    ):
        raise ValueError(f"{name} must be canonical UTC milliseconds")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
