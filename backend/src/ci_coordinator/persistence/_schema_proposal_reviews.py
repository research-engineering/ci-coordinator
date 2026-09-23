from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
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

workflow_proposal_reviews = Table(
    "workflow_proposal_reviews",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("operation_id", String(256), nullable=False),
    Column("proposal_manifest_id", String(41), nullable=False),
    Column("provider_revision", String(40), nullable=False),
    Column("inventory_digest", String(64), nullable=False),
    Column("proposal_digest", String(64), nullable=False),
    Column("base_epoch_id", String(64), nullable=True),
    Column("base_revision", BigInteger, nullable=True),
    Column("target_epoch_id", String(64), nullable=False),
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
        f"installation_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_workflow_proposal_reviews_installation_id_safe",
    ),
    CheckConstraint(
        f"repository_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_workflow_proposal_reviews_repository_id_safe",
    ),
    CheckConstraint(
        "octet_length(operation_id) BETWEEN 1 AND 256",
        name="ck_workflow_proposal_reviews_operation_id_limit",
    ),
    CheckConstraint(
        "proposal_manifest_id ~ '^proposal:[0-9a-f]{32}$'",
        name="ck_workflow_proposal_reviews_manifest_id",
    ),
    CheckConstraint(
        "provider_revision ~ '^[0-9a-f]{40}$' "
        "AND inventory_digest ~ '^[0-9a-f]{64}$' "
        "AND proposal_digest ~ '^[0-9a-f]{64}$'",
        name="ck_workflow_proposal_reviews_provider_identity",
    ),
    CheckConstraint(
        "(base_epoch_id IS NULL AND base_revision IS NULL) OR "
        f"(base_epoch_id ~ '^[0-9a-f]{{64}}$' AND base_revision BETWEEN 1 AND {_safe_integer_max})",
        name="ck_workflow_proposal_reviews_base_shape",
    ),
    CheckConstraint(
        "target_epoch_id ~ '^[0-9a-f]{64}$'",
        name="ck_workflow_proposal_reviews_target_epoch",
    ),
    CheckConstraint(
        f"semantic_diff_version = '{SEMANTIC_DIFF_VERSION}'",
        name="ck_workflow_proposal_reviews_diff_version",
    ),
    CheckConstraint(
        f"octet_length(semantic_diff_canonical_json) BETWEEN 1 AND {MAX_SEMANTIC_DIFF_BYTES} "
        "AND semantic_diff_hash ~ '^[0-9a-f]{64}$' "
        f"AND changed_pointer_count BETWEEN 0 AND {MAX_CHANGED_POINTERS}",
        name="ck_workflow_proposal_reviews_diff_evidence",
    ),
    CheckConstraint(
        "audit_event_id ~ '^audit_[0-9a-f]{32}$' AND octet_length(audit_input_hash) = 32",
        name="ck_workflow_proposal_reviews_audit_identity",
    ),
    CheckConstraint(
        "octet_length(attestation_transaction_digest) = 32 "
        "AND octet_length(session_handle_digest) = 32 "
        "AND initiating_actor ~ '^keycloak-human:v1:[0-9a-f]{64}$' "
        f"AND reviewer_user_id BETWEEN 1 AND {_safe_integer_max} "
        "AND reviewer_login ~ '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$' "
        "AND reviewer_permission ~ '^(admin|maintain)$'",
        name="ck_workflow_proposal_reviews_attestation_identity",
    ),
    CheckConstraint(
        "isfinite(attestation_issued_at) AND isfinite(permission_observed_at) "
        "AND isfinite(receipt_expires_at) "
        "AND permission_observed_at >= attestation_issued_at "
        "AND receipt_expires_at > permission_observed_at "
        "AND receipt_expires_at <= attestation_issued_at + INTERVAL '300 seconds' "
        "AND authority_profile_digest ~ '^[0-9a-f]{64}$'",
        name="ck_workflow_proposal_reviews_attestation_time",
    ),
    PrimaryKeyConstraint(
        "installation_id",
        "repository_id",
        "operation_id",
        name="pk_workflow_proposal_reviews",
    ),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "proposal_manifest_id",
        name="uq_workflow_proposal_reviews_scope_manifest",
    ),
    UniqueConstraint("audit_event_id", name="uq_workflow_proposal_reviews_audit_event"),
    UniqueConstraint(
        "attestation_transaction_digest",
        name="uq_workflow_proposal_reviews_attestation_transaction",
    ),
    ForeignKeyConstraint(
        ["installation_id", "repository_id", "base_epoch_id"],
        [
            f"{APPLICATION_SCHEMA}.config_epochs.installation_id",
            f"{APPLICATION_SCHEMA}.config_epochs.repository_id",
            f"{APPLICATION_SCHEMA}.config_epochs.epoch_id",
        ],
        ondelete="RESTRICT",
        name="fk_workflow_proposal_reviews_base_epoch",
    ),
    ForeignKeyConstraint(
        ["installation_id", "repository_id", "target_epoch_id"],
        [
            f"{APPLICATION_SCHEMA}.config_epochs.installation_id",
            f"{APPLICATION_SCHEMA}.config_epochs.repository_id",
            f"{APPLICATION_SCHEMA}.config_epochs.epoch_id",
        ],
        ondelete="RESTRICT",
        name="fk_workflow_proposal_reviews_target_epoch",
    ),
    ForeignKeyConstraint(
        ["audit_event_id"],
        [f"{APPLICATION_SCHEMA}.audit_events.audit_event_id"],
        ondelete="RESTRICT",
        name="fk_workflow_proposal_reviews_audit_event",
    ),
    schema=APPLICATION_SCHEMA,
)
