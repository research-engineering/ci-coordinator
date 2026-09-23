from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ci_coordinator.audit_replay.event import (
    AuditEventError,
    AuditEventRecord,
    AuditEventRecordLike,
    AuditJsonResourceFailure,
    audit_event_from_mapping,
    audit_event_hash,
    audit_input_hash_for_record,
)
from ci_coordinator.kernel import hash_object


@dataclass(frozen=True)
class ValidAuditChain:
    last_event_hash: str | None
    valid: Literal[True] = True


@dataclass(frozen=True)
class InvalidAuditChain:
    reason: str
    audit_event_id: str | None
    resource_failure: AuditJsonResourceFailure | None = None
    valid: Literal[False] = False


type AuditChainVerificationResult = ValidAuditChain | InvalidAuditChain


def verify_audit_chain(records: Sequence[AuditEventRecordLike]) -> AuditChainVerificationResult:
    _, verification = _normalize_and_verify_audit_chain(records)
    return verification


def verify_audit_chain_extension(
    previous_record: AuditEventRecordLike | None,
    records: Sequence[AuditEventRecordLike],
) -> AuditChainVerificationResult:
    """Verify a suffix against one previously verified immutable record."""

    _, verification = normalize_and_verify_audit_chain_extension(previous_record, records)
    return verification


def normalize_and_verify_audit_chain_extension(
    previous_record: AuditEventRecordLike | None,
    records: Sequence[AuditEventRecordLike],
) -> tuple[tuple[AuditEventRecord, ...], AuditChainVerificationResult]:
    """Normalize and verify one bounded suffix against a verified checkpoint."""

    if previous_record is None:
        return _normalize_and_verify_audit_chain(records)
    try:
        previous = audit_event_from_mapping(previous_record)
    except AuditEventError as error:
        return (
            (),
            InvalidAuditChain(
                reason=str(error),
                audit_event_id=error.audit_event_id,
                resource_failure=error.resource_failure,
            ),
        )
    normalized_records: list[AuditEventRecord] = []
    idempotency_keys = {previous.idempotency_key: previous.input_hash}
    for raw_record in records:
        try:
            record = audit_event_from_mapping(raw_record)
        except AuditEventError as error:
            return (
                tuple(normalized_records),
                InvalidAuditChain(
                    reason=str(error),
                    audit_event_id=error.audit_event_id,
                    resource_failure=error.resource_failure,
                ),
            )
        normalized_records.append(record)
        invalid = _verify_audit_chain_record(record, previous, idempotency_keys)
        if invalid is not None:
            return tuple(normalized_records), invalid
        previous = record
    return tuple(normalized_records), ValidAuditChain(last_event_hash=previous.event_hash)


def verify_audit_event_integrity(
    raw_record: AuditEventRecordLike,
) -> InvalidAuditChain | None:
    """Verify one event's content and derived identity without claiming chain position."""

    try:
        record = audit_event_from_mapping(raw_record)
    except AuditEventError as error:
        return InvalidAuditChain(
            reason=str(error),
            audit_event_id=error.audit_event_id,
            resource_failure=error.resource_failure,
        )
    return _verify_audit_event_content(record)


def _normalize_and_verify_audit_chain(
    records: Sequence[AuditEventRecordLike],
) -> tuple[tuple[AuditEventRecord, ...], AuditChainVerificationResult]:
    normalized_records: list[AuditEventRecord] = []
    previous: AuditEventRecord | None = None
    idempotency_keys: dict[str, str] = {}

    for raw_record in records:
        try:
            record = audit_event_from_mapping(raw_record)
        except AuditEventError as error:
            return tuple(normalized_records), InvalidAuditChain(
                reason=str(error),
                audit_event_id=error.audit_event_id,
                resource_failure=error.resource_failure,
            )
        normalized_records.append(record)

        invalid = _verify_audit_chain_record(record, previous, idempotency_keys)
        if invalid is not None:
            return tuple(normalized_records), invalid
        previous = record

    return tuple(normalized_records), ValidAuditChain(
        last_event_hash=previous.event_hash if previous is not None else None
    )


def _verify_audit_chain_record(
    record: AuditEventRecord,
    previous: AuditEventRecord | None,
    idempotency_keys: dict[str, str],
) -> InvalidAuditChain | None:
    expected_sequence = previous.sequence + 1 if previous is not None else 1
    if record.sequence != expected_sequence:
        return InvalidAuditChain(
            reason=f"expected sequence {expected_sequence}, got {record.sequence}",
            audit_event_id=record.audit_event_id,
        )

    expected_previous_hash = previous.event_hash if previous is not None else None
    if record.previous_event_hash != expected_previous_hash:
        return InvalidAuditChain(
            reason="previous event hash does not match",
            audit_event_id=record.audit_event_id,
        )

    invalid = _verify_audit_event_content(record)
    if invalid is not None:
        return invalid

    existing_input_hash = idempotency_keys.get(record.idempotency_key)
    if existing_input_hash is not None:
        reason = (
            "duplicate audit event idempotency key"
            if existing_input_hash == record.input_hash
            else "conflicting audit event idempotency key"
        )
        return InvalidAuditChain(reason=reason, audit_event_id=record.audit_event_id)

    idempotency_keys[record.idempotency_key] = record.input_hash
    return None


def _verify_audit_event_content(record: AuditEventRecord) -> InvalidAuditChain | None:

    try:
        expected_payload_hash = hash_object(record.payload)
    except ValueError as error:
        return InvalidAuditChain(reason=str(error), audit_event_id=record.audit_event_id)
    if record.payload_hash != expected_payload_hash:
        return InvalidAuditChain(
            reason="payload hash does not match payload",
            audit_event_id=record.audit_event_id,
        )

    expected_input_hash = audit_input_hash_for_record(record, expected_payload_hash)
    if record.input_hash != expected_input_hash:
        return InvalidAuditChain(
            reason="input hash does not match audit event input",
            audit_event_id=record.audit_event_id,
        )

    expected_event_hash = audit_event_hash(
        schema_version=record.schema_version,
        idempotency_key=record.idempotency_key,
        installation_id=record.installation_id,
        repository_id=record.repository_id,
        subject_type=record.subject_type,
        subject_id=record.subject_id,
        event_type=record.event_type,
        created_at=record.created_at,
        actor=record.actor,
        sequence=record.sequence,
        previous_event_hash=record.previous_event_hash,
        payload_hash=record.payload_hash,
        input_hash=record.input_hash,
    )
    if record.event_hash != expected_event_hash:
        return InvalidAuditChain(
            reason="event hash does not match event identity",
            audit_event_id=record.audit_event_id,
        )

    if record.audit_event_id != f"audit_{record.event_hash[:32]}":
        return InvalidAuditChain(
            reason="audit event id does not match event hash",
            audit_event_id=record.audit_event_id,
        )

    return None


def audit_chain_verification_to_mapping(result: AuditChainVerificationResult) -> dict[str, object]:
    if result.valid:
        return {"valid": True, "lastEventHash": result.last_event_hash}
    mapped: dict[str, object] = {
        "valid": False,
        "reason": result.reason,
        "auditEventId": result.audit_event_id,
    }
    if result.resource_failure is not None:
        mapped["resourceFailure"] = result.resource_failure.to_mapping()
    return mapped


def invalid_audit_chain_to_event_error(result: InvalidAuditChain) -> AuditEventError:
    location = result.audit_event_id or "genesis"
    return AuditEventError(
        f"invalid audit ledger at {location}: {result.reason}",
        audit_event_id=result.audit_event_id,
        resource_failure=result.resource_failure,
    )
