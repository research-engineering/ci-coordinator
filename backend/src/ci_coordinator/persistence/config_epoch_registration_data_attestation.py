"""Two-way data attestation for config-registration audit pairs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, cast

from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import verify_audit_event_integrity
from ci_coordinator.config_epochs import CONFIG_EPOCH_REGISTRATION_EVENT_TYPE
from ci_coordinator.persistence.audit_codec import row_to_record
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.scalar_rows import required_bytes as _required_bytes
from ci_coordinator.persistence.scalar_rows import required_int as _required_int
from ci_coordinator.persistence.scalar_rows import required_string as _required_str
from ci_coordinator.persistence.schema import (
    audit_events,
    config_epoch_registrations,
    config_epochs,
)

_AUDIT_SCHEMA: Final = "config-epoch-registration-audit/v1"
_IDEMPOTENCY_PREFIX: Final = "config-epoch-registration:"
_SUBJECT_PREFIX: Final = "config-epoch:"

_PAIR_ROWS = select(
    *audit_events.c,
    config_epoch_registrations.c.installation_id.label("registration_installation_id"),
    config_epoch_registrations.c.repository_id.label("registration_repository_id"),
    config_epoch_registrations.c.operation_id.label("registration_operation_id"),
    config_epoch_registrations.c.epoch_id.label("registration_epoch_id"),
    config_epoch_registrations.c.audit_event_id.label("registration_audit_event_id"),
    config_epoch_registrations.c.audit_input_hash.label("registration_audit_input_hash"),
    config_epochs.c.source_format.label("registration_source_format"),
    config_epochs.c.source_hash.label("registration_source_hash"),
    config_epochs.c.document_hash.label("registration_document_hash"),
    config_epochs.c.epoch_hash.label("registration_epoch_hash"),
).select_from(
    config_epoch_registrations.join(
        config_epochs,
        (config_epoch_registrations.c.installation_id == config_epochs.c.installation_id)
        & (config_epoch_registrations.c.repository_id == config_epochs.c.repository_id)
        & (config_epoch_registrations.c.epoch_id == config_epochs.c.epoch_id),
    ).outerjoin(
        audit_events,
        config_epoch_registrations.c.audit_event_id == audit_events.c.audit_event_id,
    )
)


async def config_epoch_registration_pairs_match_contract(
    connection: AsyncConnection,
) -> bool:
    return await connection.run_sync(config_epoch_registration_pairs_match_contract_sync)


def config_epoch_registration_pairs_match_contract_sync(connection: Connection) -> bool:
    """Return whether registrations and their owner events form one exact bijection."""

    orphan_exists = connection.scalar(
        text(
            "SELECT EXISTS ("
            "SELECT 1 FROM ci_coordinator.audit_events AS event "
            "LEFT JOIN ci_coordinator.config_epoch_registrations AS registration "
            "ON registration.audit_event_id = event.audit_event_id "
            "WHERE event.event_type = convert_to(:event_type, 'UTF8') "
            "AND registration.audit_event_id IS NULL)"
        ),
        {"event_type": CONFIG_EPOCH_REGISTRATION_EVENT_TYPE},
    )
    if orphan_exists is not False:
        return False

    result = connection.execute(_PAIR_ROWS.execution_options(stream_results=True)).mappings()
    try:
        return all(_pair_matches(cast(Mapping[str, object], row)) for row in result)
    finally:
        result.close()


def _pair_matches(row: Mapping[str, object]) -> bool:
    try:
        event = row_to_record(row)
        installation_id = _required_int(row, "registration_installation_id")
        repository_id = _required_int(row, "registration_repository_id")
        operation_id = _required_str(row, "registration_operation_id")
        epoch_id = _required_str(row, "registration_epoch_id")
        registration_audit_event_id = _required_str(
            row,
            "registration_audit_event_id",
        )
        registration_input_hash = _required_bytes(
            row,
            "registration_audit_input_hash",
        ).hex()
        expected_payload = {
            "schemaVersion": _AUDIT_SCHEMA,
            "operationId": operation_id,
            "epochId": epoch_id,
            "sourceFormat": _required_str(row, "registration_source_format"),
            "sourceHash": _required_str(row, "registration_source_hash"),
            "documentHash": _required_str(row, "registration_document_hash"),
            "epochHash": _required_str(row, "registration_epoch_hash"),
        }
    except (KeyError, PersistenceInvariantViolation, TypeError, ValueError):
        return False

    return (
        verify_audit_event_integrity(event) is None
        and event.audit_event_id == registration_audit_event_id
        and event.input_hash == registration_input_hash
        and event.idempotency_key
        == f"{_IDEMPOTENCY_PREFIX}{installation_id}:{repository_id}:{operation_id}"
        and event.installation_id == installation_id
        and event.repository_id == repository_id
        and event.subject_type == "policy-decision"
        and event.subject_id == f"{_SUBJECT_PREFIX}{installation_id}:{repository_id}"
        and event.event_type == CONFIG_EPOCH_REGISTRATION_EVENT_TYPE
        and event.payload == expected_payload
    )
