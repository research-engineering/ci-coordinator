from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NoReturn

from ci_coordinator.audit_replay.checkpoint import (
    InvalidAuditSuccessor,
    ValidAuditSuccessor,
    verify_audit_successor_chain,
)
from ci_coordinator.audit_replay.checkpoint_admission import AuthenticatedAuditCheckpoint
from ci_coordinator.audit_replay.event import AuditEventRecordLike
from ci_coordinator.capacity_qualification.admission import CapacityQualified
from ci_coordinator.kernel import Clock, hash_object, is_safe_json_integer

AUDIT_STORAGE_INVENTORY_SCHEMA = "ci-audit-storage-inventory/v1"
type AuditStorageCopyKind = Literal["backup", "live", "quarantine", "wal"]

_COPY_ID = re.compile(r"[a-z][a-z0-9._-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class AuditStorageCopy:
    copy_id: str
    kind: AuditStorageCopyKind
    content_digest: str
    size_bytes: int
    checkpoint_envelope_digest: str
    successor_chain_digest: str
    key_binding_inventory_digest: str

    def __post_init__(self) -> None:
        if type(self.copy_id) is not str or _COPY_ID.fullmatch(self.copy_id) is None:
            raise ValueError("audit storage copy id is invalid")
        if self.kind not in {"backup", "live", "quarantine", "wal"}:
            raise ValueError("audit storage copy kind is invalid")
        for name, value in (
            ("content", self.content_digest),
            ("checkpoint", self.checkpoint_envelope_digest),
            ("successor", self.successor_chain_digest),
            ("key binding inventory", self.key_binding_inventory_digest),
        ):
            _require_digest(value, name)
        _bounded_nonnegative(self.size_bytes, "audit storage copy bytes")

    def to_mapping(self) -> dict[str, object]:
        return {
            "checkpointEnvelopeDigest": self.checkpoint_envelope_digest,
            "contentDigest": self.content_digest,
            "copyId": self.copy_id,
            "keyBindingInventoryDigest": self.key_binding_inventory_digest,
            "kind": self.kind,
            "sizeBytes": self.size_bytes,
            "successorChainDigest": self.successor_chain_digest,
        }


@dataclass(frozen=True, slots=True)
class AuditRestoreEvidence:
    content_digest: str
    source_copy_id: str
    checkpoint_envelope_digest: str
    successor_chain_digest: str
    duration_microseconds: int

    def __post_init__(self) -> None:
        _require_digest(self.content_digest, "audit restore evidence")
        if type(self.source_copy_id) is not str or _COPY_ID.fullmatch(self.source_copy_id) is None:
            raise ValueError("audit restore source copy id is invalid")
        _require_digest(self.checkpoint_envelope_digest, "audit restore checkpoint")
        _require_digest(self.successor_chain_digest, "audit restore successor")
        _bounded_nonnegative(self.duration_microseconds, "audit restore duration")

    def to_mapping(self) -> dict[str, object]:
        return {
            "checkpointEnvelopeDigest": self.checkpoint_envelope_digest,
            "contentDigest": self.content_digest,
            "durationMicroseconds": self.duration_microseconds,
            "sourceCopyId": self.source_copy_id,
            "successorChainDigest": self.successor_chain_digest,
        }


@dataclass(frozen=True, slots=True)
class AuditStorageInventory:
    database_identity_digest: str
    checkpoint_envelope_digest: str
    successor_chain_digest: str
    copies: tuple[AuditStorageCopy, ...]
    key_binding_inventory_digest: str
    key_binding_count: int
    live_row_count: int
    backup_duration_microseconds: int
    replay_duration_microseconds: int
    replay_temporary_storage_bytes: int
    restore_evidence: AuditRestoreEvidence

    def __post_init__(self) -> None:
        _require_digest(self.database_identity_digest, "audit storage database identity")
        _require_digest(self.checkpoint_envelope_digest, "audit storage checkpoint")
        _require_digest(self.successor_chain_digest, "audit storage successor")
        _require_digest(self.key_binding_inventory_digest, "audit storage key inventory")
        if type(self.copies) is not tuple or not 4 <= len(self.copies) <= 4_096:
            raise ValueError("audit storage copy count is outside its bound")
        if any(type(copy) is not AuditStorageCopy for copy in self.copies):
            raise TypeError("audit storage copies must be exact")
        coordinates = tuple((copy.kind, copy.copy_id) for copy in self.copies)
        if tuple(sorted(set(coordinates))) != coordinates:
            raise ValueError("audit storage copies must be unique and sorted")
        kind_counts = Counter(copy.kind for copy in self.copies)
        if any(kind_counts[kind] < 1 for kind in ("backup", "live", "quarantine", "wal")):
            raise ValueError("audit storage inventory must cover every copy kind")
        if any(
            copy.checkpoint_envelope_digest != self.checkpoint_envelope_digest
            or copy.successor_chain_digest != self.successor_chain_digest
            or copy.key_binding_inventory_digest != self.key_binding_inventory_digest
            for copy in self.copies
        ):
            raise ValueError("audit storage copy binding does not match its inventory")
        if type(self.restore_evidence) is not AuditRestoreEvidence:
            raise TypeError("audit restore evidence must be exact")
        backups = {copy.copy_id: copy for copy in self.copies if copy.kind == "backup"}
        source_backup = backups.get(self.restore_evidence.source_copy_id)
        if (
            source_backup is None
            or self.restore_evidence.content_digest != source_backup.content_digest
            or self.restore_evidence.checkpoint_envelope_digest != self.checkpoint_envelope_digest
            or self.restore_evidence.successor_chain_digest != self.successor_chain_digest
        ):
            raise ValueError("audit restore evidence does not bind a retained backup")
        for name, value in (
            ("key binding count", self.key_binding_count),
            ("live row count", self.live_row_count),
            ("backup duration", self.backup_duration_microseconds),
            ("replay duration", self.replay_duration_microseconds),
            ("replay temporary storage", self.replay_temporary_storage_bytes),
        ):
            _bounded_nonnegative(value, f"audit storage {name}")

    @property
    def inventory_digest(self) -> str:
        return hash_object(self.to_mapping())

    def bytes_for(self, kind: AuditStorageCopyKind) -> int:
        return sum(copy.size_bytes for copy in self.copies if copy.kind == kind)

    def to_mapping(self) -> dict[str, object]:
        return {
            "backupDurationMicroseconds": self.backup_duration_microseconds,
            "checkpointEnvelopeDigest": self.checkpoint_envelope_digest,
            "copies": [copy.to_mapping() for copy in self.copies],
            "databaseIdentityDigest": self.database_identity_digest,
            "keyBindingCount": self.key_binding_count,
            "keyBindingInventoryDigest": self.key_binding_inventory_digest,
            "liveRowCount": self.live_row_count,
            "replayDurationMicroseconds": self.replay_duration_microseconds,
            "replayTemporaryStorageBytes": self.replay_temporary_storage_bytes,
            "restoreEvidence": self.restore_evidence.to_mapping(),
            "schemaVersion": AUDIT_STORAGE_INVENTORY_SCHEMA,
            "successorChainDigest": self.successor_chain_digest,
        }


@dataclass(frozen=True, slots=True)
class AuditRetentionPrerequisiteRejected:
    code: Literal[
        "audit_retention_capacity_expired",
        "audit_retention_capacity_insufficient",
        "audit_retention_checkpoint_expired",
        "audit_retention_identity_mismatch",
        "audit_retention_inventory_mismatch",
        "audit_retention_successor_invalid",
    ]
    verified: Literal[False] = False


_VERIFIED_TOKEN = object()


class VerifiedAuditRetentionPrerequisite:
    __capacity_receipt_digest: str
    __checkpoint_envelope_digest: str
    __inventory_digest: str
    __successor_chain_digest: str

    __slots__ = (
        "__capacity_receipt_digest",
        "__checkpoint_envelope_digest",
        "__inventory_digest",
        "__successor_chain_digest",
    )

    def __init__(
        self,
        token: object,
        *,
        capacity_receipt_digest: str,
        checkpoint_envelope_digest: str,
        inventory_digest: str,
        successor_chain_digest: str,
    ) -> None:
        if token is not _VERIFIED_TOKEN:
            raise TypeError("VerifiedAuditRetentionPrerequisite cannot be constructed directly")
        object.__setattr__(
            self,
            "_VerifiedAuditRetentionPrerequisite__capacity_receipt_digest",
            capacity_receipt_digest,
        )
        object.__setattr__(
            self,
            "_VerifiedAuditRetentionPrerequisite__checkpoint_envelope_digest",
            checkpoint_envelope_digest,
        )
        object.__setattr__(
            self,
            "_VerifiedAuditRetentionPrerequisite__inventory_digest",
            inventory_digest,
        )
        object.__setattr__(
            self,
            "_VerifiedAuditRetentionPrerequisite__successor_chain_digest",
            successor_chain_digest,
        )

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("VerifiedAuditRetentionPrerequisite is immutable")

    def __reduce__(self) -> NoReturn:
        raise TypeError("VerifiedAuditRetentionPrerequisite cannot be serialized")

    @property
    def verified(self) -> Literal[True]:
        return True

    @property
    def capacity_receipt_digest(self) -> str:
        return self.__capacity_receipt_digest

    @property
    def checkpoint_envelope_digest(self) -> str:
        return self.__checkpoint_envelope_digest

    @property
    def inventory_digest(self) -> str:
        return self.__inventory_digest

    @property
    def successor_chain_digest(self) -> str:
        return self.__successor_chain_digest


type AuditRetentionPrerequisiteResult = (
    VerifiedAuditRetentionPrerequisite | AuditRetentionPrerequisiteRejected
)


def assess_audit_retention_prerequisite(
    checkpoint: AuthenticatedAuditCheckpoint,
    successor_records: Sequence[AuditEventRecordLike],
    inventory: AuditStorageInventory,
    capacity: CapacityQualified,
    *,
    clock: Clock,
) -> AuditRetentionPrerequisiteResult:
    now = clock.now()
    if not _aware_datetime(now):
        return AuditRetentionPrerequisiteRejected("audit_retention_identity_mismatch")
    if type(checkpoint) is not AuthenticatedAuditCheckpoint or not checkpoint.is_valid_at(now):
        return AuditRetentionPrerequisiteRejected("audit_retention_checkpoint_expired")
    if type(capacity) is not CapacityQualified or not capacity.is_valid_at(now):
        return AuditRetentionPrerequisiteRejected("audit_retention_capacity_expired")
    if type(inventory) is not AuditStorageInventory:
        return AuditRetentionPrerequisiteRejected("audit_retention_inventory_mismatch")

    successor = verify_audit_successor_chain(checkpoint.statement, successor_records)
    if isinstance(successor, InvalidAuditSuccessor):
        return AuditRetentionPrerequisiteRejected("audit_retention_successor_invalid")
    if not isinstance(successor, ValidAuditSuccessor):
        return AuditRetentionPrerequisiteRejected("audit_retention_successor_invalid")
    identity = capacity.identity
    if (
        identity.artifact_digest != checkpoint.statement.artifact_digest
        or identity.database_identity_digest != checkpoint.statement.database_identity_digest
        or inventory.database_identity_digest != checkpoint.statement.database_identity_digest
    ):
        return AuditRetentionPrerequisiteRejected("audit_retention_identity_mismatch")
    if (
        inventory.checkpoint_envelope_digest != checkpoint.envelope_digest
        or inventory.successor_chain_digest != successor.successor_chain_digest
        or identity.audit_storage_inventory_digest != inventory.inventory_digest
    ):
        return AuditRetentionPrerequisiteRejected("audit_retention_inventory_mismatch")
    if not _capacity_covers_inventory(capacity, inventory):
        return AuditRetentionPrerequisiteRejected("audit_retention_capacity_insufficient")
    return VerifiedAuditRetentionPrerequisite(
        _VERIFIED_TOKEN,
        capacity_receipt_digest=capacity.receipt_digest,
        checkpoint_envelope_digest=checkpoint.envelope_digest,
        inventory_digest=inventory.inventory_digest,
        successor_chain_digest=successor.successor_chain_digest,
    )


def _capacity_covers_inventory(
    capacity: CapacityQualified,
    inventory: AuditStorageInventory,
) -> bool:
    values = {
        "audit-backup-bytes": inventory.bytes_for("backup"),
        "audit-backup-microseconds": inventory.backup_duration_microseconds,
        "audit-key-binding-count": inventory.key_binding_count,
        "audit-live-data-bytes": inventory.bytes_for("live"),
        "audit-live-row-count": inventory.live_row_count,
        "audit-quarantine-bytes": inventory.bytes_for("quarantine"),
        "audit-replay-microseconds": inventory.replay_duration_microseconds,
        "audit-replay-temporary-storage-bytes": inventory.replay_temporary_storage_bytes,
        "audit-restore-microseconds": inventory.restore_evidence.duration_microseconds,
        "audit-wal-bytes": inventory.bytes_for("wal"),
    }
    return all(capacity.covers(metric_id, value) for metric_id, value in values.items())


def _require_digest(value: object, name: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} digest is invalid")


def _bounded_nonnegative(value: object, name: str) -> None:
    if type(value) is not int or value < 0 or not is_safe_json_integer(value):
        raise ValueError(f"{name} is outside its admitted interval")


def _aware_datetime(value: object) -> bool:
    return type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None
