from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

type ColumnFact = tuple[str, str, str, bool, str | None]
type ConstraintFact = tuple[str, str, str, str, bool, bool, bool]
type IndexFact = tuple[str, str, bool, bool, bool, bool, bool, str]
type StorageFact = tuple[str, str, str]
type TableFact = tuple[str, str, str, bool, bool]

_EXPECTED_COLUMNS: Final[tuple[ColumnFact, ...]] = (
    ("audit_events", "sequence", "bigint", False, None),
    ("audit_events", "installation_id", "bigint", True, None),
    ("audit_events", "repository_id", "bigint", True, None),
    ("audit_events", "idempotency_key", "bytea", False, None),
    ("audit_events", "idempotency_key_digest", "bytea", False, None),
    ("audit_events", "subject_type", "character varying(64)", False, None),
    ("audit_events", "subject_id", "bytea", False, None),
    ("audit_events", "event_type", "bytea", False, None),
    ("audit_events", "created_at", "character varying(24)", False, None),
    ("audit_events", "actor", "bytea", False, None),
    ("audit_events", "payload_canonical_json", "bytea", False, None),
    ("audit_events", "schema_version", "character varying(64)", False, None),
    ("audit_events", "audit_event_id", "character varying(38)", False, None),
    ("audit_events", "previous_event_hash", "bytea", True, None),
    ("audit_events", "payload_hash", "bytea", False, None),
    ("audit_events", "input_hash", "bytea", False, None),
    ("audit_events", "event_hash", "bytea", False, None),
    ("audit_ledger_head", "head_id", "smallint", False, None),
    ("audit_ledger_head", "revision", "bigint", False, None),
    ("audit_ledger_head", "last_sequence", "bigint", True, None),
    ("audit_ledger_head", "last_event_hash", "bytea", True, None),
)

_EXPECTED_CONSTRAINTS: Final[tuple[ConstraintFact, ...]] = (
    (
        "audit_events",
        "ck_audit_events_actor_byte_limit",
        "c",
        "CHECK (octet_length(actor) >= 1 AND octet_length(actor) <= 4096)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_event_type_byte_limit",
        "c",
        "CHECK (octet_length(event_type) >= 1 AND octet_length(event_type) <= 4096)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_hash_lengths",
        "c",
        (
            "CHECK (octet_length(payload_hash) = 32 AND octet_length(input_hash) = 32 "
            "AND octet_length(event_hash) = 32)"
        ),
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_idempotency_digest_length",
        "c",
        "CHECK (octet_length(idempotency_key_digest) = 32)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_idempotency_key_byte_limit",
        "c",
        "CHECK (octet_length(idempotency_key) >= 1 AND octet_length(idempotency_key) <= 4096)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_payload_canonical_json_byte_limit",
        "c",
        (
            "CHECK (octet_length(payload_canonical_json) >= 1 AND "
            "octet_length(payload_canonical_json) <= 1048576)"
        ),
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_predecessor_shape",
        "c",
        (
            "CHECK (sequence = 1 AND previous_event_hash IS NULL OR "
            "sequence > 1 AND previous_event_hash IS NOT NULL)"
        ),
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_previous_hash_length",
        "c",
        "CHECK (previous_event_hash IS NULL OR octet_length(previous_event_hash) = 32)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_schema_version",
        "c",
        "CHECK (schema_version::text = 'ci-audit-event/v1'::text)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_scope_shape",
        "c",
        (
            "CHECK (installation_id IS NULL AND repository_id IS NULL OR "
            "installation_id >= 1 AND installation_id <= '9007199254740991'::bigint AND "
            "repository_id >= 1 AND repository_id <= '9007199254740991'::bigint)"
        ),
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_sequence_safe",
        "c",
        "CHECK (sequence >= 1 AND sequence <= '9007199254740991'::bigint)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_subject_id_byte_limit",
        "c",
        "CHECK (octet_length(subject_id) >= 1 AND octet_length(subject_id) <= 4096)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "ck_audit_events_subject_type",
        "c",
        (
            "CHECK (subject_type::text = ANY (ARRAY['webhook-delivery'::character varying, "
            "'observation'::character varying, 'policy-decision'::character varying, "
            "'reconciliation-state'::character varying, 'coordinator-check'::character varying, "
            "'dynamic-ci-plan'::character varying, 'release-evidence'::character varying, "
            "'policy-drift'::character varying]::text[]))"
        ),
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "fk_audit_events_previous_event_hash",
        "f",
        "FOREIGN KEY (previous_event_hash) REFERENCES ci_coordinator.audit_events(event_hash)",
        False,
        False,
        True,
    ),
    ("audit_events", "pk_audit_events", "p", "PRIMARY KEY (sequence)", False, False, True),
    (
        "audit_events",
        "uq_audit_events_audit_event_id",
        "u",
        "UNIQUE (audit_event_id)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "uq_audit_events_event_hash",
        "u",
        "UNIQUE (event_hash)",
        False,
        False,
        True,
    ),
    (
        "audit_events",
        "uq_audit_events_idempotency_digest",
        "u",
        "UNIQUE (idempotency_key_digest)",
        False,
        False,
        True,
    ),
    (
        "audit_ledger_head",
        "ck_audit_ledger_head_hash_length",
        "c",
        "CHECK (last_event_hash IS NULL OR octet_length(last_event_hash) = 32)",
        False,
        False,
        True,
    ),
    (
        "audit_ledger_head",
        "ck_audit_ledger_head_revision_safe",
        "c",
        "CHECK (revision >= 0 AND revision <= '9007199254740991'::bigint)",
        False,
        False,
        True,
    ),
    (
        "audit_ledger_head",
        "ck_audit_ledger_head_singleton",
        "c",
        "CHECK (head_id = 1)",
        False,
        False,
        True,
    ),
    (
        "audit_ledger_head",
        "ck_audit_ledger_head_state",
        "c",
        (
            "CHECK (revision = 0 AND last_sequence IS NULL AND last_event_hash IS NULL OR "
            "revision >= 1 AND revision = last_sequence AND last_event_hash IS NOT NULL)"
        ),
        False,
        False,
        True,
    ),
    (
        "audit_ledger_head",
        "fk_audit_ledger_head_last_event_hash",
        "f",
        "FOREIGN KEY (last_event_hash) REFERENCES ci_coordinator.audit_events(event_hash)",
        False,
        False,
        True,
    ),
    (
        "audit_ledger_head",
        "pk_audit_ledger_head",
        "p",
        "PRIMARY KEY (head_id)",
        False,
        False,
        True,
    ),
)

_EXPECTED_INDEXES: Final[tuple[IndexFact, ...]] = (
    (
        "audit_events",
        "ix_audit_events_scope_sequence",
        False,
        False,
        True,
        True,
        True,
        (
            "CREATE INDEX ix_audit_events_scope_sequence ON ci_coordinator.audit_events "
            "USING btree (installation_id, repository_id, sequence)"
        ),
    ),
)

_EXPECTED_TABLES: Final[tuple[TableFact, ...]] = (
    ("audit_events", "r", "p", False, False),
    ("audit_ledger_head", "r", "p", False, False),
)

_EXPECTED_STORAGE: Final[tuple[StorageFact, ...]] = (
    ("audit_events", "idempotency_key", "x"),
    ("audit_events", "subject_id", "x"),
    ("audit_events", "event_type", "x"),
    ("audit_events", "actor", "x"),
    ("audit_events", "payload_canonical_json", "x"),
)


async def schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(schema_matches_contract_sync)


def schema_matches_contract_sync(connection: Connection) -> bool:
    columns = connection.execute(
        text(
            "SELECT relation.relname, attribute.attname, "
            "format_type(attribute.atttypid, attribute.atttypmod), "
            "NOT attribute.attnotnull, "
            "pg_get_expr(default_value.adbin, default_value.adrelid) "
            "FROM pg_attribute AS attribute "
            "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "LEFT JOIN pg_attrdef AS default_value "
            "ON default_value.adrelid = relation.oid "
            "AND default_value.adnum = attribute.attnum "
            "WHERE namespace.nspname = :schema "
            "AND relation.relname IN ('audit_events', 'audit_ledger_head') "
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
            "SELECT relation.relname, conname, contype, "
            "pg_get_constraintdef(pg_constraint.oid, true), condeferrable, condeferred, "
            "convalidated "
            "FROM pg_constraint "
            "JOIN pg_class AS relation ON relation.oid = conrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema "
            "AND relation.relname IN ('audit_events', 'audit_ledger_head') "
            "AND contype <> 'n' "
            "ORDER BY 1, 2"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    constraint_facts: tuple[ConstraintFact, ...] = tuple(
        (row[0], row[1], row[2], row[3], row[4], row[5], row[6]) for row in constraints
    )
    indexes = connection.execute(
        text(
            "SELECT table_metadata.relname, index_relation.relname, "
            "index_metadata.indisunique, index_metadata.indisprimary, "
            "index_metadata.indisvalid, index_metadata.indisready, "
            "index_metadata.indislive, pg_get_indexdef(index_relation.oid, 0, false) "
            "FROM pg_index AS index_metadata "
            "JOIN pg_class AS index_relation ON index_relation.oid = index_metadata.indexrelid "
            "JOIN pg_class AS table_metadata ON table_metadata.oid = index_metadata.indrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = index_relation.relnamespace "
            "LEFT JOIN pg_constraint AS constraint_metadata "
            "ON constraint_metadata.conindid = index_metadata.indexrelid "
            "WHERE namespace.nspname = :schema "
            "AND table_metadata.relname IN ('audit_events', 'audit_ledger_head') "
            "AND constraint_metadata.oid IS NULL "
            "ORDER BY table_metadata.relname, index_relation.relname"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    index_facts: tuple[IndexFact, ...] = tuple(
        (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]) for row in indexes
    )
    tables = connection.execute(
        text(
            "SELECT relation.relname, relation.relkind, relation.relpersistence, "
            "relation.relrowsecurity, relation.relforcerowsecurity "
            "FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema "
            "AND relation.relname IN ('audit_events', 'audit_ledger_head') "
            "ORDER BY relation.relname"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    table_facts: tuple[TableFact, ...] = tuple(
        (row[0], row[1], row[2], row[3], row[4]) for row in tables
    )
    storage = connection.execute(
        text(
            "SELECT relation.relname, attribute.attname, attribute.attstorage "
            "FROM pg_attribute AS attribute "
            "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema "
            "AND relation.relname = 'audit_events' "
            "AND attribute.attname IN "
            "('idempotency_key', 'subject_id', 'event_type', 'actor', "
            "'payload_canonical_json') "
            "ORDER BY attribute.attnum"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    storage_facts: tuple[StorageFact, ...] = tuple((row[0], row[1], row[2]) for row in storage)
    toast_fact = connection.execute(
        text(
            "SELECT toast.relkind "
            "FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "LEFT JOIN pg_class AS toast ON toast.oid = relation.reltoastrelid "
            "WHERE namespace.nspname = :schema "
            "AND relation.relname = 'audit_events'"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    unexpected_objects = connection.scalar(
        text(
            "SELECT ("
            "SELECT count(*) FROM pg_constraint AS constraint_metadata "
            "WHERE constraint_metadata.conrelid IN "
            "(to_regclass(:audit_events), to_regclass(:audit_ledger_head)) "
            "AND constraint_metadata.contype <> 'n' "
            "AND NOT constraint_metadata.conenforced"
            ") + ("
            "SELECT count(*) FROM pg_trigger AS trigger_metadata "
            "WHERE trigger_metadata.tgrelid IN "
            "(to_regclass(:audit_events), to_regclass(:audit_ledger_head)) "
            "AND NOT trigger_metadata.tgisinternal"
            ") + ("
            "SELECT count(*) FROM pg_policy AS policy_metadata "
            "WHERE policy_metadata.polrelid IN "
            "(to_regclass(:audit_events), to_regclass(:audit_ledger_head))"
            ")"
        ),
        {
            "schema": APPLICATION_SCHEMA,
            "audit_events": f"{APPLICATION_SCHEMA}.audit_events",
            "audit_ledger_head": f"{APPLICATION_SCHEMA}.audit_ledger_head",
        },
    )
    return (
        column_facts == _EXPECTED_COLUMNS
        and constraint_facts == _EXPECTED_CONSTRAINTS
        and index_facts == _EXPECTED_INDEXES
        and table_facts == _EXPECTED_TABLES
        and storage_facts == _EXPECTED_STORAGE
        and toast_fact.scalar_one_or_none() == "t"
        and unexpected_objects == 0
    )
