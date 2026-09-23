from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast, overload

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ci_coordinator.audit_replay import (
    AllAuditReplayFilter,
    AuditChainTipStatement,
    AuditCheckpointRejection,
    AuditEventRecord,
    AuditLedgerSnapshot,
    AuthenticatedAuditCheckpoint,
    InvalidAuditSuccessor,
    ValidAuditSuccessor,
    admit_audit_checkpoint,
    audit_checkpoint_signature_payload,
    build_audit_chain_tip_statement,
    build_audit_event,
    encode_audit_checkpoint_envelope,
    verify_audit_successor_chain,
)
from ci_coordinator.audit_replay.paged_replay import AuditReplayScan, scan_audit_replay
from ci_coordinator.kernel import FixedClock, canonical_json, hash_object, load_strict_json

from ._event_test_support import scalar_event_input

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
ARTIFACT_DIGEST = "sha256:" + "a" * 64
DATABASE_DIGEST = "b" * 64
KEY_ID = "audit-custody-2026-09"
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(65, 97)))
PUBLIC_KEY_PEM = PRIVATE_KEY.public_key().public_bytes(
    Encoding.PEM,
    PublicFormat.SubjectPublicKeyInfo,
)


class _AuditRepository:
    def __init__(self, records: tuple[AuditEventRecord, ...]) -> None:
        self._records = records

    async def snapshot(self) -> AuditLedgerSnapshot:
        last = self._records[-1]
        return AuditLedgerSnapshot(
            last_sequence=last.sequence,
            last_event_hash=last.event_hash,
            maximum_sequence=last.sequence,
        )

    async def load_page(
        self,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
    ) -> tuple[AuditEventRecord, ...]:
        return tuple(
            record
            for record in self._records
            if after_sequence < record.sequence <= through_sequence
        )[:limit]


def _events() -> tuple[AuditEventRecord, AuditEventRecord, AuditEventRecord]:
    first = build_audit_event(
        scalar_event_input({"value": 1}, idempotency_key="checkpoint-first"),
        None,
    )
    second = build_audit_event(
        scalar_event_input({"value": 2}, idempotency_key="checkpoint-second"),
        first,
    )
    third = build_audit_event(
        scalar_event_input({"value": 3}, idempotency_key="checkpoint-third"),
        second,
    )
    return first, second, third


def _encode_checkpoint(
    statement: AuditChainTipStatement,
    *,
    private_key: Ed25519PrivateKey = PRIVATE_KEY,
    signed_at: datetime = NOW,
    expires_at: datetime | None = None,
) -> bytes:
    admitted_expires_at = expires_at or signed_at + timedelta(hours=1)
    signature = private_key.sign(
        audit_checkpoint_signature_payload(
            statement,
            key_id=KEY_ID,
            signed_at=signed_at,
            expires_at=admitted_expires_at,
        )
    )
    return encode_audit_checkpoint_envelope(
        statement,
        key_id=KEY_ID,
        signed_at=signed_at,
        expires_at=admitted_expires_at,
        signature=base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
    )


def make_authenticated_checkpoint() -> tuple[AuthenticatedAuditCheckpoint, AuditEventRecord]:
    first, second, third = _events()
    scan = asyncio.run(scan_audit_replay(_AuditRepository((first, second)), AllAuditReplayFilter()))
    statement = build_audit_chain_tip_statement(
        scan,
        database_identity_digest=DATABASE_DIGEST,
        artifact_digest=ARTIFACT_DIGEST,
        observed_at=NOW,
    )
    content = _encode_checkpoint(statement)
    admitted = admit_audit_checkpoint(
        content,
        public_key_pem=PUBLIC_KEY_PEM,
        expected_key_id=KEY_ID,
        expected_database_identity_digest=DATABASE_DIGEST,
        expected_artifact_digest=ARTIFACT_DIGEST,
        clock=FixedClock(NOW),
    )
    assert isinstance(admitted, AuthenticatedAuditCheckpoint)
    return admitted, third


def test_checkpoint_is_produced_from_a_complete_valid_replay_and_authenticated() -> None:
    checkpoint, third = make_authenticated_checkpoint()

    successor = verify_audit_successor_chain(checkpoint.statement, (third,))

    assert checkpoint.authenticated is True
    assert checkpoint.statement.retained_sequence == 2
    assert isinstance(successor, ValidAuditSuccessor)
    assert successor.event_count == 1
    assert successor.last_sequence == 3


def test_checkpoint_statement_rejects_a_non_json_safe_retained_sequence() -> None:
    checkpoint, _third = make_authenticated_checkpoint()

    with pytest.raises(ValueError, match="retained sequence"):
        replace(checkpoint.statement, retained_sequence=9_007_199_254_740_992)


def test_checkpoint_rejects_a_resigned_contradictory_event_identity_and_hash() -> None:
    checkpoint, _third = make_authenticated_checkpoint()
    root = cast(dict[str, object], load_strict_json(_encode_checkpoint(checkpoint.statement)))
    statement = cast(dict[str, object], root["statement"])
    event_hash = cast(str, statement["lastEventHash"])
    replacement = "0" if event_hash[0] != "0" else "1"
    statement["lastEventId"] = f"audit_{replacement}{event_hash[1:32]}"
    statement["statementDigest"] = hash_object(
        {key: value for key, value in statement.items() if key != "statementDigest"}
    )
    unsigned = {key: value for key, value in root.items() if key != "signature"}
    signature = PRIVATE_KEY.sign(canonical_json(unsigned))
    root["signature"] = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

    admitted = admit_audit_checkpoint(
        canonical_json(root) + b"\n",
        public_key_pem=PUBLIC_KEY_PEM,
        expected_key_id=KEY_ID,
        expected_database_identity_digest=DATABASE_DIGEST,
        expected_artifact_digest=ARTIFACT_DIGEST,
        clock=FixedClock(NOW),
    )

    assert admitted == AuditCheckpointRejection("audit_checkpoint_invalid")


def test_audit_replay_scan_cannot_be_forged_by_a_checkpoint_caller() -> None:
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        AuditReplayScan(
            object(),
            snapshot=AuditLedgerSnapshot(0, None, None),
            report=AllAuditReplayFilter(),  # type: ignore[arg-type]
            matched_event_count=0,
            last_event=None,
        )


def test_checkpoint_signature_and_exact_environment_are_independent_conjuncts() -> None:
    checkpoint, _ = make_authenticated_checkpoint()
    statement = checkpoint.statement
    wrong_key = Ed25519PrivateKey.from_private_bytes(bytes(range(97, 129)))

    invalid_signature = admit_audit_checkpoint(
        _encode_checkpoint(statement, private_key=wrong_key),
        public_key_pem=PUBLIC_KEY_PEM,
        expected_key_id=KEY_ID,
        expected_database_identity_digest=DATABASE_DIGEST,
        expected_artifact_digest=ARTIFACT_DIGEST,
        clock=FixedClock(NOW),
    )

    assert invalid_signature == AuditCheckpointRejection("audit_checkpoint_signature_invalid")

    foreign_environment = admit_audit_checkpoint(
        _encode_checkpoint(statement),
        public_key_pem=PUBLIC_KEY_PEM,
        expected_key_id=KEY_ID,
        expected_database_identity_digest="c" * 64,
        expected_artifact_digest=ARTIFACT_DIGEST,
        clock=FixedClock(NOW),
    )

    assert foreign_environment == AuditCheckpointRejection("audit_checkpoint_foreign")


def test_checkpoint_clock_skew_is_consistent_with_returned_validity() -> None:
    checkpoint, _ = make_authenticated_checkpoint()
    content = _encode_checkpoint(
        checkpoint.statement,
        signed_at=NOW + timedelta(minutes=5),
    )

    admitted = admit_audit_checkpoint(
        content,
        public_key_pem=PUBLIC_KEY_PEM,
        expected_key_id=KEY_ID,
        expected_database_identity_digest=DATABASE_DIGEST,
        expected_artifact_digest=ARTIFACT_DIGEST,
        clock=FixedClock(NOW),
    )

    assert isinstance(admitted, AuthenticatedAuditCheckpoint)
    assert admitted.is_valid_at(NOW)


def test_checkpoint_rejects_signature_beyond_the_clock_skew() -> None:
    checkpoint, _ = make_authenticated_checkpoint()
    content = _encode_checkpoint(
        checkpoint.statement,
        signed_at=NOW + timedelta(minutes=5, milliseconds=1),
    )

    admitted = admit_audit_checkpoint(
        content,
        public_key_pem=PUBLIC_KEY_PEM,
        expected_key_id=KEY_ID,
        expected_database_identity_digest=DATABASE_DIGEST,
        expected_artifact_digest=ARTIFACT_DIGEST,
        clock=FixedClock(NOW),
    )

    assert admitted == AuditCheckpointRejection("audit_checkpoint_not_yet_valid")


def test_successor_rejects_a_broken_checkpoint_link() -> None:
    checkpoint, third = make_authenticated_checkpoint()

    result = verify_audit_successor_chain(
        checkpoint.statement,
        (replace(third, previous_event_hash="0" * 64),),
    )

    assert isinstance(result, InvalidAuditSuccessor)
    assert result.reason == "audit successor previous hash does not match"


def test_successor_bounds_iteration_independently_from_sequence_length() -> None:
    checkpoint, _third = make_authenticated_checkpoint()

    class ContradictorySequence(Sequence[AuditEventRecord]):
        def __len__(self) -> int:
            return 1

        @overload
        def __getitem__(self, index: int) -> AuditEventRecord: ...

        @overload
        def __getitem__(self, index: slice) -> Sequence[AuditEventRecord]: ...

        def __getitem__(self, index: int | slice) -> AuditEventRecord | Sequence[AuditEventRecord]:
            raise IndexError(index)

        def __iter__(self) -> Iterator[AuditEventRecord]:
            first, _second, _third = _events()
            yield from (first for _ in range(4_097))

    result = verify_audit_successor_chain(checkpoint.statement, ContradictorySequence())

    assert result == InvalidAuditSuccessor("audit successor count is outside its bound", None)
