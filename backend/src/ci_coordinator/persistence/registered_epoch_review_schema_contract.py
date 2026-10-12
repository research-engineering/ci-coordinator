"""Independent PostgreSQL catalog contract for registered-epoch review storage."""

from typing import Final

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_schema_contract import EconomicsCatalogContract

_PENDING: Final = "registered_epoch_attestation_transactions"
_REVIEWS: Final = "registered_epoch_reviews"
_NULLABLE: Final = frozenset(
    {
        (_PENDING, "expected_active_epoch_id"),
        (_PENDING, "expected_active_revision"),
        (_REVIEWS, "base_epoch_id"),
        (_REVIEWS, "base_revision"),
    }
)
_SHAPES: Final = {
    _PENDING: (
        ("transaction_digest", "bytea"),
        ("session_handle_digest", "bytea"),
        ("installation_id", "bigint"),
        ("repository_id", "bigint"),
        ("operation_id", "character varying(256)"),
        ("target_epoch_id", "character varying(64)"),
        ("source_hash", "character varying(64)"),
        ("provider_revision", "character varying(40)"),
        ("inventory_digest", "character varying(64)"),
        ("expected_active_epoch_id", "character varying(64)"),
        ("expected_active_revision", "bigint"),
        ("initiating_actor", "character varying(82)"),
        ("authority_profile_digest", "character varying(64)"),
        ("issued_at", "timestamp with time zone"),
        ("expires_at", "timestamp with time zone"),
    ),
    _REVIEWS: (
        ("installation_id", "bigint"),
        ("repository_id", "bigint"),
        ("operation_id", "character varying(256)"),
        ("target_epoch_id", "character varying(64)"),
        ("source_hash", "character varying(64)"),
        ("registration_operation_id", "character varying(256)"),
        ("provider_revision", "character varying(40)"),
        ("inventory_digest", "character varying(64)"),
        ("base_epoch_id", "character varying(64)"),
        ("base_revision", "bigint"),
        ("semantic_diff_version", "character varying(64)"),
        ("semantic_diff_canonical_json", "bytea"),
        ("semantic_diff_hash", "character varying(64)"),
        ("changed_pointer_count", "smallint"),
        ("attestation_transaction_digest", "bytea"),
        ("session_handle_digest", "bytea"),
        ("initiating_actor", "character varying(82)"),
        ("reviewer_user_id", "bigint"),
        ("reviewer_login", "character varying(39)"),
        ("reviewer_permission", "character varying(8)"),
        ("attestation_issued_at", "timestamp with time zone"),
        ("permission_observed_at", "timestamp with time zone"),
        ("receipt_expires_at", "timestamp with time zone"),
        ("authority_profile_digest", "character varying(64)"),
        ("audit_event_id", "character varying(38)"),
        ("audit_input_hash", "bytea"),
    ),
}
_RELATIONS: Final = tuple(sorted(_SHAPES))
_SAFE: Final = "'9007199254740991'::bigint"
_SCOPE: Final = (
    f"CHECK (installation_id >= 1 AND installation_id <= {_SAFE} AND repository_id >= 1 AND "
    f"repository_id <= {_SAFE})"
)
_SUBJECT: Final = (
    "CHECK (target_epoch_id::text ~ '^[0-9a-f]{64}$'::text AND "
    "source_hash::text ~ '^[0-9a-f]{64}$'::text AND "
    "provider_revision::text ~ '^[0-9a-f]{40}$'::text AND "
    "inventory_digest::text ~ '^[0-9a-f]{64}$'::text)"
)


def _epoch_reference(column: str) -> str:
    return (
        f"FOREIGN KEY (installation_id, repository_id, {column}) REFERENCES "
        "ci_coordinator.config_epochs(installation_id, repository_id, epoch_id) "
        "ON DELETE RESTRICT"
    )


_CHECKS: Final = {
    (_PENDING, "ck_registered_epoch_attestation_transactions_digests"): (
        "CHECK (octet_length(transaction_digest) = 32 AND octet_length(session_handle_digest) = 32)"
    ),
    (_PENDING, "ck_registered_epoch_attestation_transactions_scope"): _SCOPE,
    (_PENDING, "ck_registered_epoch_attestation_transactions_operation"): (
        "CHECK (octet_length(operation_id::text) >= 1 AND octet_length(operation_id::text) <= 256)"
    ),
    (_PENDING, "ck_registered_epoch_attestation_transactions_subject"): _SUBJECT,
    (_PENDING, "ck_registered_epoch_attestation_transactions_active"): (
        "CHECK (expected_active_epoch_id IS NULL AND expected_active_revision IS NULL OR "
        "expected_active_epoch_id::text ~ '^[0-9a-f]{64}$'::text AND "
        f"expected_active_revision >= 1 AND expected_active_revision <= {_SAFE})"
    ),
    (_PENDING, "ck_registered_epoch_attestation_transactions_authority"): (
        "CHECK (initiating_actor::text ~ '^keycloak-human:v1:[0-9a-f]{64}$'::text AND "
        "authority_profile_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (_PENDING, "ck_registered_epoch_attestation_transactions_time"): (
        "CHECK (isfinite(issued_at) AND isfinite(expires_at) AND expires_at > issued_at "
        "AND expires_at <= (issued_at + '00:05:00'::interval))"
    ),
    (_REVIEWS, "ck_registered_epoch_reviews_scope"): _SCOPE,
    (_REVIEWS, "ck_registered_epoch_reviews_operations"): (
        "CHECK (octet_length(operation_id::text) >= 1 AND octet_length(operation_id::text) <= 256 "
        "AND octet_length(registration_operation_id::text) >= 1 AND "
        "octet_length(registration_operation_id::text) <= 256)"
    ),
    (_REVIEWS, "ck_registered_epoch_reviews_subject"): _SUBJECT,
    (_REVIEWS, "ck_registered_epoch_reviews_base_shape"): (
        "CHECK (base_epoch_id IS NULL AND base_revision IS NULL OR "
        "base_epoch_id::text ~ '^[0-9a-f]{64}$'::text AND base_revision >= 1 AND "
        f"base_revision <= {_SAFE})"
    ),
    (_REVIEWS, "ck_registered_epoch_reviews_diff_evidence"): (
        "CHECK (semantic_diff_version::text = 'policy-semantic-diff/v1'::text AND "
        "octet_length(semantic_diff_canonical_json) >= 1 AND "
        "octet_length(semantic_diff_canonical_json) <= 65536 AND "
        "semantic_diff_hash::text ~ '^[0-9a-f]{64}$'::text AND "
        "changed_pointer_count >= 0 AND changed_pointer_count <= 1024)"
    ),
    (_REVIEWS, "ck_registered_epoch_reviews_audit_identity"): (
        "CHECK (audit_event_id::text ~ '^audit_[0-9a-f]{32}$'::text AND "
        "octet_length(audit_input_hash) = 32)"
    ),
    (_REVIEWS, "ck_registered_epoch_reviews_attestation_identity"): (
        "CHECK (octet_length(attestation_transaction_digest) = 32 AND "
        "octet_length(session_handle_digest) = 32 AND "
        "initiating_actor::text ~ '^keycloak-human:v1:[0-9a-f]{64}$'::text AND "
        f"reviewer_user_id >= 1 AND reviewer_user_id <= {_SAFE} AND "
        "reviewer_login::text ~ "
        "'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$'::text AND "
        "reviewer_permission::text ~ '^(admin|maintain)$'::text)"
    ),
    (_REVIEWS, "ck_registered_epoch_reviews_attestation_time"): (
        "CHECK (isfinite(attestation_issued_at) AND isfinite(permission_observed_at) AND "
        "isfinite(receipt_expires_at) AND permission_observed_at >= attestation_issued_at AND "
        "receipt_expires_at > permission_observed_at AND "
        "receipt_expires_at <= (attestation_issued_at + '00:05:00'::interval) AND "
        "authority_profile_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
}
_FOREIGN: Final = {
    (_PENDING, "fk_registered_epoch_attestation_transactions_session"): (
        "FOREIGN KEY (session_handle_digest) REFERENCES "
        "ci_coordinator.control_plane_sessions(handle_digest) ON DELETE CASCADE"
    ),
    (_PENDING, "fk_registered_epoch_attestation_transactions_target_epoch"): _epoch_reference(
        "target_epoch_id"
    ),
    (_PENDING, "fk_registered_epoch_attestation_transactions_active_epoch"): _epoch_reference(
        "expected_active_epoch_id"
    ),
    (_REVIEWS, "fk_registered_epoch_reviews_target_epoch"): _epoch_reference("target_epoch_id"),
    (_REVIEWS, "fk_registered_epoch_reviews_base_epoch"): _epoch_reference("base_epoch_id"),
    (_REVIEWS, "fk_registered_epoch_reviews_registration"): (
        "FOREIGN KEY (installation_id, repository_id, registration_operation_id) REFERENCES "
        "ci_coordinator.config_epoch_registrations(installation_id, repository_id, "
        "operation_id) ON DELETE RESTRICT"
    ),
    (_REVIEWS, "fk_registered_epoch_reviews_audit_event"): (
        "FOREIGN KEY (audit_event_id) REFERENCES ci_coordinator.audit_events(audit_event_id) "
        "ON DELETE RESTRICT"
    ),
}
_PRIMARY: Final = {
    (_PENDING, "pk_registered_epoch_attestation_transactions"): "PRIMARY KEY (transaction_digest)",
    (_REVIEWS, "pk_registered_epoch_reviews"): (
        "PRIMARY KEY (installation_id, repository_id, operation_id)"
    ),
}
_UNIQUE: Final = {
    (_PENDING, "uq_registered_epoch_attestation_transactions_operation"): (
        "UNIQUE (installation_id, repository_id, operation_id)"
    ),
    (_REVIEWS, "uq_registered_epoch_reviews_audit_event"): "UNIQUE (audit_event_id)",
    (_REVIEWS, "uq_registered_epoch_reviews_attestation_transaction"): (
        "UNIQUE (attestation_transaction_digest)"
    ),
}
_INDEXES: Final = {
    (_PENDING, "ix_registered_epoch_attestation_transactions_expiry"): (
        "CREATE INDEX ix_registered_epoch_attestation_transactions_expiry ON "
        "ci_coordinator.registered_epoch_attestation_transactions USING btree "
        "(expires_at, transaction_digest)"
    ),
    (_PENDING, "ix_registered_epoch_attestation_transactions_session"): (
        "CREATE INDEX ix_registered_epoch_attestation_transactions_session ON "
        "ci_coordinator.registered_epoch_attestation_transactions USING btree "
        "(session_handle_digest, expires_at)"
    ),
}

REGISTERED_EPOCH_REVIEW_CATALOG: Final = EconomicsCatalogContract(
    relations=_RELATIONS,
    columns=tuple(
        (table, name, kind, (table, name) in _NULLABLE, None)
        for table in _RELATIONS
        for name, kind in _SHAPES[table]
    ),
    constraints=frozenset(
        (table, name, kind, False, False, True, True)
        for definitions, kind in ((_CHECKS, "c"), (_FOREIGN, "f"), (_PRIMARY, "p"), (_UNIQUE, "u"))
        for table, name in definitions
    ),
    constraint_definitions={**_CHECKS, **_FOREIGN, **_PRIMARY, **_UNIQUE},
    indexes=frozenset(
        (table, name, False, False, True, True, True, False) for table, name in _INDEXES
    ),
    index_definitions=_INDEXES,
    tables=tuple((table, "r", "p", False, False, True, True) for table in _RELATIONS),
    triggers=(
        (
            _REVIEWS,
            "tr_registered_epoch_reviews_immutable",
            "reject_proposal_review_mutation",
            "ci_coordinator",
            False,
            "O",
            27,
            True,
            True,
            0,
            0,
            "",
            True,
            True,
            False,
            False,
        ),
    ),
    rewrite_rules=(),
    routine_source=None,
)


def registered_epoch_review_schema_matches_sync(connection: Connection) -> bool:
    return ci_economics_schema_matches_contract_sync(
        connection,
        contract=REGISTERED_EPOCH_REVIEW_CATALOG,
    )


async def registered_epoch_review_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(registered_epoch_review_schema_matches_sync)
