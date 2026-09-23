from __future__ import annotations

from dataclasses import replace

import pytest
from audit_replay.test_checkpoint import (
    ARTIFACT_DIGEST,
    DATABASE_DIGEST,
    make_authenticated_checkpoint,
)

from capacity_qualification._support import (
    CAPACITY_KEY_ID,
    CAPACITY_PUBLIC_KEY_PEM,
    NOW,
    capacity_expectation,
    make_capacity_identity,
    make_capacity_receipt,
    sign_capacity_receipt,
)
from ci_coordinator.audit_replay import ValidAuditSuccessor, verify_audit_successor_chain
from ci_coordinator.capacity_qualification import (
    AuditRestoreEvidence,
    AuditRetentionPrerequisiteRejected,
    AuditStorageCopy,
    AuditStorageCopyKind,
    AuditStorageInventory,
    CapacityQualified,
    VerifiedAuditRetentionPrerequisite,
    admit_capacity_receipt,
    assess_audit_retention_prerequisite,
)
from ci_coordinator.kernel import FixedClock


def _inventory(
    checkpoint_digest: str,
    successor_digest: str,
    *,
    live_bytes: int = 10,
) -> AuditStorageInventory:
    key_digest = "8" * 64
    copy_specs: tuple[tuple[AuditStorageCopyKind, str, str, int], ...] = (
        ("backup", "backup-primary", "9" * 64, 20),
        ("live", "live-primary", "a" * 64, live_bytes),
        ("quarantine", "quarantine-inventory", "b" * 64, 0),
        ("wal", "wal-primary", "c" * 64, 5),
    )
    copies = tuple(
        AuditStorageCopy(
            copy_id=copy_id,
            kind=kind,
            content_digest=content_digest,
            size_bytes=size,
            checkpoint_envelope_digest=checkpoint_digest,
            successor_chain_digest=successor_digest,
            key_binding_inventory_digest=key_digest,
        )
        for kind, copy_id, content_digest, size in copy_specs
    )
    return AuditStorageInventory(
        database_identity_digest=DATABASE_DIGEST,
        checkpoint_envelope_digest=checkpoint_digest,
        successor_chain_digest=successor_digest,
        copies=copies,
        key_binding_inventory_digest=key_digest,
        key_binding_count=2,
        live_row_count=3,
        backup_duration_microseconds=40,
        replay_duration_microseconds=50,
        replay_temporary_storage_bytes=60,
        restore_evidence=AuditRestoreEvidence(
            content_digest="9" * 64,
            source_copy_id="backup-primary",
            checkpoint_envelope_digest=checkpoint_digest,
            successor_chain_digest=successor_digest,
            duration_microseconds=70,
        ),
    )


def _qualified_capacity(inventory: AuditStorageInventory) -> CapacityQualified:
    identity = make_capacity_identity(
        artifact_digest=ARTIFACT_DIGEST,
        database_identity_digest=DATABASE_DIGEST,
        audit_storage_inventory_digest=inventory.inventory_digest,
    )
    receipt = make_capacity_receipt(identity=identity)
    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )
    assert isinstance(result, CapacityQualified)
    return result


def test_retention_prerequisite_closes_checkpoint_successor_inventory_and_capacity() -> None:
    checkpoint, successor_event = make_authenticated_checkpoint()
    successor = verify_audit_successor_chain(checkpoint.statement, (successor_event,))
    assert isinstance(successor, ValidAuditSuccessor)
    inventory = _inventory(checkpoint.envelope_digest, successor.successor_chain_digest)

    result = assess_audit_retention_prerequisite(
        checkpoint,
        (successor_event,),
        inventory,
        _qualified_capacity(inventory),
        clock=FixedClock(NOW),
    )

    assert isinstance(result, VerifiedAuditRetentionPrerequisite)
    assert result.verified is True
    assert result.inventory_digest == inventory.inventory_digest


def test_retention_rejects_inventory_not_bound_by_the_capacity_receipt() -> None:
    checkpoint, successor_event = make_authenticated_checkpoint()
    successor = verify_audit_successor_chain(checkpoint.statement, (successor_event,))
    assert isinstance(successor, ValidAuditSuccessor)
    inventory = _inventory(checkpoint.envelope_digest, successor.successor_chain_digest)
    capacity = _qualified_capacity(inventory)
    changed_copy = replace(inventory.copies[1], content_digest="e" * 64)
    changed_inventory = replace(
        inventory,
        copies=(inventory.copies[0], changed_copy, *inventory.copies[2:]),
    )

    result = assess_audit_retention_prerequisite(
        checkpoint,
        (successor_event,),
        changed_inventory,
        capacity,
        clock=FixedClock(NOW),
    )

    assert result == AuditRetentionPrerequisiteRejected("audit_retention_inventory_mismatch")


def test_inventory_rejects_restore_bytes_from_another_backup() -> None:
    checkpoint, successor_event = make_authenticated_checkpoint()
    successor = verify_audit_successor_chain(checkpoint.statement, (successor_event,))
    assert isinstance(successor, ValidAuditSuccessor)
    inventory = _inventory(checkpoint.envelope_digest, successor.successor_chain_digest)

    with pytest.raises(ValueError, match="does not bind a retained backup"):
        replace(
            inventory,
            restore_evidence=replace(inventory.restore_evidence, content_digest="d" * 64),
        )


def test_retention_rejects_storage_larger_than_the_qualified_observation() -> None:
    checkpoint, successor_event = make_authenticated_checkpoint()
    successor = verify_audit_successor_chain(checkpoint.statement, (successor_event,))
    assert isinstance(successor, ValidAuditSuccessor)
    inventory = _inventory(
        checkpoint.envelope_digest,
        successor.successor_chain_digest,
        live_bytes=101,
    )

    result = assess_audit_retention_prerequisite(
        checkpoint,
        (successor_event,),
        inventory,
        _qualified_capacity(inventory),
        clock=FixedClock(NOW),
    )

    assert result == AuditRetentionPrerequisiteRejected("audit_retention_capacity_insufficient")
