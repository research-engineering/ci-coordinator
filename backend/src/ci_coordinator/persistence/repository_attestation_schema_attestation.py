"""Catalog attestation for one-use repository-attestation transactions."""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA

_RELATION: Final = "repository_attestation_transactions"
_EXPECTED_COLUMNS: Final = (
    ("transaction_digest", "bytea", False, None, "x"),
    ("session_handle_digest", "bytea", False, None, "x"),
    ("installation_id", "bigint", False, None, "p"),
    ("repository_id", "bigint", False, None, "p"),
    ("operation_id", "character varying(256)", False, None, "x"),
    ("proposal_manifest_id", "character varying(41)", False, None, "x"),
    ("provider_revision", "character varying(40)", False, None, "x"),
    ("proposal_digest", "character varying(64)", False, None, "x"),
    ("expected_active_epoch_id", "character varying(64)", True, None, "x"),
    ("expected_active_revision", "bigint", True, None, "p"),
    ("initiating_actor", "character varying(82)", False, None, "x"),
    ("authority_profile_digest", "character varying(64)", False, None, "x"),
    ("issued_at", "timestamp with time zone", False, None, "p"),
    ("expires_at", "timestamp with time zone", False, None, "p"),
)
_EXPECTED_CONSTRAINT_DEFINITIONS: Final = {
    "ck_repository_attestation_transactions_active": (
        "CHECK (expected_active_epoch_id IS NULL AND expected_active_revision IS NULL OR "
        "expected_active_epoch_id::text ~ '^[0-9a-f]{64}$'::text AND "
        "expected_active_revision >= 1 AND "
        "expected_active_revision <= '9007199254740991'::bigint)"
    ),
    "ck_repository_attestation_transactions_authority": (
        "CHECK (initiating_actor::text ~ '^keycloak-human:v1:[0-9a-f]{64}$'::text AND "
        "authority_profile_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    "ck_repository_attestation_transactions_digests": (
        "CHECK (octet_length(transaction_digest) = 32 AND octet_length(session_handle_digest) = 32)"
    ),
    "ck_repository_attestation_transactions_operation": (
        "CHECK (octet_length(operation_id::text) >= 1 AND "
        "octet_length(operation_id::text) <= 256 AND "
        "proposal_manifest_id::text ~ '^proposal:[0-9a-f]{32}$'::text)"
    ),
    "ck_repository_attestation_transactions_proposal": (
        "CHECK (provider_revision::text ~ '^[0-9a-f]{40}$'::text AND "
        "proposal_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    "ck_repository_attestation_transactions_scope": (
        "CHECK (installation_id >= 1 AND "
        "installation_id <= '9007199254740991'::bigint AND repository_id >= 1 AND "
        "repository_id <= '9007199254740991'::bigint)"
    ),
    "ck_repository_attestation_transactions_time": (
        "CHECK (isfinite(issued_at) AND isfinite(expires_at) AND expires_at > issued_at "
        "AND expires_at <= (issued_at + '00:05:00'::interval))"
    ),
    "fk_repository_attestation_transactions_active_epoch": (
        "FOREIGN KEY (installation_id, repository_id, expected_active_epoch_id) REFERENCES "
        "ci_coordinator.config_epochs(installation_id, repository_id, epoch_id) "
        "ON DELETE RESTRICT"
    ),
    "fk_repository_attestation_transactions_session": (
        "FOREIGN KEY (session_handle_digest) REFERENCES "
        "ci_coordinator.control_plane_sessions(handle_digest) ON DELETE CASCADE"
    ),
    "pk_repository_attestation_transactions": "PRIMARY KEY (transaction_digest)",
    "uq_repository_attestation_transactions_operation": (
        "UNIQUE (installation_id, repository_id, operation_id)"
    ),
}
_EXPECTED_INDEXES: Final = frozenset(
    {
        (
            "ix_repository_attestation_transactions_expiry",
            False,
            False,
            True,
            True,
            True,
            "CREATE INDEX ix_repository_attestation_transactions_expiry ON "
            "ci_coordinator.repository_attestation_transactions USING btree "
            "(expires_at, transaction_digest)",
        ),
        (
            "ix_repository_attestation_transactions_session",
            False,
            False,
            True,
            True,
            True,
            "CREATE INDEX ix_repository_attestation_transactions_session ON "
            "ci_coordinator.repository_attestation_transactions USING btree "
            "(session_handle_digest, expires_at)",
        ),
    }
)


async def repository_attestation_schema_matches_contract(
    connection: AsyncConnection,
) -> bool:
    return await connection.run_sync(repository_attestation_schema_matches_contract_sync)


def repository_attestation_schema_matches_contract_sync(connection: Connection) -> bool:
    return not repository_attestation_schema_mismatches_sync(connection)


def repository_attestation_schema_mismatches_sync(connection: Connection) -> tuple[str, ...]:
    columns = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT attribute.attname, "
                "format_type(attribute.atttypid, attribute.atttypmod), "
                "NOT attribute.attnotnull, "
                "pg_get_expr(default_value.adbin, default_value.adrelid), "
                "attribute.attstorage::text FROM pg_attribute AS attribute "
                "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "LEFT JOIN pg_attrdef AS default_value ON default_value.adrelid = relation.oid "
                "AND default_value.adnum = attribute.attnum "
                "WHERE namespace.nspname = :schema AND relation.relname = :relation "
                "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
                "ORDER BY attribute.attnum"
            ),
            {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
        )
    )
    constraints = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT constraint_metadata.conname, constraint_metadata.contype::text, "
                "pg_get_constraintdef(constraint_metadata.oid, true), "
                "constraint_metadata.condeferrable, constraint_metadata.condeferred, "
                "constraint_metadata.convalidated, constraint_metadata.connoinherit "
                "FROM pg_constraint AS constraint_metadata "
                "JOIN pg_class AS relation ON relation.oid = constraint_metadata.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname = :relation "
                "AND constraint_metadata.contype <> 'n'"
            ),
            {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
        )
    )
    indexes = frozenset(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT index_relation.relname, index_metadata.indisunique, "
                "index_metadata.indisprimary, index_metadata.indisvalid, "
                "index_metadata.indisready, index_metadata.indislive, "
                "pg_get_indexdef(index_metadata.indexrelid) FROM pg_index AS index_metadata "
                "JOIN pg_class AS relation ON relation.oid = index_metadata.indrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "JOIN pg_class AS index_relation ON index_relation.oid = index_metadata.indexrelid "
                "LEFT JOIN pg_constraint AS constraint_metadata "
                "ON constraint_metadata.conindid = index_metadata.indexrelid "
                "WHERE namespace.nspname = :schema AND relation.relname = :relation "
                "AND constraint_metadata.oid IS NULL"
            ),
            {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
        )
    )
    table = connection.execute(
        text(
            "SELECT relation.relkind, relation.relpersistence, relation.relrowsecurity, "
            "relation.relforcerowsecurity, relation.relowner = namespace.nspowner, "
            "row_type.typowner = namespace.nspowner FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "JOIN pg_type AS row_type ON row_type.oid = relation.reltype "
            "WHERE namespace.nspname = :schema AND relation.relname = :relation"
        ),
        {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
    ).one_or_none()
    unexpected = connection.execute(
        text(
            "SELECT (SELECT count(*) FROM pg_trigger AS trigger_metadata "
            "JOIN pg_class AS relation ON relation.oid = trigger_metadata.tgrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname = :relation "
            "AND NOT trigger_metadata.tgisinternal) + "
            "(SELECT count(*) FROM pg_policy AS policy_metadata "
            "WHERE policy_metadata.polrelid = to_regclass(:qualified_relation))"
        ),
        {
            "schema": APPLICATION_SCHEMA,
            "relation": _RELATION,
            "qualified_relation": f"{APPLICATION_SCHEMA}.{_RELATION}",
        },
    ).scalar_one()
    mismatches: list[str] = []
    if columns != _EXPECTED_COLUMNS:
        mismatches.append("columns")
    actual_constraint_names = {row[0] for row in constraints}
    expected_constraint_names = set(_EXPECTED_CONSTRAINT_DEFINITIONS)
    if actual_constraint_names != expected_constraint_names:
        mismatches.append("constraint-names")
    for row in constraints:
        expected_kind = (
            "p"
            if row[0].startswith("pk_")
            else "u"
            if row[0].startswith("uq_")
            else "f"
            if row[0].startswith("fk_")
            else "c"
        )
        expected_no_inherit = expected_kind in {"f", "p", "u"}
        if (
            row[0] not in _EXPECTED_CONSTRAINT_DEFINITIONS
            or row[1] != expected_kind
            or _EXPECTED_CONSTRAINT_DEFINITIONS.get(row[0]) != row[2]
            or row[3] is not False
            or row[4] is not False
            or row[5] is not True
            or row[6] is not expected_no_inherit
        ):
            mismatches.append(f"constraint:{row[0]}")
    if indexes != _EXPECTED_INDEXES:
        mismatches.append("indexes")
    if table != ("r", "p", False, False, True, True):
        mismatches.append("table")
    if unexpected != 0:
        mismatches.append("unexpected-objects")
    return tuple(mismatches)
