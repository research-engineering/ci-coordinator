"""Bounded seeded-data and validated-constraint facts for capabilities."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.schema import APPLICATION_SCHEMA


async def audit_ledger_seed_is_valid(connection: AsyncConnection) -> bool:
    """The singleton audit-head seed is a required, bounded data fact."""
    return await connection.run_sync(audit_ledger_seed_is_valid_sync)


def audit_ledger_seed_is_valid_sync(connection: Connection) -> bool:
    result = connection.scalar(
        text(
            "SELECT count(*) = 1 FROM ci_coordinator.audit_ledger_head "
            "WHERE head_id = 1 AND revision >= 0 AND "
            "((revision = 0 AND last_sequence IS NULL AND last_event_hash IS NULL) "
            "OR (revision >= 1 AND revision = last_sequence AND last_event_hash IS NOT NULL))"
        )
    )
    return result is True


async def audit_ledger_bridge_constraints_are_validated(connection: AsyncConnection) -> bool:
    """Read the finite audit data-domain bridge constraints without policy decisions."""
    return await connection.run_sync(audit_ledger_bridge_constraints_are_validated_sync)


def audit_ledger_bridge_constraints_are_validated_sync(connection: Connection) -> bool:
    result = connection.execute(
        text(
            "SELECT conname, convalidated FROM pg_constraint "
            "WHERE conrelid = 'ci_coordinator.audit_events'::regclass "
            "AND conname IN ("
            "'ck_audit_events_payload_canonical_json_byte_limit', "
            "'ck_audit_events_idempotency_key_byte_limit', "
            "'ck_audit_events_subject_id_byte_limit', "
            "'ck_audit_events_event_type_byte_limit', "
            "'ck_audit_events_actor_byte_limit') ORDER BY conname"
        )
    )
    facts = tuple((row[0], row[1] is True) for row in result)
    expected_names = (
        "ck_audit_events_actor_byte_limit",
        "ck_audit_events_event_type_byte_limit",
        "ck_audit_events_idempotency_key_byte_limit",
        "ck_audit_events_payload_canonical_json_byte_limit",
        "ck_audit_events_subject_id_byte_limit",
    )
    return facts == tuple((name, True) for name in expected_names)


async def application_schema_is_empty_of_public_default_privileges(
    connection: AsyncConnection,
) -> bool:
    """Return whether the application schema has no PUBLIC default ACL grant."""
    return await connection.run_sync(application_schema_is_empty_of_public_default_privileges_sync)


def application_schema_is_empty_of_public_default_privileges_sync(
    connection: Connection,
) -> bool:
    result = connection.scalar(
        text(
            "SELECT NOT EXISTS ("
            "SELECT 1 FROM pg_default_acl AS default_acl "
            "JOIN pg_namespace AS namespace ON namespace.oid = default_acl.defaclnamespace "
            "CROSS JOIN LATERAL aclexplode(default_acl.defaclacl) AS privilege "
            "WHERE namespace.nspname = :schema AND privilege.grantee = 0"
            ")"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    return result is True
