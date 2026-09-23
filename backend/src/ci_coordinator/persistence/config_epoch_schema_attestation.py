"""Catalog fact collection for the config-epoch lifecycle capability."""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.catalog_observation import (
    column_rows,
)
from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

_RELATIONS: Final = ("active_config_epochs", "config_epoch_activations", "config_epochs")
_EXPECTED_COLUMNS: Final = (
    ("active_config_epochs", "installation_id", "bigint", False, None),
    ("active_config_epochs", "repository_id", "bigint", False, None),
    ("active_config_epochs", "epoch_id", "character varying(64)", False, None),
    ("active_config_epochs", "revision", "bigint", False, None),
    ("config_epoch_activations", "installation_id", "bigint", False, None),
    ("config_epoch_activations", "repository_id", "bigint", False, None),
    ("config_epoch_activations", "operation_id", "character varying(256)", False, None),
    ("config_epoch_activations", "expected_revision", "bigint", True, None),
    ("config_epoch_activations", "previous_epoch_id", "character varying(64)", True, None),
    ("config_epoch_activations", "previous_revision", "bigint", True, None),
    ("config_epoch_activations", "target_epoch_id", "character varying(64)", False, None),
    ("config_epoch_activations", "result_revision", "bigint", False, None),
    ("config_epoch_activations", "audit_event_id", "character varying(38)", False, None),
    ("config_epoch_activations", "audit_input_hash", "bytea", False, None),
    ("config_epochs", "epoch_id", "character varying(64)", False, None),
    ("config_epochs", "installation_id", "bigint", False, None),
    ("config_epochs", "repository_id", "bigint", False, None),
    ("config_epochs", "source_format", "character varying(8)", False, None),
    ("config_epochs", "source_bytes", "bytea", False, None),
    ("config_epochs", "normalized_document_bytes", "bytea", False, None),
    ("config_epochs", "compiled_policy_bytes", "bytea", False, None),
    ("config_epochs", "document_schema_id", "character varying(4096)", False, None),
    ("config_epochs", "document_profile_id", "character varying(4096)", False, None),
    ("config_epochs", "semantic_profile_id", "character varying(4096)", False, None),
    ("config_epochs", "compiled_schema_id", "character varying(4096)", False, None),
    ("config_epochs", "producer_resource_profile_id", "character varying(4096)", False, None),
    ("config_epochs", "producer_byte_profile_id", "character varying(4096)", False, None),
    (
        "config_epochs",
        "producer_feasibility_profile_id",
        "character varying(4096)",
        False,
        None,
    ),
    ("config_epochs", "source_hash", "character varying(64)", False, None),
    ("config_epochs", "document_hash", "character varying(64)", False, None),
    ("config_epochs", "epoch_hash", "character varying(64)", False, None),
)
_EXPECTED_CONSTRAINTS: Final = frozenset(
    {
        ("active_config_epochs", "ck_active_config_epochs_epoch_id_hash", "c"),
        ("active_config_epochs", "ck_active_config_epochs_installation_id_safe", "c"),
        ("active_config_epochs", "ck_active_config_epochs_repository_id_safe", "c"),
        ("active_config_epochs", "ck_active_config_epochs_revision_safe", "c"),
        ("active_config_epochs", "fk_active_config_epochs_epoch", "f"),
        ("active_config_epochs", "pk_active_config_epochs", "p"),
        (
            "config_epoch_activations",
            "ck_config_epoch_activations_audit_identity",
            "c",
        ),
        (
            "config_epoch_activations",
            "ck_config_epoch_activations_epoch_hashes",
            "c",
        ),
        (
            "config_epoch_activations",
            "ck_config_epoch_activations_installation_id_safe",
            "c",
        ),
        (
            "config_epoch_activations",
            "ck_config_epoch_activations_operation_id_limit",
            "c",
        ),
        (
            "config_epoch_activations",
            "ck_config_epoch_activations_repository_id_safe",
            "c",
        ),
        (
            "config_epoch_activations",
            "ck_config_epoch_activations_revision_successor",
            "c",
        ),
        (
            "config_epoch_activations",
            "ck_config_epoch_activations_revisions_safe",
            "c",
        ),
        ("config_epoch_activations", "fk_config_epoch_activations_audit_event", "f"),
        (
            "config_epoch_activations",
            "fk_config_epoch_activations_previous_epoch",
            "f",
        ),
        (
            "config_epoch_activations",
            "fk_config_epoch_activations_target_epoch",
            "f",
        ),
        ("config_epoch_activations", "pk_config_epoch_activations", "p"),
        (
            "config_epoch_activations",
            "uq_config_epoch_activations_audit_event",
            "u",
        ),
        (
            "config_epoch_activations",
            "uq_config_epoch_activations_scope_revision",
            "u",
        ),
        ("config_epochs", "ck_config_epochs_compiled_bytes_limit", "c"),
        ("config_epochs", "ck_config_epochs_compiled_schema_id_limit", "c"),
        ("config_epochs", "ck_config_epochs_document_profile_id_limit", "c"),
        ("config_epochs", "ck_config_epochs_document_schema_id_limit", "c"),
        ("config_epochs", "ck_config_epochs_epoch_id_hash", "c"),
        ("config_epochs", "ck_config_epochs_hashes", "c"),
        ("config_epochs", "ck_config_epochs_installation_id_safe", "c"),
        ("config_epochs", "ck_config_epochs_normalized_bytes_limit", "c"),
        ("config_epochs", "ck_config_epochs_producer_byte_profile_id_limit", "c"),
        (
            "config_epochs",
            "ck_config_epochs_producer_feasibility_profile_id_limit",
            "c",
        ),
        (
            "config_epochs",
            "ck_config_epochs_producer_resource_profile_id_limit",
            "c",
        ),
        ("config_epochs", "ck_config_epochs_repository_id_safe", "c"),
        ("config_epochs", "ck_config_epochs_semantic_profile_id_limit", "c"),
        ("config_epochs", "ck_config_epochs_source_bytes_limit", "c"),
        ("config_epochs", "ck_config_epochs_source_format", "c"),
        ("config_epochs", "pk_config_epochs", "p"),
        ("config_epochs", "uq_config_epochs_scope_epoch", "u"),
    }
)
_EXPECTED_CONSTRAINT_DEFINITIONS: Final = {
    ("active_config_epochs", "ck_active_config_epochs_epoch_id_hash"): (
        "CHECK (epoch_id::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    ("active_config_epochs", "ck_active_config_epochs_installation_id_safe"): (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint)"
    ),
    ("active_config_epochs", "ck_active_config_epochs_repository_id_safe"): (
        "CHECK (repository_id >= 1 AND repository_id <= '9007199254740991'::bigint)"
    ),
    ("active_config_epochs", "ck_active_config_epochs_revision_safe"): (
        "CHECK (revision >= 1 AND revision <= '9007199254740991'::bigint)"
    ),
    ("active_config_epochs", "fk_active_config_epochs_epoch"): (
        "FOREIGN KEY (installation_id, repository_id, epoch_id) REFERENCES "
        "ci_coordinator.config_epochs(installation_id, repository_id, epoch_id) ON DELETE RESTRICT"
    ),
    ("active_config_epochs", "pk_active_config_epochs"): (
        "PRIMARY KEY (installation_id, repository_id)"
    ),
    ("config_epoch_activations", "ck_config_epoch_activations_audit_identity"): (
        "CHECK (audit_event_id::text ~ '^audit_[0-9a-f]{32}$'::text AND "
        "octet_length(audit_input_hash) = 32)"
    ),
    ("config_epoch_activations", "ck_config_epoch_activations_epoch_hashes"): (
        "CHECK (target_epoch_id::text ~ '^[0-9a-f]{64}$'::text AND "
        "(previous_epoch_id IS NULL OR previous_epoch_id::text ~ '^[0-9a-f]{64}$'::text))"
    ),
    ("config_epoch_activations", "ck_config_epoch_activations_installation_id_safe"): (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint)"
    ),
    ("config_epoch_activations", "ck_config_epoch_activations_operation_id_limit"): (
        "CHECK (octet_length(operation_id::text) >= 1 AND octet_length(operation_id::text) <= 256)"
    ),
    ("config_epoch_activations", "ck_config_epoch_activations_repository_id_safe"): (
        "CHECK (repository_id >= 1 AND repository_id <= '9007199254740991'::bigint)"
    ),
    ("config_epoch_activations", "ck_config_epoch_activations_revision_successor"): (
        "CHECK (expected_revision IS NULL AND previous_epoch_id IS NULL AND "
        "previous_revision IS NULL AND result_revision = 1 OR expected_revision >= 1 AND "
        "previous_epoch_id IS NOT NULL AND previous_revision = expected_revision AND "
        "result_revision = (previous_revision + 1))"
    ),
    ("config_epoch_activations", "ck_config_epoch_activations_revisions_safe"): (
        "CHECK (result_revision >= 1 AND result_revision <= '9007199254740991'::bigint AND "
        "(expected_revision IS NULL OR expected_revision <= '9007199254740991'::bigint) AND "
        "(previous_revision IS NULL OR previous_revision <= '9007199254740991'::bigint))"
    ),
    ("config_epoch_activations", "fk_config_epoch_activations_audit_event"): (
        "FOREIGN KEY (audit_event_id) REFERENCES ci_coordinator.audit_events(audit_event_id) "
        "ON DELETE RESTRICT"
    ),
    ("config_epoch_activations", "fk_config_epoch_activations_previous_epoch"): (
        "FOREIGN KEY (installation_id, repository_id, previous_epoch_id) REFERENCES "
        "ci_coordinator.config_epochs(installation_id, repository_id, epoch_id) ON DELETE RESTRICT"
    ),
    ("config_epoch_activations", "fk_config_epoch_activations_target_epoch"): (
        "FOREIGN KEY (installation_id, repository_id, target_epoch_id) REFERENCES "
        "ci_coordinator.config_epochs(installation_id, repository_id, epoch_id) ON DELETE RESTRICT"
    ),
    ("config_epoch_activations", "pk_config_epoch_activations"): (
        "PRIMARY KEY (installation_id, repository_id, operation_id)"
    ),
    ("config_epoch_activations", "uq_config_epoch_activations_audit_event"): (
        "UNIQUE (audit_event_id)"
    ),
    ("config_epoch_activations", "uq_config_epoch_activations_scope_revision"): (
        "UNIQUE (installation_id, repository_id, result_revision)"
    ),
    ("config_epochs", "ck_config_epochs_compiled_bytes_limit"): (
        "CHECK (octet_length(compiled_policy_bytes) >= 1 AND "
        "octet_length(compiled_policy_bytes) <= 4194304)"
    ),
    ("config_epochs", "ck_config_epochs_compiled_schema_id_limit"): (
        "CHECK (octet_length(compiled_schema_id::text) >= 1 AND "
        "octet_length(compiled_schema_id::text) <= 4096)"
    ),
    ("config_epochs", "ck_config_epochs_document_profile_id_limit"): (
        "CHECK (octet_length(document_profile_id::text) >= 1 AND "
        "octet_length(document_profile_id::text) <= 4096)"
    ),
    ("config_epochs", "ck_config_epochs_document_schema_id_limit"): (
        "CHECK (octet_length(document_schema_id::text) >= 1 AND "
        "octet_length(document_schema_id::text) <= 4096)"
    ),
    ("config_epochs", "ck_config_epochs_epoch_id_hash"): (
        "CHECK (epoch_id::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    ("config_epochs", "ck_config_epochs_hashes"): (
        "CHECK (source_hash::text ~ '^[0-9a-f]{64}$'::text AND "
        "document_hash::text ~ '^[0-9a-f]{64}$'::text AND "
        "epoch_hash::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    ("config_epochs", "ck_config_epochs_installation_id_safe"): (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint)"
    ),
    ("config_epochs", "ck_config_epochs_normalized_bytes_limit"): (
        "CHECK (octet_length(normalized_document_bytes) >= 1 AND "
        "octet_length(normalized_document_bytes) <= 4194304)"
    ),
    ("config_epochs", "ck_config_epochs_producer_byte_profile_id_limit"): (
        "CHECK (octet_length(producer_byte_profile_id::text) >= 1 AND "
        "octet_length(producer_byte_profile_id::text) <= 4096)"
    ),
    ("config_epochs", "ck_config_epochs_producer_feasibility_profile_id_limit"): (
        "CHECK (octet_length(producer_feasibility_profile_id::text) >= 1 AND "
        "octet_length(producer_feasibility_profile_id::text) <= 4096)"
    ),
    ("config_epochs", "ck_config_epochs_producer_resource_profile_id_limit"): (
        "CHECK (octet_length(producer_resource_profile_id::text) >= 1 AND "
        "octet_length(producer_resource_profile_id::text) <= 4096)"
    ),
    ("config_epochs", "ck_config_epochs_repository_id_safe"): (
        "CHECK (repository_id >= 1 AND repository_id <= '9007199254740991'::bigint)"
    ),
    ("config_epochs", "ck_config_epochs_semantic_profile_id_limit"): (
        "CHECK (octet_length(semantic_profile_id::text) >= 1 AND "
        "octet_length(semantic_profile_id::text) <= 4096)"
    ),
    ("config_epochs", "ck_config_epochs_source_bytes_limit"): (
        "CHECK (octet_length(source_bytes) <= 2097152)"
    ),
    ("config_epochs", "ck_config_epochs_source_format"): (
        "CHECK (source_format::text = ANY (ARRAY['json'::character varying, "
        "'yaml-1.2'::character varying]::text[]))"
    ),
    ("config_epochs", "pk_config_epochs"): "PRIMARY KEY (epoch_id)",
    ("config_epochs", "uq_config_epochs_scope_epoch"): (
        "UNIQUE (installation_id, repository_id, epoch_id)"
    ),
}
_EXPECTED_TABLES: Final = (
    ("active_config_epochs", "r", "p", False, False),
    ("config_epoch_activations", "r", "p", False, False),
    ("config_epochs", "r", "p", False, False),
)
_EXPECTED_STORAGE: Final = (
    ("config_epoch_activations", "audit_input_hash", "x"),
    ("config_epochs", "source_bytes", "x"),
    ("config_epochs", "normalized_document_bytes", "x"),
    ("config_epochs", "compiled_policy_bytes", "x"),
)
_EXPECTED_TRIGGERS: Final = frozenset(
    {
        ("config_epoch_activations", "tr_config_epoch_activations_immutable"),
        ("config_epochs", "tr_config_epochs_immutable"),
    }
)


async def config_epoch_lifecycle_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(config_epoch_lifecycle_schema_matches_contract_sync)


def config_epoch_lifecycle_schema_matches_contract_sync(connection: Connection) -> bool:
    columns = column_rows(
        connection, ("active_config_epochs", "config_epoch_activations", "config_epochs")
    )
    column_facts = tuple((row[0], row[1], row[2], row[3] is True, row[4]) for row in columns)
    constraints = tuple(
        connection.execute(
            text(
                "SELECT relation.relname, conname, contype, "
                "pg_get_constraintdef(pg_constraint.oid, true), "
                "condeferrable, condeferred, convalidated FROM pg_constraint "
                "JOIN pg_class AS relation ON relation.oid = conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname IN "
                "('active_config_epochs', 'config_epoch_activations', 'config_epochs') "
                "AND contype <> 'n'"
            ),
            {"schema": APPLICATION_SCHEMA},
        )
    )
    constraint_facts = frozenset((row[0], row[1], row[2]) for row in constraints)
    tables = tuple(
        connection.execute(
            text(
                "SELECT relation.relname, relation.relkind, relation.relpersistence, "
                "relation.relrowsecurity, relation.relforcerowsecurity "
                "FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname IN "
                "('active_config_epochs', 'config_epoch_activations', 'config_epochs') "
                "ORDER BY relation.relname"
            ),
            {"schema": APPLICATION_SCHEMA},
        )
    )
    table_facts = tuple((row[0], row[1], row[2], row[3] is True, row[4] is True) for row in tables)
    storage = tuple(
        connection.execute(
            text(
                "SELECT relation.relname, attribute.attname, attribute.attstorage "
                "FROM pg_attribute AS attribute "
                "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND "
                "((relation.relname = 'config_epoch_activations' "
                "AND attribute.attname = 'audit_input_hash') OR "
                "(relation.relname = 'config_epochs' AND attribute.attname IN "
                "('source_bytes', 'normalized_document_bytes', 'compiled_policy_bytes'))) "
                "ORDER BY relation.relname, attribute.attnum"
            ),
            {"schema": APPLICATION_SCHEMA},
        )
    )
    storage_facts = tuple((row[0], row[1], row[2]) for row in storage)
    triggers = tuple(
        connection.execute(
            text(
                "SELECT relation.relname, trigger_metadata.tgname, function_metadata.prosecdef "
                "FROM pg_trigger AS trigger_metadata "
                "JOIN pg_class AS relation ON relation.oid = trigger_metadata.tgrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "JOIN pg_proc AS function_metadata "
                "ON function_metadata.oid = trigger_metadata.tgfoid "
                "WHERE namespace.nspname = :schema AND relation.relname IN "
                "('active_config_epochs', 'config_epoch_activations', 'config_epochs') "
                "AND NOT trigger_metadata.tgisinternal"
            ),
            {"schema": APPLICATION_SCHEMA},
        )
    )
    unexpected_objects = connection.scalar(
        text(
            "SELECT ("
            "SELECT count(*) FROM pg_index AS index_metadata "
            "JOIN pg_class AS relation ON relation.oid = index_metadata.indrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "LEFT JOIN pg_constraint AS constraint_metadata "
            "ON constraint_metadata.conindid = index_metadata.indexrelid "
            "WHERE namespace.nspname = :schema AND relation.relname IN "
            "('active_config_epochs', 'config_epoch_activations', 'config_epochs') "
            "AND constraint_metadata.oid IS NULL"
            ") + ("
            "SELECT count(*) FROM pg_policy AS policy_metadata WHERE policy_metadata.polrelid IN "
            "(to_regclass(:active), to_regclass(:activations), to_regclass(:epochs))"
            ")"
        ),
        {
            "schema": APPLICATION_SCHEMA,
            "active": f"{APPLICATION_SCHEMA}.active_config_epochs",
            "activations": f"{APPLICATION_SCHEMA}.config_epoch_activations",
            "epochs": f"{APPLICATION_SCHEMA}.config_epochs",
        },
    )
    return (
        column_facts == _EXPECTED_COLUMNS
        and constraint_facts == _EXPECTED_CONSTRAINTS
        and all(
            _EXPECTED_CONSTRAINT_DEFINITIONS[(row[0], row[1])] == row[3]
            and row[4] is False
            and row[5] is False
            and row[6] is True
            for row in constraints
        )
        and table_facts == _EXPECTED_TABLES
        and storage_facts == _EXPECTED_STORAGE
        and frozenset((row[0], row[1]) for row in triggers) == _EXPECTED_TRIGGERS
        and all(row[2] is False for row in triggers)
        and unexpected_objects == 0
    )
