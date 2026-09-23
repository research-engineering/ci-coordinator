"""Catalog fact collection for governance-baseline storage."""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.governance_observation import MAX_GOVERNANCE_STATE_BYTES
from ci_coordinator.persistence.catalog_statements import (
    OWNED_RELATION_SQL,
    RELATION_COLUMNS_SQL,
    RELATION_CONSTRAINTS_SQL,
    RELATION_TRIGGERS_SQL,
)
from ci_coordinator.persistence.governance_baseline_operation_schema_attestation import (
    governance_baseline_operation_schema_matches_contract_sync,
)
from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

_RELATION: Final = "governance_baselines"
_EXPECTED_COLUMNS: Final = (
    ("installation_id", "bigint", False, None),
    ("repository_id", "bigint", False, None),
    ("version", "bigint", False, None),
    ("baseline_id", "character varying(84)", False, None),
    ("operation_id", "character varying(256)", False, None),
    ("state_digest", "character varying(64)", False, None),
    ("state_canonical_json", "bytea", False, None),
    ("observed_at", "timestamp with time zone", False, None),
    ("approved_at", "timestamp with time zone", False, None),
    ("actor", "character varying(256)", False, None),
    ("reason", "character varying(1024)", False, None),
    ("supersedes_baseline_id", "character varying(84)", True, None),
    ("supersedes_version", "bigint", True, None),
    ("supersedes_state_digest", "character varying(64)", True, None),
    ("audit_event_id", "character varying(38)", False, None),
    ("audit_input_hash", "bytea", False, None),
)
_EXPECTED_CONSTRAINTS: Final = frozenset(
    {
        ("ck_governance_baselines_approval_text", "c"),
        ("ck_governance_baselines_audit_identity", "c"),
        ("ck_governance_baselines_chain_shape", "c"),
        ("ck_governance_baselines_identity", "c"),
        ("ck_governance_baselines_installation_id_safe", "c"),
        ("ck_governance_baselines_operation_id_limit", "c"),
        ("ck_governance_baselines_repository_id_safe", "c"),
        ("ck_governance_baselines_state_bytes", "c"),
        ("ck_governance_baselines_time_order", "c"),
        ("fk_governance_baselines_audit_event", "f"),
        ("fk_governance_baselines_predecessor", "f"),
        ("pk_governance_baselines", "p"),
        ("uq_governance_baselines_audit_event", "u"),
        ("uq_governance_baselines_baseline_id", "u"),
        ("uq_governance_baselines_scope_operation", "u"),
        ("uq_governance_baselines_scope_version_identity", "u"),
    }
)
_EXPECTED_CONSTRAINT_DEFINITIONS: Final = {
    "ck_governance_baselines_approval_text": (
        "CHECK (octet_length(actor::text) >= 1 AND octet_length(actor::text) <= 256 "
        "AND octet_length(reason::text) >= 1 AND octet_length(reason::text) <= 1024 "
        "AND reason::text = btrim(reason::text))"
    ),
    "ck_governance_baselines_audit_identity": (
        "CHECK (audit_event_id::text ~ '^audit_[0-9a-f]{32}$'::text "
        "AND octet_length(audit_input_hash) = 32)"
    ),
    "ck_governance_baselines_chain_shape": (
        "CHECK (version = 1 AND supersedes_baseline_id IS NULL "
        "AND supersedes_version IS NULL AND supersedes_state_digest IS NULL "
        "OR version >= 2 AND version <= '9007199254740991'::bigint "
        "AND supersedes_baseline_id::text "
        "~ '^governance-baseline:[0-9a-f]{64}$'::text "
        "AND supersedes_version = (version - 1) "
        "AND supersedes_state_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    "ck_governance_baselines_identity": (
        "CHECK (baseline_id::text ~ '^governance-baseline:[0-9a-f]{64}$'::text "
        "AND state_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    "ck_governance_baselines_installation_id_safe": (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint)"
    ),
    "ck_governance_baselines_operation_id_limit": (
        "CHECK (octet_length(operation_id::text) >= 1 AND octet_length(operation_id::text) <= 256)"
    ),
    "ck_governance_baselines_repository_id_safe": (
        "CHECK (repository_id >= 1 AND repository_id <= '9007199254740991'::bigint)"
    ),
    "ck_governance_baselines_state_bytes": (
        f"CHECK (octet_length(state_canonical_json) >= 1 "
        f"AND octet_length(state_canonical_json) <= {MAX_GOVERNANCE_STATE_BYTES})"
    ),
    "ck_governance_baselines_time_order": "CHECK (observed_at <= approved_at)",
    "fk_governance_baselines_audit_event": (
        "FOREIGN KEY (audit_event_id) REFERENCES "
        "ci_coordinator.audit_events(audit_event_id) ON DELETE RESTRICT"
    ),
    "fk_governance_baselines_predecessor": (
        "FOREIGN KEY (installation_id, repository_id, supersedes_version, "
        "supersedes_baseline_id) REFERENCES ci_coordinator.governance_baselines"
        "(installation_id, repository_id, version, baseline_id) ON DELETE RESTRICT"
    ),
    "pk_governance_baselines": ("PRIMARY KEY (installation_id, repository_id, version)"),
    "uq_governance_baselines_audit_event": "UNIQUE (audit_event_id)",
    "uq_governance_baselines_baseline_id": "UNIQUE (baseline_id)",
    "uq_governance_baselines_scope_operation": (
        "UNIQUE (installation_id, repository_id, operation_id)"
    ),
    "uq_governance_baselines_scope_version_identity": (
        "UNIQUE (installation_id, repository_id, version, baseline_id)"
    ),
}
_EXPECTED_ROUTINE: Final = (
    "reject_governance_baseline_mutation",
    "",
    "trigger",
    "plpgsql",
    False,
    "v",
    False,
    False,
    (
        " BEGIN RAISE EXCEPTION 'governance baseline history is immutable' "
        "USING ERRCODE = '42501'; END; "
    ),
    True,
)


async def governance_baseline_schema_matches_contract(
    connection: AsyncConnection,
) -> bool:
    return await connection.run_sync(governance_baseline_schema_matches_contract_sync)


def _governance_baseline_relation_matches_contract_sync(connection: Connection) -> bool:
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
            "AND attribute.attname = 'state_canonical_json'"
        ),
        {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
    ).scalar_one_or_none()
    trigger = connection.execute(
        text(RELATION_TRIGGERS_SQL),
        {"schema": APPLICATION_SCHEMA, "relation": _RELATION},
    ).one_or_none()
    routine = connection.execute(
        text(
            "SELECT routine.proname, pg_get_function_identity_arguments(routine.oid), "
            "pg_get_function_result(routine.oid), language.lanname, routine.prosecdef, "
            "routine.provolatile, routine.proisstrict, routine.proleakproof, routine.prosrc, "
            "routine.proowner = namespace.nspowner FROM pg_proc AS routine "
            "JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
            "JOIN pg_language AS language ON language.oid = routine.prolang "
            "WHERE namespace.nspname = :schema "
            "AND routine.proname = 'reject_governance_baseline_mutation'"
        ),
        {"schema": APPLICATION_SCHEMA},
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
            "tr_governance_baselines_immutable",
            "reject_governance_baseline_mutation",
            False,
            "O",
            27,
            True,
        )
        and routine == _EXPECTED_ROUTINE
        and unexpected == 0
    )


def governance_baseline_schema_matches_contract_sync(connection: Connection) -> bool:
    return _governance_baseline_relation_matches_contract_sync(
        connection
    ) and governance_baseline_operation_schema_matches_contract_sync(connection)
