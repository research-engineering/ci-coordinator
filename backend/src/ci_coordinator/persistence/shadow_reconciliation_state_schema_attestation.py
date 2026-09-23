"""Catalog facts for the immutable shadow-evidence and reconciliation-CAS capability."""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

type ColumnFact = tuple[str, str, str, bool]
type ConstraintFact = tuple[str, str, str, str, bool, bool, bool]
type IndexFact = tuple[str, str, bool, bool, bool, bool, bool, str]
type TableFact = tuple[str, str, str, bool, bool]

_RELATIONS: Final = (
    "reconciliation_observations",
    "reconciliation_results",
    "reconciliation_subjects",
    "shadow_evidence",
)
_EXPECTED_COLUMNS: Final[tuple[ColumnFact, ...]] = (
    ("reconciliation_observations", "subject_id", "character varying(64)", False),
    ("reconciliation_observations", "observation_id", "character varying(1024)", False),
    ("reconciliation_observations", "revision", "bigint", False),
    ("reconciliation_observations", "observation_canonical_json", "bytea", False),
    ("reconciliation_observations", "semantic_hash", "character varying(64)", False),
    ("reconciliation_results", "subject_id", "character varying(64)", False),
    ("reconciliation_results", "revision", "bigint", False),
    ("reconciliation_results", "result_canonical_json", "bytea", False),
    ("reconciliation_results", "semantic_hash", "character varying(64)", False),
    ("reconciliation_subjects", "subject_id", "character varying(64)", False),
    ("reconciliation_subjects", "installation_id", "bigint", False),
    ("reconciliation_subjects", "repository_id", "bigint", False),
    ("reconciliation_subjects", "subject_canonical_json", "bytea", False),
    ("reconciliation_subjects", "contract_canonical_json", "bytea", False),
    ("reconciliation_subjects", "identity_hash", "character varying(64)", False),
    ("reconciliation_subjects", "contract_hash", "character varying(64)", False),
    ("reconciliation_subjects", "revision", "bigint", False),
    ("reconciliation_subjects", "created_at", "timestamp with time zone", False),
    ("reconciliation_subjects", "deadline_at", "timestamp with time zone", False),
    ("reconciliation_subjects", "next_attempt_at", "timestamp with time zone", False),
    ("reconciliation_subjects", "attempt_count", "bigint", False),
    ("reconciliation_subjects", "max_attempts", "bigint", False),
    ("reconciliation_subjects", "backoff_seconds", "bigint", False),
    ("reconciliation_subjects", "max_backoff_seconds", "bigint", False),
    ("reconciliation_subjects", "claim_generation", "bigint", False),
    ("reconciliation_subjects", "lease_token", "character varying(64)", True),
    ("reconciliation_subjects", "lease_acquired_at", "timestamp with time zone", True),
    ("reconciliation_subjects", "lease_expires_at", "timestamp with time zone", True),
    ("shadow_evidence", "profile_id", "character varying(256)", False),
    ("shadow_evidence", "repository", "character varying(1024)", False),
    ("shadow_evidence", "event", "character varying(1024)", False),
    ("shadow_evidence", "surface", "character varying(1024)", False),
    ("shadow_evidence", "record_canonical_json", "bytea", False),
    ("shadow_evidence", "semantic_hash", "character varying(64)", False),
)
_EXPECTED_CONSTRAINTS: Final[tuple[ConstraintFact, ...]] = (
    (
        "reconciliation_observations",
        "ck_reconciliation_observations_canonical_byte_limit",
        "c",
        (
            "CHECK (octet_length(observation_canonical_json) >= 1 AND "
            "octet_length(observation_canonical_json) <= 65536)"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_observations",
        "ck_reconciliation_observations_identity_limits",
        "c",
        (
            "CHECK (subject_id::text ~ '^[0-9a-f]{64}$'::text AND "
            "octet_length(observation_id::text) >= 1 AND "
            "octet_length(observation_id::text) <= 1024)"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_observations",
        "ck_reconciliation_observations_revision_safe",
        "c",
        "CHECK (revision >= 1 AND revision <= '9007199254740991'::bigint)",
        False,
        False,
        True,
    ),
    (
        "reconciliation_observations",
        "ck_reconciliation_observations_semantic_hash",
        "c",
        "CHECK (semantic_hash::text ~ '^[0-9a-f]{64}$'::text)",
        False,
        False,
        True,
    ),
    (
        "reconciliation_observations",
        "fk_reconciliation_observations_subject",
        "f",
        (
            "FOREIGN KEY (subject_id) REFERENCES "
            "ci_coordinator.reconciliation_subjects(subject_id) "
            "ON DELETE RESTRICT"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_observations",
        "pk_reconciliation_observations",
        "p",
        "PRIMARY KEY (subject_id, observation_id)",
        False,
        False,
        True,
    ),
    (
        "reconciliation_observations",
        "uq_reconciliation_observations_subject_revision",
        "u",
        "UNIQUE (subject_id, revision)",
        False,
        False,
        True,
    ),
    (
        "reconciliation_results",
        "ck_reconciliation_results_canonical_byte_limit",
        "c",
        (
            "CHECK (octet_length(result_canonical_json) >= 1 AND "
            "octet_length(result_canonical_json) <= 1048576)"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_results",
        "ck_reconciliation_results_revision_safe",
        "c",
        "CHECK (revision >= 0 AND revision <= '9007199254740991'::bigint)",
        False,
        False,
        True,
    ),
    (
        "reconciliation_results",
        "ck_reconciliation_results_semantic_hash",
        "c",
        "CHECK (semantic_hash::text ~ '^[0-9a-f]{64}$'::text)",
        False,
        False,
        True,
    ),
    (
        "reconciliation_results",
        "ck_reconciliation_results_subject_hash",
        "c",
        "CHECK (subject_id::text ~ '^[0-9a-f]{64}$'::text)",
        False,
        False,
        True,
    ),
    (
        "reconciliation_results",
        "fk_reconciliation_results_subject",
        "f",
        (
            "FOREIGN KEY (subject_id) REFERENCES "
            "ci_coordinator.reconciliation_subjects(subject_id) "
            "ON DELETE RESTRICT"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_results",
        "pk_reconciliation_results",
        "p",
        "PRIMARY KEY (subject_id, revision)",
        False,
        False,
        True,
    ),
    (
        "reconciliation_subjects",
        "ck_reconciliation_subjects_canonical_byte_limits",
        "c",
        (
            "CHECK (octet_length(subject_canonical_json) >= 1 AND "
            "octet_length(subject_canonical_json) <= 16384 AND "
            "octet_length(contract_canonical_json) >= 1 AND "
            "octet_length(contract_canonical_json) <= 1048576)"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_subjects",
        "ck_reconciliation_subjects_convergence_bounds",
        "c",
        (
            "CHECK (attempt_count >= 0 AND attempt_count <= max_attempts AND "
            "max_attempts >= 1 AND max_attempts <= 1000 AND backoff_seconds >= 1 AND "
            "backoff_seconds <= max_backoff_seconds AND max_backoff_seconds >= 1 AND "
            "max_backoff_seconds <= 3600 AND claim_generation >= attempt_count AND "
            "claim_generation <= '9007199254740991'::bigint)"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_subjects",
        "ck_reconciliation_subjects_convergence_lifetime",
        "c",
        (
            "CHECK (deadline_at > created_at AND next_attempt_at >= created_at AND "
            "next_attempt_at <= deadline_at AND deadline_at <= (created_at + "
            "'24:00:00'::interval))"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_subjects",
        "ck_reconciliation_subjects_hashes",
        "c",
        (
            "CHECK (subject_id::text ~ '^[0-9a-f]{64}$'::text AND "
            "identity_hash::text = subject_id::text AND contract_hash::text ~ "
            "'^[0-9a-f]{64}$'::text)"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_subjects",
        "ck_reconciliation_subjects_lease_shape",
        "c",
        (
            "CHECK (lease_token IS NULL AND lease_acquired_at IS NULL AND "
            "lease_expires_at IS NULL OR lease_token IS NOT NULL AND "
            "lease_token::text ~ '^[0-9a-f]{64}$'::text AND lease_acquired_at IS NOT NULL AND "
            "lease_expires_at IS NOT NULL AND "
            "lease_acquired_at >= created_at AND lease_expires_at > lease_acquired_at AND "
            "lease_expires_at <= (lease_acquired_at + "
            "'01:00:00'::interval))"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_subjects",
        "ck_reconciliation_subjects_revision_safe",
        "c",
        "CHECK (revision >= 0 AND revision <= '9007199254740991'::bigint)",
        False,
        False,
        True,
    ),
    (
        "reconciliation_subjects",
        "ck_reconciliation_subjects_scope_safe",
        "c",
        (
            "CHECK (installation_id >= 1 AND installation_id <= "
            "'9007199254740991'::bigint AND repository_id >= 1 AND "
            "repository_id <= '9007199254740991'::bigint)"
        ),
        False,
        False,
        True,
    ),
    (
        "reconciliation_subjects",
        "pk_reconciliation_subjects",
        "p",
        "PRIMARY KEY (subject_id)",
        False,
        False,
        True,
    ),
    (
        "shadow_evidence",
        "ck_shadow_evidence_key_byte_limits",
        "c",
        (
            "CHECK (octet_length(profile_id::text) >= 1 AND "
            "octet_length(profile_id::text) <= 256 AND "
            "octet_length(repository::text) >= 1 AND "
            "octet_length(repository::text) <= 1024 AND octet_length(event::text) >= 1 AND "
            "octet_length(event::text) <= 1024 AND octet_length(surface::text) >= 1 AND "
            "octet_length(surface::text) <= 1024)"
        ),
        False,
        False,
        True,
    ),
    (
        "shadow_evidence",
        "ck_shadow_evidence_record_byte_limit",
        "c",
        (
            "CHECK (octet_length(record_canonical_json) >= 1 AND "
            "octet_length(record_canonical_json) <= 1048576)"
        ),
        False,
        False,
        True,
    ),
    (
        "shadow_evidence",
        "ck_shadow_evidence_semantic_hash",
        "c",
        "CHECK (semantic_hash::text ~ '^[0-9a-f]{64}$'::text)",
        False,
        False,
        True,
    ),
    (
        "shadow_evidence",
        "pk_shadow_evidence",
        "p",
        "PRIMARY KEY (profile_id, repository, event, surface)",
        False,
        False,
        True,
    ),
)
_EXPECTED_INDEXES: Final[tuple[IndexFact, ...]] = (
    (
        "reconciliation_subjects",
        "ix_reconciliation_subjects_due",
        False,
        False,
        True,
        True,
        True,
        (
            "CREATE INDEX ix_reconciliation_subjects_due ON "
            "ci_coordinator.reconciliation_subjects USING btree (next_attempt_at, subject_id)"
        ),
    ),
    (
        "reconciliation_subjects",
        "ix_reconciliation_subjects_scope_created",
        False,
        False,
        True,
        True,
        True,
        (
            "CREATE INDEX ix_reconciliation_subjects_scope_created ON "
            "ci_coordinator.reconciliation_subjects USING btree "
            "(installation_id, repository_id, created_at, subject_id)"
        ),
    ),
)
_EXPECTED_TABLES: Final[tuple[TableFact, ...]] = (
    ("reconciliation_observations", "r", "p", False, False),
    ("reconciliation_results", "r", "p", False, False),
    ("reconciliation_subjects", "r", "p", False, False),
    ("shadow_evidence", "r", "p", False, False),
)


async def shadow_reconciliation_state_schema_matches_contract(
    connection: AsyncConnection,
) -> bool:
    return await connection.run_sync(shadow_reconciliation_state_schema_matches_contract_sync)


def shadow_reconciliation_state_schema_matches_contract_sync(connection: Connection) -> bool:
    return _shadow_predecessor_projection_matches(connection, successor=False)


def _shadow_predecessor_projection_matches(connection: Connection, *, successor: bool) -> bool:
    columns = tuple(
        connection.execute(
            text(
                "SELECT relation.relname, attribute.attname, "
                "format_type(attribute.atttypid, attribute.atttypmod), "
                "NOT attribute.attnotnull "
                "FROM pg_attribute AS attribute "
                "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname = ANY(:relations) "
                "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
                "ORDER BY relation.relname, attribute.attnum"
            ),
            {"schema": APPLICATION_SCHEMA, "relations": list(_RELATIONS)},
        )
    )
    column_facts: tuple[ColumnFact, ...] = tuple(
        (row[0], row[1], row[2], row[3] is True) for row in columns
    )
    constraints = tuple(
        connection.execute(
            text(
                "SELECT relation.relname, conname, contype, "
                "pg_get_constraintdef(pg_constraint.oid, true), "
                "condeferrable, condeferred, convalidated "
                "FROM pg_constraint "
                "JOIN pg_class AS relation ON relation.oid = conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname = ANY(:relations) "
                "AND contype <> 'n' ORDER BY relation.relname, conname"
            ),
            {"schema": APPLICATION_SCHEMA, "relations": list(_RELATIONS)},
        )
    )
    constraint_facts: tuple[ConstraintFact, ...] = tuple(
        (row[0], row[1], row[2], row[3], row[4], row[5], row[6]) for row in constraints
    )
    indexes = tuple(
        connection.execute(
            text(
                "SELECT table_metadata.relname, index_relation.relname, "
                "index_metadata.indisunique, index_metadata.indisprimary, "
                "index_metadata.indisvalid, index_metadata.indisready, "
                "index_metadata.indislive, pg_get_indexdef(index_relation.oid, 0, false) "
                "FROM pg_index AS index_metadata "
                "JOIN pg_class AS index_relation "
                "ON index_relation.oid = index_metadata.indexrelid "
                "JOIN pg_class AS table_metadata "
                "ON table_metadata.oid = index_metadata.indrelid "
                "JOIN pg_namespace AS namespace "
                "ON namespace.oid = index_relation.relnamespace "
                "LEFT JOIN pg_constraint AS constraint_metadata "
                "ON constraint_metadata.conindid = index_metadata.indexrelid "
                "WHERE namespace.nspname = :schema "
                "AND table_metadata.relname = ANY(:relations) "
                "AND constraint_metadata.oid IS NULL "
                "ORDER BY table_metadata.relname, index_relation.relname"
            ),
            {"schema": APPLICATION_SCHEMA, "relations": list(_RELATIONS)},
        )
    )
    index_facts: tuple[IndexFact, ...] = tuple(
        (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]) for row in indexes
    )
    tables = tuple(
        connection.execute(
            text(
                "SELECT relation.relname, relation.relkind, relation.relpersistence, "
                "relation.relrowsecurity, relation.relforcerowsecurity "
                "FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname = ANY(:relations) "
                "ORDER BY relation.relname"
            ),
            {"schema": APPLICATION_SCHEMA, "relations": list(_RELATIONS)},
        )
    )
    table_facts: tuple[TableFact, ...] = tuple(
        (row[0], row[1], row[2], row[3] is True, row[4] is True) for row in tables
    )
    unexpected_objects = connection.scalar(
        text(
            "SELECT ("
            "SELECT count(*) FROM pg_constraint AS constraint_metadata "
            "WHERE constraint_metadata.conrelid = ANY("
            "SELECT relation.oid FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname = ANY(:relations)) "
            "AND constraint_metadata.contype <> 'n' AND NOT constraint_metadata.conenforced"
            ") + ("
            "SELECT count(*) FROM pg_trigger AS trigger_metadata "
            "WHERE trigger_metadata.tgrelid = ANY("
            "SELECT relation.oid FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname = ANY(:relations)) "
            "AND NOT trigger_metadata.tgisinternal"
            ") + ("
            "SELECT count(*) FROM pg_policy AS policy_metadata "
            "WHERE policy_metadata.polrelid = ANY("
            "SELECT relation.oid FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname = ANY(:relations))"
            ")"
        ),
        {"schema": APPLICATION_SCHEMA, "relations": list(_RELATIONS)},
    )
    if successor:
        column_facts = tuple(
            row
            for row in column_facts
            if not (
                row[0] == "reconciliation_subjects"
                and row[1]
                in {"execution_origin", "production_generation", "production_authority_id"}
            )
        )
        constraint_facts = tuple(
            row
            for row in constraint_facts
            if not (
                row[0] == "reconciliation_subjects"
                and row[1]
                in {
                    "ck_reconciliation_subjects_production_origin",
                    "fk_reconciliation_subjects_production_generation",
                }
            )
        )
        index_facts = tuple(
            row
            for row in index_facts
            if (row[0], row[1])
            != ("reconciliation_subjects", "ix_reconciliation_subjects_production_drain")
        )
    return (
        column_facts == _EXPECTED_COLUMNS
        and constraint_facts == _EXPECTED_CONSTRAINTS
        and index_facts == _EXPECTED_INDEXES
        and table_facts == _EXPECTED_TABLES
        and unexpected_objects == 0
    )
