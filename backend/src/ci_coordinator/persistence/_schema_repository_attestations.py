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
    String,
    Table,
    UniqueConstraint,
)

from ci_coordinator.persistence._schema_core import (
    APPLICATION_SCHEMA,
    _safe_integer_max,
    metadata,
)

repository_attestation_transactions = Table(
    "repository_attestation_transactions",
    metadata,
    Column("transaction_digest", LargeBinary(32), nullable=False),
    Column("session_handle_digest", LargeBinary(32), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("operation_id", String(256), nullable=False),
    Column("proposal_manifest_id", String(41), nullable=False),
    Column("provider_revision", String(40), nullable=False),
    Column("proposal_digest", String(64), nullable=False),
    Column("expected_active_epoch_id", String(64)),
    Column("expected_active_revision", BigInteger),
    Column("initiating_actor", String(82), nullable=False),
    Column("authority_profile_digest", String(64), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "octet_length(transaction_digest) = 32 AND octet_length(session_handle_digest) = 32",
        name="ck_repository_attestation_transactions_digests",
    ),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max} "
        f"AND repository_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_repository_attestation_transactions_scope",
    ),
    CheckConstraint(
        "octet_length(operation_id) BETWEEN 1 AND 256 "
        "AND proposal_manifest_id ~ '^proposal:[0-9a-f]{32}$'",
        name="ck_repository_attestation_transactions_operation",
    ),
    CheckConstraint(
        "provider_revision ~ '^[0-9a-f]{40}$' AND proposal_digest ~ '^[0-9a-f]{64}$'",
        name="ck_repository_attestation_transactions_proposal",
    ),
    CheckConstraint(
        "(expected_active_epoch_id IS NULL AND expected_active_revision IS NULL) OR "
        "(expected_active_epoch_id ~ '^[0-9a-f]{64}$' "
        f"AND expected_active_revision BETWEEN 1 AND {_safe_integer_max})",
        name="ck_repository_attestation_transactions_active",
    ),
    CheckConstraint(
        "initiating_actor ~ '^keycloak-human:v1:[0-9a-f]{64}$' "
        "AND authority_profile_digest ~ '^[0-9a-f]{64}$'",
        name="ck_repository_attestation_transactions_authority",
    ),
    CheckConstraint(
        "isfinite(issued_at) AND isfinite(expires_at) "
        "AND expires_at > issued_at "
        "AND expires_at <= issued_at + INTERVAL '300 seconds'",
        name="ck_repository_attestation_transactions_time",
    ),
    PrimaryKeyConstraint(
        "transaction_digest",
        name="pk_repository_attestation_transactions",
    ),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "operation_id",
        name="uq_repository_attestation_transactions_operation",
    ),
    ForeignKeyConstraint(
        ["session_handle_digest"],
        [f"{APPLICATION_SCHEMA}.control_plane_sessions.handle_digest"],
        ondelete="CASCADE",
        name="fk_repository_attestation_transactions_session",
    ),
    ForeignKeyConstraint(
        ["installation_id", "repository_id", "expected_active_epoch_id"],
        [
            f"{APPLICATION_SCHEMA}.config_epochs.installation_id",
            f"{APPLICATION_SCHEMA}.config_epochs.repository_id",
            f"{APPLICATION_SCHEMA}.config_epochs.epoch_id",
        ],
        ondelete="RESTRICT",
        name="fk_repository_attestation_transactions_active_epoch",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_repository_attestation_transactions_expiry",
    repository_attestation_transactions.c.expires_at,
    repository_attestation_transactions.c.transaction_digest,
)
Index(
    "ix_repository_attestation_transactions_session",
    repository_attestation_transactions.c.session_handle_digest,
    repository_attestation_transactions.c.expires_at,
)
