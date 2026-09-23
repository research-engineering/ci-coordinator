"""Exact catalog facts for operation-aware config epoch registration."""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.catalog_statements import (
    RELATION_COLUMNS_SQL,
    RELATION_CONSTRAINTS_SQL,
    RELATION_TRIGGERS_SQL,
)
from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

_RELATION: Final = "config_epoch_registrations"
_EXPECTED_COLUMNS: Final = (
    ("installation_id", "bigint", False, None),
    ("repository_id", "bigint", False, None),
    ("operation_id", "character varying(256)", False, None),
    ("epoch_id", "character varying(64)", False, None),
    ("audit_event_id", "character varying(38)", False, None),
    ("audit_input_hash", "bytea", False, None),
)
_EXPECTED_CONSTRAINTS: Final = {
    "ck_config_epoch_registrations_audit_identity": (
        "CHECK (audit_event_id::text ~ '^audit_[0-9a-f]{32}$'::text AND "
        "octet_length(audit_input_hash) = 32)"
    ),
    "ck_config_epoch_registrations_epoch_id_hash": (
        "CHECK (epoch_id::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    "ck_config_epoch_registrations_installation_id_safe": (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint)"
    ),
    "ck_config_epoch_registrations_operation_id_limit": (
        "CHECK (octet_length(operation_id::text) >= 1 AND octet_length(operation_id::text) <= 256)"
    ),
    "ck_config_epoch_registrations_repository_id_safe": (
        "CHECK (repository_id >= 1 AND repository_id <= '9007199254740991'::bigint)"
    ),
    "fk_config_epoch_registrations_audit_event": (
        "FOREIGN KEY (audit_event_id) REFERENCES ci_coordinator.audit_events(audit_event_id) "
        "ON DELETE RESTRICT"
    ),
    "fk_config_epoch_registrations_epoch": (
        "FOREIGN KEY (installation_id, repository_id, epoch_id) REFERENCES "
        "ci_coordinator.config_epochs(installation_id, repository_id, epoch_id) "
        "ON DELETE RESTRICT"
    ),
    "pk_config_epoch_registrations": ("PRIMARY KEY (installation_id, repository_id, operation_id)"),
    "uq_config_epoch_registrations_audit_event": "UNIQUE (audit_event_id)",
}


async def config_epoch_registration_schema_matches_contract(
    connection: AsyncConnection,
) -> bool:
    return await connection.run_sync(config_epoch_registration_schema_matches_contract_sync)


def config_epoch_registration_schema_matches_contract_sync(connection: Connection) -> bool:
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
        text(
            "SELECT relation.relkind, relation.relpersistence, relation.relrowsecurity, "
            "relation.relforcerowsecurity FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname = :relation"
        ),
        {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
    ).one_or_none()
    triggers = tuple(
        connection.execute(
            text(RELATION_TRIGGERS_SQL),
            {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
        )
    )
    audit_storage = connection.scalar(
        text(
            "SELECT attribute.attstorage FROM pg_attribute AS attribute "
            "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname = :relation "
            "AND attribute.attname = 'audit_input_hash'"
        ),
        {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
    )
    unexpected_objects = connection.scalar(
        text(
            "SELECT (SELECT count(*) FROM pg_index AS index_metadata "
            "JOIN pg_class AS relation ON relation.oid = index_metadata.indrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "LEFT JOIN pg_constraint AS constraint_metadata "
            "ON constraint_metadata.conindid = index_metadata.indexrelid "
            "WHERE namespace.nspname = :schema AND relation.relname = :relation "
            "AND constraint_metadata.oid IS NULL) + "
            "(SELECT count(*) FROM pg_policy WHERE polrelid = to_regclass(:qualified))"
        ),
        {
            "schema": APPLICATION_SCHEMA,
            "relation": _RELATION,
            "qualified": f"{APPLICATION_SCHEMA}.{_RELATION}",
        },
    )
    observed_columns = tuple((row[0], row[1], row[2] is True, row[3]) for row in columns)
    observed_constraints = {row[0]: row[2] for row in constraints}
    observed_table = None if table is None else tuple(table)
    observed_triggers = tuple(tuple(row) for row in triggers)
    return (
        observed_columns == _EXPECTED_COLUMNS
        and len(constraints) == len(_EXPECTED_CONSTRAINTS)
        and observed_constraints == _EXPECTED_CONSTRAINTS
        and all(
            row[1] in {"c", "f", "p", "u"}
            and row[3] is False
            and row[4] is False
            and row[5] is True
            for row in constraints
        )
        and observed_table == ("r", "p", False, False)
        and observed_triggers
        == (
            (
                "tr_config_epoch_registrations_immutable",
                "reject_config_epoch_mutation",
                False,
                "O",
                27,
                True,
            ),
        )
        and audit_storage == "x"
        and unexpected_objects == 0
    )
