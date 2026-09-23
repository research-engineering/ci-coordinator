from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

type ColumnFact = tuple[str, str, str, bool, str | None]

_EXPECTED_COMPATIBILITY_COLUMNS: Final[tuple[ColumnFact, ...]] = (
    (
        "database_compatibility_capabilities",
        "revision_id",
        "character varying(32)",
        False,
        None,
    ),
    (
        "database_compatibility_capabilities",
        "capability_id",
        "character varying(128)",
        False,
        None,
    ),
    (
        "database_compatibility_capabilities",
        "descriptor_hash",
        "character varying(64)",
        False,
        None,
    ),
    ("database_compatibility_declarations", "generation", "bigint", False, None),
    (
        "database_compatibility_declarations",
        "revision_id",
        "character varying(32)",
        False,
        None,
    ),
    (
        "database_compatibility_declarations",
        "parent_revision_id",
        "character varying(32)",
        True,
        None,
    ),
    (
        "database_compatibility_declarations",
        "transition_kind",
        "character varying(16)",
        False,
        None,
    ),
    ("database_compatibility_declarations", "lineage_id", "character varying(128)", False, None),
    ("database_compatibility_declarations", "protocol_version", "smallint", False, None),
    (
        "database_compatibility_declarations",
        "declaration_hash",
        "character varying(64)",
        False,
        None,
    ),
)

_EXPECTED_COMPATIBILITY_CONSTRAINTS: Final[tuple[tuple[str, str, str, bool], ...]] = (
    ("database_compatibility_capabilities", "ck_database_capability_descriptor_hash", "c", True),
    ("database_compatibility_capabilities", "fk_database_capability_revision", "f", True),
    ("database_compatibility_capabilities", "pk_database_compatibility_capability", "p", True),
    (
        "database_compatibility_declarations",
        "ck_database_compatibility_declaration_hash",
        "c",
        True,
    ),
    (
        "database_compatibility_declarations",
        "ck_database_compatibility_generation_positive",
        "c",
        True,
    ),
    ("database_compatibility_declarations", "ck_database_compatibility_parent_shape", "c", True),
    (
        "database_compatibility_declarations",
        "ck_database_compatibility_transition_kind",
        "c",
        True,
    ),
    ("database_compatibility_declarations", "fk_database_compatibility_parent_revision", "f", True),
    ("database_compatibility_declarations", "pk_database_compatibility_declarations", "p", True),
    ("database_compatibility_declarations", "uq_database_compatibility_revision", "u", True),
)

_EXPECTED_COMPATIBILITY_TRIGGERS: Final[tuple[tuple[str, str, str, str, bool], ...]] = (
    (
        "database_compatibility_capabilities",
        "tr_database_compatibility_capabilities_immutable",
        "ci_coordinator",
        "reject_compatibility_mutation",
        False,
    ),
    (
        "database_compatibility_declarations",
        "tr_database_compatibility_declarations_immutable",
        "ci_coordinator",
        "reject_compatibility_mutation",
        False,
    ),
)


async def compatibility_protocol_schema_matches_contract(connection: AsyncConnection) -> bool:
    """Collect exact protocol relation facts without deciding their use."""
    return await connection.run_sync(compatibility_protocol_schema_matches_contract_sync)


def compatibility_protocol_schema_matches_contract_sync(connection: Connection) -> bool:
    columns = connection.execute(
        text(
            "SELECT relation.relname, attribute.attname, "
            "format_type(attribute.atttypid, attribute.atttypmod), "
            "NOT attribute.attnotnull, "
            "pg_get_expr(default_value.adbin, default_value.adrelid) "
            "FROM pg_attribute AS attribute "
            "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "LEFT JOIN pg_attrdef AS default_value ON default_value.adrelid = relation.oid "
            "AND default_value.adnum = attribute.attnum "
            "WHERE namespace.nspname = :schema "
            "AND relation.relname IN "
            "('database_compatibility_declarations', 'database_compatibility_capabilities') "
            "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
            "ORDER BY relation.relname, attribute.attnum"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    column_facts: tuple[ColumnFact, ...] = tuple(
        (row[0], row[1], row[2], row[3] is True, row[4]) for row in columns
    )
    constraints = connection.execute(
        text(
            "SELECT relation.relname, conname, contype, convalidated "
            "FROM pg_constraint "
            "JOIN pg_class AS relation ON relation.oid = conrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema "
            "AND relation.relname IN "
            "('database_compatibility_declarations', 'database_compatibility_capabilities') "
            "AND contype <> 'n' ORDER BY 1, 2"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    constraint_facts = tuple(tuple(row) for row in constraints)
    triggers = connection.execute(
        text(
            "SELECT relation.relname, trigger_metadata.tgname, function_namespace.nspname, "
            "function_metadata.proname, function_metadata.prosecdef "
            "FROM pg_trigger AS trigger_metadata "
            "JOIN pg_class AS relation ON relation.oid = trigger_metadata.tgrelid "
            "JOIN pg_namespace AS relation_namespace "
            "ON relation_namespace.oid = relation.relnamespace "
            "JOIN pg_proc AS function_metadata ON function_metadata.oid = trigger_metadata.tgfoid "
            "JOIN pg_namespace AS function_namespace "
            "ON function_namespace.oid = function_metadata.pronamespace "
            "WHERE relation_namespace.nspname = :schema "
            "AND relation.relname IN "
            "('database_compatibility_declarations', 'database_compatibility_capabilities') "
            "AND NOT trigger_metadata.tgisinternal ORDER BY 1, 2"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    trigger_facts = tuple(tuple(row) for row in triggers)
    return (
        column_facts == _EXPECTED_COMPATIBILITY_COLUMNS
        and constraint_facts == _EXPECTED_COMPATIBILITY_CONSTRAINTS
        and trigger_facts == _EXPECTED_COMPATIBILITY_TRIGGERS
    )
