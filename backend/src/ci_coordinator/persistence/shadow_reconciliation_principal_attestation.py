"""Exact column-grant admission for the additive shadow/reconciliation capability."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

_RELATION_COLUMNS = {
    "shadow_evidence": (
        "profile_id",
        "repository",
        "event",
        "surface",
        "record_canonical_json",
        "semantic_hash",
    ),
    "reconciliation_subjects": (
        "subject_id",
        "installation_id",
        "repository_id",
        "subject_canonical_json",
        "contract_canonical_json",
        "identity_hash",
        "contract_hash",
        "revision",
        "created_at",
        "deadline_at",
        "next_attempt_at",
        "attempt_count",
        "max_attempts",
        "backoff_seconds",
        "max_backoff_seconds",
        "claim_generation",
        "lease_token",
        "lease_acquired_at",
        "lease_expires_at",
    ),
    "reconciliation_observations": (
        "subject_id",
        "observation_id",
        "revision",
        "observation_canonical_json",
        "semantic_hash",
    ),
    "reconciliation_results": (
        "subject_id",
        "revision",
        "result_canonical_json",
        "semantic_hash",
    ),
}
_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
_COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")


async def shadow_reconciliation_runtime_principal_is_restricted(
    connection: AsyncConnection,
) -> bool:
    """Require exact column grants while preserving predecessor table-grant admission."""
    return await _principal_matches(connection, successor=False)


async def shadow_reconciliation_v2_runtime_principal_is_restricted(
    connection: AsyncConnection,
) -> bool:
    return await _principal_matches(connection, successor=True)


async def _principal_matches(connection: AsyncConnection, *, successor: bool) -> bool:
    relation_names = tuple(_RELATION_COLUMNS)
    table_facts = tuple(
        await connection.execute(
            text(
                "SELECT relation.relname, privilege.name, "
                "has_table_privilege(relation.oid, privilege.name) "
                "FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "CROSS JOIN unnest(CAST(:privileges AS text[])) AS privilege(name) "
                "WHERE namespace.nspname = :schema AND relation.relkind = 'r' "
                "AND relation.relname = ANY(:relations) "
                "ORDER BY relation.relname, privilege.name"
            ),
            {
                "schema": APPLICATION_SCHEMA,
                "relations": list(relation_names),
                "privileges": list(_TABLE_PRIVILEGES),
            },
        )
    )
    column_facts = tuple(
        await connection.execute(
            text(
                "SELECT relation.relname, attribute.attname, privilege.name, "
                "has_column_privilege(relation.oid, attribute.attnum, privilege.name) "
                "FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "JOIN pg_attribute AS attribute ON attribute.attrelid = relation.oid "
                "CROSS JOIN unnest(CAST(:privileges AS text[])) AS privilege(name) "
                "WHERE namespace.nspname = :schema AND relation.relkind = 'r' "
                "AND relation.relname = ANY(:relations) "
                "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
                "ORDER BY relation.relname, attribute.attnum, privilege.name"
            ),
            {
                "schema": APPLICATION_SCHEMA,
                "relations": list(relation_names),
                "privileges": list(_COLUMN_PRIVILEGES),
            },
        )
    )
    has_grant_option = await connection.scalar(
        text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_attribute AS attribute "
            "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "JOIN pg_roles AS principal ON principal.rolname = session_user "
            "CROSS JOIN LATERAL aclexplode(COALESCE(attribute.attacl, '{}'::aclitem[])) "
            "AS privilege "
            "WHERE namespace.nspname = :schema AND relation.relkind = 'r' "
            "AND relation.relname = ANY(:relations) "
            "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
            "AND privilege.grantee = principal.oid AND privilege.is_grantable"
            ")"
        ),
        {"schema": APPLICATION_SCHEMA, "relations": list(relation_names)},
    )
    return (
        _table_facts_are_exact(table_facts)
        and _column_facts_are_exact(column_facts, successor=successor)
        and has_grant_option is False
    )


def _table_facts_are_exact(rows: tuple[Sequence[object], ...]) -> bool:
    if any(len(row) != 3 for row in rows):
        return False
    expected = {
        (relation, privilege) for relation in _RELATION_COLUMNS for privilege in _TABLE_PRIVILEGES
    }
    facts = {(row[0], row[1]): row[2] for row in rows}
    return set(facts) == expected and all(value is False for value in facts.values())


def _column_facts_are_exact(rows: tuple[Sequence[object], ...], *, successor: bool = False) -> bool:
    if any(len(row) != 4 for row in rows):
        return False
    mutable_subject_columns = {
        "revision",
        "next_attempt_at",
        "attempt_count",
        "backoff_seconds",
        "claim_generation",
        "lease_token",
        "lease_acquired_at",
        "lease_expires_at",
    }
    relation_columns = dict(_RELATION_COLUMNS)
    if successor:
        relation_columns["reconciliation_subjects"] += (
            "execution_origin",
            "production_generation",
            "production_authority_id",
        )
    expected = {
        (relation, column, privilege): (
            privilege in {"SELECT", "INSERT"}
            or (
                relation == "reconciliation_subjects"
                and column in mutable_subject_columns
                and privilege == "UPDATE"
            )
        )
        for relation, columns in relation_columns.items()
        for column in columns
        for privilege in _COLUMN_PRIVILEGES
    }
    facts = {(row[0], row[1], row[2]): row[3] for row in rows}
    return facts == expected
