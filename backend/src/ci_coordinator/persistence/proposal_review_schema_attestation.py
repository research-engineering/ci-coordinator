"""Catalog fact collection for proposal-review registration storage."""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.catalog_statements import (
    OWNED_RELATION_SQL,
    RELATION_COLUMNS_SQL,
    RELATION_CONSTRAINTS_SQL,
    RELATION_TRIGGERS_SQL,
)
from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

_RELATION: Final = "workflow_proposal_reviews"
_EXPECTED_COLUMNS: Final = (
    ("installation_id", "bigint", False, None),
    ("repository_id", "bigint", False, None),
    ("operation_id", "character varying(256)", False, None),
    ("proposal_manifest_id", "character varying(41)", False, None),
    ("provider_revision", "character varying(40)", False, None),
    ("inventory_digest", "character varying(64)", False, None),
    ("proposal_digest", "character varying(64)", False, None),
    ("base_epoch_id", "character varying(64)", True, None),
    ("base_revision", "bigint", True, None),
    ("target_epoch_id", "character varying(64)", False, None),
    ("semantic_diff_version", "character varying(64)", False, None),
    ("semantic_diff_canonical_json", "bytea", False, None),
    ("semantic_diff_hash", "character varying(64)", False, None),
    ("changed_pointer_count", "smallint", False, None),
    ("attestation_transaction_digest", "bytea", False, None),
    ("session_handle_digest", "bytea", False, None),
    ("initiating_actor", "character varying(82)", False, None),
    ("reviewer_user_id", "bigint", False, None),
    ("reviewer_login", "character varying(39)", False, None),
    ("reviewer_permission", "character varying(8)", False, None),
    ("attestation_issued_at", "timestamp with time zone", False, None),
    ("permission_observed_at", "timestamp with time zone", False, None),
    ("receipt_expires_at", "timestamp with time zone", False, None),
    ("authority_profile_digest", "character varying(64)", False, None),
    ("audit_event_id", "character varying(38)", False, None),
    ("audit_input_hash", "bytea", False, None),
)
_EXPECTED_CONSTRAINTS: Final = frozenset(
    {
        ("ck_workflow_proposal_reviews_audit_identity", "c"),
        ("ck_workflow_proposal_reviews_attestation_identity", "c"),
        ("ck_workflow_proposal_reviews_attestation_time", "c"),
        ("ck_workflow_proposal_reviews_base_shape", "c"),
        ("ck_workflow_proposal_reviews_diff_evidence", "c"),
        ("ck_workflow_proposal_reviews_diff_version", "c"),
        ("ck_workflow_proposal_reviews_installation_id_safe", "c"),
        ("ck_workflow_proposal_reviews_manifest_id", "c"),
        ("ck_workflow_proposal_reviews_operation_id_limit", "c"),
        ("ck_workflow_proposal_reviews_provider_identity", "c"),
        ("ck_workflow_proposal_reviews_repository_id_safe", "c"),
        ("ck_workflow_proposal_reviews_target_epoch", "c"),
        ("fk_workflow_proposal_reviews_audit_event", "f"),
        ("fk_workflow_proposal_reviews_base_epoch", "f"),
        ("fk_workflow_proposal_reviews_target_epoch", "f"),
        ("pk_workflow_proposal_reviews", "p"),
        ("uq_workflow_proposal_reviews_audit_event", "u"),
        ("uq_workflow_proposal_reviews_attestation_transaction", "u"),
        ("uq_workflow_proposal_reviews_scope_manifest", "u"),
    }
)
_EXPECTED_CONSTRAINT_DEFINITIONS: Final = {
    "ck_workflow_proposal_reviews_audit_identity": (
        "CHECK (audit_event_id::text ~ '^audit_[0-9a-f]{32}$'::text AND "
        "octet_length(audit_input_hash) = 32)"
    ),
    "ck_workflow_proposal_reviews_attestation_identity": (
        "CHECK (octet_length(attestation_transaction_digest) = 32 AND "
        "octet_length(session_handle_digest) = 32 AND "
        "initiating_actor::text ~ '^keycloak-human:v1:[0-9a-f]{64}$'::text AND "
        "reviewer_user_id >= 1 AND reviewer_user_id <= '9007199254740991'::bigint AND "
        "reviewer_login::text ~ "
        "'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$'::text AND "
        "reviewer_permission::text ~ '^(admin|maintain)$'::text)"
    ),
    "ck_workflow_proposal_reviews_attestation_time": (
        "CHECK (isfinite(attestation_issued_at) AND isfinite(permission_observed_at) AND "
        "isfinite(receipt_expires_at) AND permission_observed_at >= attestation_issued_at AND "
        "receipt_expires_at > permission_observed_at AND "
        "receipt_expires_at <= (attestation_issued_at + '00:05:00'::interval) AND "
        "authority_profile_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    "ck_workflow_proposal_reviews_base_shape": (
        "CHECK (base_epoch_id IS NULL AND base_revision IS NULL OR "
        "base_epoch_id::text ~ '^[0-9a-f]{64}$'::text AND base_revision >= 1 AND "
        "base_revision <= '9007199254740991'::bigint)"
    ),
    "ck_workflow_proposal_reviews_diff_evidence": (
        "CHECK (octet_length(semantic_diff_canonical_json) >= 1 AND "
        "octet_length(semantic_diff_canonical_json) <= 65536 AND "
        "semantic_diff_hash::text ~ '^[0-9a-f]{64}$'::text AND "
        "changed_pointer_count >= 0 AND changed_pointer_count <= 1024)"
    ),
    "ck_workflow_proposal_reviews_diff_version": (
        "CHECK (semantic_diff_version::text = 'policy-semantic-diff/v1'::text)"
    ),
    "ck_workflow_proposal_reviews_installation_id_safe": (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint)"
    ),
    "ck_workflow_proposal_reviews_manifest_id": (
        "CHECK (proposal_manifest_id::text ~ '^proposal:[0-9a-f]{32}$'::text)"
    ),
    "ck_workflow_proposal_reviews_operation_id_limit": (
        "CHECK (octet_length(operation_id::text) >= 1 AND octet_length(operation_id::text) <= 256)"
    ),
    "ck_workflow_proposal_reviews_provider_identity": (
        "CHECK (provider_revision::text ~ '^[0-9a-f]{40}$'::text AND "
        "inventory_digest::text ~ '^[0-9a-f]{64}$'::text AND "
        "proposal_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    "ck_workflow_proposal_reviews_repository_id_safe": (
        "CHECK (repository_id >= 1 AND repository_id <= '9007199254740991'::bigint)"
    ),
    "ck_workflow_proposal_reviews_target_epoch": (
        "CHECK (target_epoch_id::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    "fk_workflow_proposal_reviews_audit_event": (
        "FOREIGN KEY (audit_event_id) REFERENCES "
        "ci_coordinator.audit_events(audit_event_id) ON DELETE RESTRICT"
    ),
    "fk_workflow_proposal_reviews_base_epoch": (
        "FOREIGN KEY (installation_id, repository_id, base_epoch_id) REFERENCES "
        "ci_coordinator.config_epochs(installation_id, repository_id, epoch_id) "
        "ON DELETE RESTRICT"
    ),
    "fk_workflow_proposal_reviews_target_epoch": (
        "FOREIGN KEY (installation_id, repository_id, target_epoch_id) REFERENCES "
        "ci_coordinator.config_epochs(installation_id, repository_id, epoch_id) "
        "ON DELETE RESTRICT"
    ),
    "pk_workflow_proposal_reviews": ("PRIMARY KEY (installation_id, repository_id, operation_id)"),
    "uq_workflow_proposal_reviews_audit_event": "UNIQUE (audit_event_id)",
    "uq_workflow_proposal_reviews_attestation_transaction": (
        "UNIQUE (attestation_transaction_digest)"
    ),
    "uq_workflow_proposal_reviews_scope_manifest": (
        "UNIQUE (installation_id, repository_id, proposal_manifest_id)"
    ),
}
_EXPECTED_ROUTINE: Final = (
    "reject_proposal_review_mutation",
    "",
    "trigger",
    "plpgsql",
    False,
    "v",
    False,
    False,
    (
        " BEGIN RAISE EXCEPTION 'workflow proposal review history is immutable' "
        "USING ERRCODE = '42501'; END; "
    ),
    True,
)


async def proposal_review_registration_schema_matches_contract(
    connection: AsyncConnection,
) -> bool:
    return await connection.run_sync(proposal_review_registration_schema_matches_contract_sync)


def proposal_review_registration_schema_matches_contract_sync(
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
            "AND attribute.attname = 'semantic_diff_canonical_json'"
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
            "routine.proowner = namespace.nspowner "
            "FROM pg_proc AS routine "
            "JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
            "JOIN pg_language AS language ON language.oid = routine.prolang "
            "WHERE namespace.nspname = :schema "
            "AND routine.proname = 'reject_proposal_review_mutation'"
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
            _EXPECTED_CONSTRAINT_DEFINITIONS[row[0]] == row[2]
            and row[3] is False
            and row[4] is False
            and row[5] is True
            for row in constraints
        )
        and table == ("r", "p", False, False, True, True)
        and storage == "x"
        and trigger
        == (
            "tr_workflow_proposal_reviews_immutable",
            "reject_proposal_review_mutation",
            False,
            "O",
            27,
            True,
        )
        and routine == _EXPECTED_ROUTINE
        and unexpected == 0
    )
