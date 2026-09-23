"""Catalog and runtime-principal attestation for operator override state."""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.operator_override_schema import operator_overrides
from ci_coordinator.persistence.schema import APPLICATION_SCHEMA


def operator_override_state_schema_matches_contract_sync(connection: Connection) -> bool:
    relation = "operator_overrides"
    columns = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT attribute.attname, format_type(attribute.atttypid, attribute.atttypmod), "
                "NOT attribute.attnotnull FROM pg_attribute AS attribute "
                "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname = :relation "
                "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
                "ORDER BY attribute.attnum"
            ),
            {"schema": APPLICATION_SCHEMA, "relation": relation},
        )
    )
    constraints = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT conname, contype, pg_get_constraintdef(pg_constraint.oid, true), "
                "condeferrable, condeferred, convalidated, conenforced "
                "FROM pg_constraint JOIN pg_class AS relation ON relation.oid = conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname = :relation "
                "AND contype <> 'n' ORDER BY conname"
            ),
            {"schema": APPLICATION_SCHEMA, "relation": relation},
        )
    )
    table_fact = connection.execute(
        text(
            "SELECT relation.relkind, relation.relpersistence, relation.relrowsecurity, "
            "relation.relforcerowsecurity FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname = :relation"
        ),
        {"schema": APPLICATION_SCHEMA, "relation": relation},
    ).one_or_none()
    indexes = tuple(
        connection.scalars(
            text(
                "SELECT pg_get_indexdef(index_metadata.indexrelid) "
                "FROM pg_index AS index_metadata "
                "JOIN pg_class AS relation ON relation.oid = index_metadata.indrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "LEFT JOIN pg_constraint AS constraint_metadata "
                "ON constraint_metadata.conindid = index_metadata.indexrelid "
                "WHERE namespace.nspname = :schema AND relation.relname = :relation "
                "AND constraint_metadata.oid IS NULL ORDER BY 1"
            ),
            {"schema": APPLICATION_SCHEMA, "relation": relation},
        )
    )
    unexpected = connection.scalar(
        text(
            "SELECT (SELECT count(*) FROM pg_trigger WHERE tgrelid = "
            "to_regclass(:qualified) AND NOT tgisinternal) + "
            "(SELECT count(*) FROM pg_policy WHERE polrelid = to_regclass(:qualified))"
        ),
        {"qualified": f"{APPLICATION_SCHEMA}.{relation}"},
    )
    return (
        columns == _EXPECTED_COLUMNS
        and constraints == _EXPECTED_CONSTRAINTS
        and table_fact is not None
        and tuple(table_fact) == ("r", "p", False, False)
        and indexes == _EXPECTED_INDEXES
        and unexpected == 0
    )


async def operator_override_state_schema_matches_contract(
    connection: AsyncConnection,
) -> bool:
    return await connection.run_sync(operator_override_state_schema_matches_contract_sync)


async def operator_override_runtime_principal_is_restricted(
    connection: AsyncConnection,
) -> bool:
    columns = [column.name for column in operator_overrides.c]
    result = await connection.scalar(
        text(
            "WITH principal AS (SELECT oid FROM pg_roles WHERE rolname = session_user), "
            "relation AS (SELECT relation.oid FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname = :relation) "
            "SELECT EXISTS (SELECT 1 FROM principal) AND EXISTS (SELECT 1 FROM relation) "
            "AND NOT has_table_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), 'SELECT') "
            "AND NOT has_table_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), 'INSERT') "
            "AND NOT has_table_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), 'UPDATE') "
            "AND NOT has_table_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), 'DELETE') "
            "AND NOT has_table_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), 'TRUNCATE') "
            "AND NOT has_table_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), 'REFERENCES') "
            "AND NOT has_table_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), 'TRIGGER') "
            "AND NOT has_table_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), 'MAINTAIN') "
            "AND NOT EXISTS (SELECT 1 FROM unnest(CAST(:columns AS text[])) AS column_name "
            "WHERE NOT has_column_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), column_name, 'SELECT') "
            "OR NOT has_column_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), column_name, 'INSERT') "
            "OR has_column_privilege((SELECT oid FROM principal), "
            "(SELECT oid FROM relation), column_name, 'UPDATE')) "
            "AND NOT EXISTS (SELECT 1 FROM pg_attribute AS attribute "
            "CROSS JOIN principal WHERE attribute.attrelid = (SELECT oid FROM relation) "
            "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
            "AND EXISTS (SELECT 1 FROM aclexplode(COALESCE(attribute.attacl, acldefault('c', "
            "(SELECT relowner FROM pg_class WHERE oid = attribute.attrelid)))) AS privilege "
            "WHERE privilege.grantee = principal.oid AND privilege.is_grantable))"
        ),
        {
            "schema": APPLICATION_SCHEMA,
            "relation": operator_overrides.name,
            "columns": columns,
        },
    )
    return result is True


_EXPECTED_COLUMNS: Final = (
    ("override_id", "character varying(41)", False),
    ("operation_id", "character varying(512)", False),
    ("installation_id", "bigint", False),
    ("repository_id", "bigint", False),
    ("kind", "character varying(32)", False),
    ("target_subject_id", "character varying(512)", True),
    ("expires_at", "timestamp with time zone", True),
    ("applied_at", "timestamp with time zone", False),
    ("record_canonical_json", "bytea", False),
    ("semantic_hash", "character varying(64)", False),
    ("audit_event_id", "character varying(38)", False),
    ("audit_input_hash", "bytea", False),
)

_EXPECTED_CONSTRAINTS: Final = (
    (
        "ck_operator_overrides_audit_identity",
        "c",
        (
            "CHECK (audit_event_id::text ~ '^audit_[0-9a-f]{32}$'::text AND "
            "octet_length(audit_input_hash) = 32)"
        ),
        False,
        False,
        True,
        True,
    ),
    (
        "ck_operator_overrides_identity",
        "c",
        (
            "CHECK (override_id::text ~ '^override_[0-9a-f]{32}$'::text AND "
            "octet_length(operation_id::text) >= 1 AND octet_length(operation_id::text) <= 512)"
        ),
        False,
        False,
        True,
        True,
    ),
    (
        "ck_operator_overrides_kind_target",
        "c",
        (
            "CHECK (kind::text = 'force_full_ci'::text AND target_subject_id IS NOT NULL AND "
            "octet_length(target_subject_id::text) >= 1 AND "
            "octet_length(target_subject_id::text) <= 512 AND expires_at IS NOT NULL OR "
            "kind::text = "
            "'disable_omission'::text AND target_subject_id IS NULL AND expires_at IS NULL OR "
            "kind::text = 'enable_omission'::text AND target_subject_id::text ~ "
            "'^override_[0-9a-f]{32}$'::text AND expires_at IS NULL)"
        ),
        False,
        False,
        True,
        True,
    ),
    (
        "ck_operator_overrides_lifetime",
        "c",
        "CHECK (expires_at IS NULL OR expires_at > applied_at)",
        False,
        False,
        True,
        True,
    ),
    (
        "ck_operator_overrides_record_byte_limit",
        "c",
        (
            "CHECK (octet_length(record_canonical_json) >= 1 AND "
            "octet_length(record_canonical_json) <= 8192)"
        ),
        False,
        False,
        True,
        True,
    ),
    (
        "ck_operator_overrides_scope_safe",
        "c",
        (
            "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint "
            "AND repository_id >= 1 AND repository_id <= '9007199254740991'::bigint)"
        ),
        False,
        False,
        True,
        True,
    ),
    (
        "ck_operator_overrides_semantic_hash",
        "c",
        "CHECK (semantic_hash::text ~ '^[0-9a-f]{64}$'::text)",
        False,
        False,
        True,
        True,
    ),
    (
        "fk_operator_overrides_audit_event",
        "f",
        (
            "FOREIGN KEY (audit_event_id) REFERENCES ci_coordinator.audit_events(audit_event_id) "
            "ON DELETE RESTRICT"
        ),
        False,
        False,
        True,
        True,
    ),
    ("pk_operator_overrides", "p", "PRIMARY KEY (override_id)", False, False, True, True),
    (
        "uq_operator_overrides_audit_event",
        "u",
        "UNIQUE (audit_event_id)",
        False,
        False,
        True,
        True,
    ),
    (
        "uq_operator_overrides_operation",
        "u",
        "UNIQUE (installation_id, repository_id, operation_id)",
        False,
        False,
        True,
        True,
    ),
)

_EXPECTED_INDEXES: Final = (
    (
        "CREATE INDEX ix_operator_overrides_resolution ON ci_coordinator.operator_overrides "
        "USING btree (installation_id, repository_id, kind, target_subject_id, "
        "applied_at DESC, override_id DESC)"
    ),
)
