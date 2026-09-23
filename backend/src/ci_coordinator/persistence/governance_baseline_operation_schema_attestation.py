"""Catalog fact collection for governance-baseline operation receipts."""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ci_coordinator.governance_baseline import MAX_BASELINE_COMMAND_BYTES
from ci_coordinator.persistence.catalog_statements import (
    OWNED_RELATION_SQL,
    RELATION_COLUMNS_SQL,
    RELATION_CONSTRAINTS_SQL,
    RELATION_TRIGGERS_SQL,
)
from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

_RELATION: Final = "governance_baseline_operations"
_EXPECTED_COLUMNS: Final = (
    ("installation_id", "bigint", False, None),
    ("repository_id", "bigint", False, None),
    ("operation_id", "character varying(256)", False, None),
    ("command_canonical_json", "bytea", False, None),
    ("result_kind", "character varying(9)", False, None),
    ("result_baseline_id", "character varying(84)", False, None),
    ("result_version", "bigint", False, None),
    ("recorded_at", "timestamp with time zone", False, None),
)
_EXPECTED_CONSTRAINTS: Final = frozenset(
    {
        ("ck_governance_baseline_operations_command_bytes", "c"),
        ("ck_governance_baseline_operations_installation_id_safe", "c"),
        ("ck_governance_baseline_operations_operation_id_limit", "c"),
        ("ck_governance_baseline_operations_repository_id_safe", "c"),
        ("ck_governance_baseline_operations_result", "c"),
        ("fk_governance_baseline_operations_result", "f"),
        ("pk_governance_baseline_operations", "p"),
    }
)
_EXPECTED_CONSTRAINT_DEFINITIONS: Final = {
    "ck_governance_baseline_operations_command_bytes": (
        f"CHECK (octet_length(command_canonical_json) >= 1 "
        f"AND octet_length(command_canonical_json) <= {MAX_BASELINE_COMMAND_BYTES})"
    ),
    "ck_governance_baseline_operations_installation_id_safe": (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint)"
    ),
    "ck_governance_baseline_operations_operation_id_limit": (
        "CHECK (octet_length(operation_id::text) >= 1 AND octet_length(operation_id::text) <= 256)"
    ),
    "ck_governance_baseline_operations_repository_id_safe": (
        "CHECK (repository_id >= 1 AND repository_id <= '9007199254740991'::bigint)"
    ),
    "ck_governance_baseline_operations_result": (
        "CHECK ((result_kind::text = ANY (ARRAY['accepted'::character varying, "
        "'unchanged'::character varying]::text[])) AND result_baseline_id::text "
        "~ '^governance-baseline:[0-9a-f]{64}$'::text AND result_version >= 1 "
        "AND result_version <= '9007199254740991'::bigint)"
    ),
    "fk_governance_baseline_operations_result": (
        "FOREIGN KEY (installation_id, repository_id, result_version, "
        "result_baseline_id) REFERENCES ci_coordinator.governance_baselines"
        "(installation_id, repository_id, version, baseline_id) ON DELETE RESTRICT"
    ),
    "pk_governance_baseline_operations": (
        "PRIMARY KEY (installation_id, repository_id, operation_id)"
    ),
}


def governance_baseline_operation_schema_matches_contract_sync(
    connection: Connection,
) -> bool:
    columns = tuple(
        connection.execute(
            text(RELATION_COLUMNS_SQL),
            {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
        )
    )
    constraints = tuple(
        connection.execute(
            text(RELATION_CONSTRAINTS_SQL),
            {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
        )
    )
    table = connection.execute(
        text(OWNED_RELATION_SQL),
        {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
    ).one_or_none()
    storage = connection.execute(
        text(
            "SELECT attribute.attstorage FROM pg_attribute AS attribute "
            "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname = :relation "
            "AND attribute.attname = 'command_canonical_json'"
        ),
        {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
    ).scalar_one_or_none()
    trigger = connection.execute(
        text(RELATION_TRIGGERS_SQL),
        {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
    ).one_or_none()
    unexpected = connection.execute(
        text(
            "SELECT (SELECT count(*) FROM pg_index AS index_metadata "
            "JOIN pg_class AS relation ON relation.oid = index_metadata.indrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "LEFT JOIN pg_constraint AS constraint_metadata "
            "ON constraint_metadata.conindid = index_metadata.indexrelid "
            "WHERE namespace.nspname = :schema AND relation.relname = :relation "
            "AND constraint_metadata.oid IS NULL) + "
            "(SELECT count(*) FROM pg_policy AS policy_metadata "
            "WHERE policy_metadata.polrelid = to_regclass(:qualified_relation))"
        ),
        {
            "schema": APPLICATION_SCHEMA,
            "relation": _RELATION,
            "qualified_relation": f"{APPLICATION_SCHEMA}.{_RELATION}",
        },
    ).scalar_one()
    return (
        tuple(tuple(row) for row in columns) == _EXPECTED_COLUMNS
        and frozenset((row[0], row[1]) for row in constraints) == _EXPECTED_CONSTRAINTS
        and all(
            _EXPECTED_CONSTRAINT_DEFINITIONS.get(row[0]) == row[2]
            and row[3] is False
            and row[4] is False
            and row[5] is True
            for row in constraints
        )
        and table == ("r", "p", False, False, True, True)
        and storage == "x"
        and trigger
        == (
            "tr_governance_baseline_operations_immutable",
            "reject_governance_baseline_mutation",
            False,
            "O",
            27,
            True,
        )
        and unexpected == 0
    )
