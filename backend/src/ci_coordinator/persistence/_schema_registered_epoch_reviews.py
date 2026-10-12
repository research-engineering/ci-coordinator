from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
)

from ci_coordinator.persistence._schema_core import (
    APPLICATION_SCHEMA,
    _safe_integer_max,
    metadata,
)
from ci_coordinator.proposal_review import (
    MAX_CHANGED_POINTERS,
    MAX_SEMANTIC_DIFF_BYTES,
    SEMANTIC_DIFF_VERSION,
)


def _config_epoch_reference(column: str, name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["installation_id", "repository_id", column],
        [
            f"{APPLICATION_SCHEMA}.config_epochs.installation_id",
            f"{APPLICATION_SCHEMA}.config_epochs.repository_id",
            f"{APPLICATION_SCHEMA}.config_epochs.epoch_id",
        ],
        ondelete="RESTRICT",
        name=name,
    )


registered_epoch_attestation_transactions = Table(
    "registered_epoch_attestation_transactions",
    metadata,
    Column("transaction_digest", LargeBinary(32), nullable=False),
    Column("session_handle_digest", LargeBinary(32), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("operation_id", String(256), nullable=False),
    Column("target_epoch_id", String(64), nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column("provider_revision", String(40), nullable=False),
    Column("inventory_digest", String(64), nullable=False),
    Column("expected_active_epoch_id", String(64)),
    Column("expected_active_revision", BigInteger),
    Column("initiating_actor", String(82), nullable=False),
    Column("authority_profile_digest", String(64), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "octet_length(transaction_digest) = 32 AND octet_length(session_handle_digest) = 32",
        name="ck_registered_epoch_attestation_transactions_digests",
    ),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max} "
        f"AND repository_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_registered_epoch_attestation_transactions_scope",
    ),
    CheckConstraint(
        "octet_length(operation_id) BETWEEN 1 AND 256",
        name="ck_registered_epoch_attestation_transactions_operation",
    ),
    CheckConstraint(
        "target_epoch_id ~ '^[0-9a-f]{64}$' AND source_hash ~ '^[0-9a-f]{64}$' "
        "AND provider_revision ~ '^[0-9a-f]{40}$' AND inventory_digest ~ '^[0-9a-f]{64}$'",
        name="ck_registered_epoch_attestation_transactions_subject",
    ),
    CheckConstraint(
        "(expected_active_epoch_id IS NULL AND expected_active_revision IS NULL) OR "
        "(expected_active_epoch_id ~ '^[0-9a-f]{64}$' "
        f"AND expected_active_revision BETWEEN 1 AND {_safe_integer_max})",
        name="ck_registered_epoch_attestation_transactions_active",
    ),
    CheckConstraint(
        "initiating_actor ~ '^keycloak-human:v1:[0-9a-f]{64}$' "
        "AND authority_profile_digest ~ '^[0-9a-f]{64}$'",
        name="ck_registered_epoch_attestation_transactions_authority",
    ),
    CheckConstraint(
        "isfinite(issued_at) AND isfinite(expires_at) "
        "AND expires_at > issued_at "
        "AND expires_at <= issued_at + INTERVAL '300 seconds'",
        name="ck_registered_epoch_attestation_transactions_time",
    ),
    PrimaryKeyConstraint(
        "transaction_digest",
        name="pk_registered_epoch_attestation_transactions",
    ),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "operation_id",
        name="uq_registered_epoch_attestation_transactions_operation",
    ),
    ForeignKeyConstraint(
        ["session_handle_digest"],
        [f"{APPLICATION_SCHEMA}.control_plane_sessions.handle_digest"],
        ondelete="CASCADE",
        name="fk_registered_epoch_attestation_transactions_session",
    ),
    _config_epoch_reference(
        "target_epoch_id",
        "fk_registered_epoch_attestation_transactions_target_epoch",
    ),
    _config_epoch_reference(
        "expected_active_epoch_id",
        "fk_registered_epoch_attestation_transactions_active_epoch",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_registered_epoch_attestation_transactions_expiry",
    registered_epoch_attestation_transactions.c.expires_at,
    registered_epoch_attestation_transactions.c.transaction_digest,
)
Index(
    "ix_registered_epoch_attestation_transactions_session",
    registered_epoch_attestation_transactions.c.session_handle_digest,
    registered_epoch_attestation_transactions.c.expires_at,
)

registered_epoch_reviews = Table(
    "registered_epoch_reviews",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("operation_id", String(256), nullable=False),
    Column("target_epoch_id", String(64), nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column("registration_operation_id", String(256), nullable=False),
    Column("provider_revision", String(40), nullable=False),
    Column("inventory_digest", String(64), nullable=False),
    Column("base_epoch_id", String(64), nullable=True),
    Column("base_revision", BigInteger, nullable=True),
    Column("semantic_diff_version", String(64), nullable=False),
    Column("semantic_diff_canonical_json", LargeBinary, nullable=False),
    Column("semantic_diff_hash", String(64), nullable=False),
    Column("changed_pointer_count", SmallInteger, nullable=False),
    Column("attestation_transaction_digest", LargeBinary(32), nullable=False),
    Column("session_handle_digest", LargeBinary(32), nullable=False),
    Column("initiating_actor", String(82), nullable=False),
    Column("reviewer_user_id", BigInteger, nullable=False),
    Column("reviewer_login", String(39), nullable=False),
    Column("reviewer_permission", String(8), nullable=False),
    Column("attestation_issued_at", DateTime(timezone=True), nullable=False),
    Column("permission_observed_at", DateTime(timezone=True), nullable=False),
    Column("receipt_expires_at", DateTime(timezone=True), nullable=False),
    Column("authority_profile_digest", String(64), nullable=False),
    Column("audit_event_id", String(38), nullable=False),
    Column("audit_input_hash", LargeBinary(32), nullable=False),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max} "
        f"AND repository_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_registered_epoch_reviews_scope",
    ),
    CheckConstraint(
        "octet_length(operation_id) BETWEEN 1 AND 256 "
        "AND octet_length(registration_operation_id) BETWEEN 1 AND 256",
        name="ck_registered_epoch_reviews_operations",
    ),
    CheckConstraint(
        "target_epoch_id ~ '^[0-9a-f]{64}$' AND source_hash ~ '^[0-9a-f]{64}$' "
        "AND provider_revision ~ '^[0-9a-f]{40}$' AND inventory_digest ~ '^[0-9a-f]{64}$'",
        name="ck_registered_epoch_reviews_subject",
    ),
    CheckConstraint(
        "(base_epoch_id IS NULL AND base_revision IS NULL) OR "
        f"(base_epoch_id ~ '^[0-9a-f]{{64}}$' AND base_revision BETWEEN 1 AND {_safe_integer_max})",
        name="ck_registered_epoch_reviews_base_shape",
    ),
    CheckConstraint(
        f"semantic_diff_version = '{SEMANTIC_DIFF_VERSION}' "
        f"AND octet_length(semantic_diff_canonical_json) BETWEEN 1 AND {MAX_SEMANTIC_DIFF_BYTES} "
        "AND semantic_diff_hash ~ '^[0-9a-f]{64}$' "
        f"AND changed_pointer_count BETWEEN 0 AND {MAX_CHANGED_POINTERS}",
        name="ck_registered_epoch_reviews_diff_evidence",
    ),
    CheckConstraint(
        "audit_event_id ~ '^audit_[0-9a-f]{32}$' AND octet_length(audit_input_hash) = 32",
        name="ck_registered_epoch_reviews_audit_identity",
    ),
    CheckConstraint(
        "octet_length(attestation_transaction_digest) = 32 "
        "AND octet_length(session_handle_digest) = 32 "
        "AND initiating_actor ~ '^keycloak-human:v1:[0-9a-f]{64}$' "
        f"AND reviewer_user_id BETWEEN 1 AND {_safe_integer_max} "
        "AND reviewer_login ~ '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$' "
        "AND reviewer_permission ~ '^(admin|maintain)$'",
        name="ck_registered_epoch_reviews_attestation_identity",
    ),
    CheckConstraint(
        "isfinite(attestation_issued_at) AND isfinite(permission_observed_at) "
        "AND isfinite(receipt_expires_at) "
        "AND permission_observed_at >= attestation_issued_at "
        "AND receipt_expires_at > permission_observed_at "
        "AND receipt_expires_at <= attestation_issued_at + INTERVAL '300 seconds' "
        "AND authority_profile_digest ~ '^[0-9a-f]{64}$'",
        name="ck_registered_epoch_reviews_attestation_time",
    ),
    PrimaryKeyConstraint(
        "installation_id",
        "repository_id",
        "operation_id",
        name="pk_registered_epoch_reviews",
    ),
    UniqueConstraint("audit_event_id", name="uq_registered_epoch_reviews_audit_event"),
    UniqueConstraint(
        "attestation_transaction_digest",
        name="uq_registered_epoch_reviews_attestation_transaction",
    ),
    _config_epoch_reference("target_epoch_id", "fk_registered_epoch_reviews_target_epoch"),
    _config_epoch_reference("base_epoch_id", "fk_registered_epoch_reviews_base_epoch"),
    ForeignKeyConstraint(
        ["installation_id", "repository_id", "registration_operation_id"],
        [
            f"{APPLICATION_SCHEMA}.config_epoch_registrations.installation_id",
            f"{APPLICATION_SCHEMA}.config_epoch_registrations.repository_id",
            f"{APPLICATION_SCHEMA}.config_epoch_registrations.operation_id",
        ],
        ondelete="RESTRICT",
        name="fk_registered_epoch_reviews_registration",
    ),
    ForeignKeyConstraint(
        ["audit_event_id"],
        [f"{APPLICATION_SCHEMA}.audit_events.audit_event_id"],
        ondelete="RESTRICT",
        name="fk_registered_epoch_reviews_audit_event",
    ),
    schema=APPLICATION_SCHEMA,
)
