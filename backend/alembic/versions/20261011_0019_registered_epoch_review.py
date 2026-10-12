from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ci_coordinator.persistence.compatibility_contracts import build_declaration
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_protocol import apply_forward_declaration
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.review_renewal_migration import renewal_declarations
from ci_coordinator.persistence.schema_capabilities import (
    CONTROL_PLANE_LOGOUT_FENCE,
    REGISTERED_EPOCH_REVIEW,
)

revision: str = "20261011_0019"
down_revision: str | None = "20261011_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_SCHEMA = "ci_coordinator"
_SAFE_INTEGER_MAX = 9_007_199_254_740_991
_PENDING = "registered_epoch_attestation_transactions"
_REVIEWS = "registered_epoch_reviews"
_RENEWAL = renewal_declarations()[2]
# Exactly the published 20261011_0018 logout-fence declaration.
_PREDECESSOR = build_declaration(
    load_bundled_profile(),
    generation=_RENEWAL.generation + 1,
    revision_id="20261011_0018",
    parent_revision_id=_RENEWAL.revision_id,
    transition_kind="expand",
    capabilities=(*_RENEWAL.capabilities, CONTROL_PLANE_LOGOUT_FENCE.declaration()),
)
_SUCCESSOR = build_declaration(
    load_bundled_profile(),
    generation=_PREDECESSOR.generation + 1,
    revision_id=revision,
    parent_revision_id=_PREDECESSOR.revision_id,
    transition_kind="expand",
    capabilities=(*_PREDECESSOR.capabilities, REGISTERED_EPOCH_REVIEW.declaration()),
)


def _epoch_reference(column: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["installation_id", "repository_id", column],
        [
            f"{_SCHEMA}.config_epochs.installation_id",
            f"{_SCHEMA}.config_epochs.repository_id",
            f"{_SCHEMA}.config_epochs.epoch_id",
        ],
        ondelete="RESTRICT",
        name=name,
    )


def upgrade() -> None:
    op.create_table(
        _PENDING,
        sa.Column("transaction_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("session_handle_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("installation_id", sa.BigInteger, nullable=False),
        sa.Column("repository_id", sa.BigInteger, nullable=False),
        sa.Column("operation_id", sa.String(256), nullable=False),
        sa.Column("target_epoch_id", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("provider_revision", sa.String(40), nullable=False),
        sa.Column("inventory_digest", sa.String(64), nullable=False),
        sa.Column("expected_active_epoch_id", sa.String(64)),
        sa.Column("expected_active_revision", sa.BigInteger),
        sa.Column("initiating_actor", sa.String(82), nullable=False),
        sa.Column("authority_profile_digest", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "octet_length(transaction_digest) = 32 AND octet_length(session_handle_digest) = 32",
            name="ck_registered_epoch_attestation_transactions_digests",
        ),
        sa.CheckConstraint(
            f"installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} "
            f"AND repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}",
            name="ck_registered_epoch_attestation_transactions_scope",
        ),
        sa.CheckConstraint(
            "octet_length(operation_id) BETWEEN 1 AND 256",
            name="ck_registered_epoch_attestation_transactions_operation",
        ),
        sa.CheckConstraint(
            "target_epoch_id ~ '^[0-9a-f]{64}$' AND source_hash ~ '^[0-9a-f]{64}$' "
            "AND provider_revision ~ '^[0-9a-f]{40}$' AND inventory_digest ~ '^[0-9a-f]{64}$'",
            name="ck_registered_epoch_attestation_transactions_subject",
        ),
        sa.CheckConstraint(
            "(expected_active_epoch_id IS NULL AND expected_active_revision IS NULL) OR "
            "(expected_active_epoch_id ~ '^[0-9a-f]{64}$' "
            f"AND expected_active_revision BETWEEN 1 AND {_SAFE_INTEGER_MAX})",
            name="ck_registered_epoch_attestation_transactions_active",
        ),
        sa.CheckConstraint(
            "initiating_actor ~ '^keycloak-human:v1:[0-9a-f]{64}$' "
            "AND authority_profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_registered_epoch_attestation_transactions_authority",
        ),
        sa.CheckConstraint(
            "isfinite(issued_at) AND isfinite(expires_at) "
            "AND expires_at > issued_at "
            "AND expires_at <= issued_at + INTERVAL '300 seconds'",
            name="ck_registered_epoch_attestation_transactions_time",
        ),
        sa.PrimaryKeyConstraint(
            "transaction_digest",
            name="pk_registered_epoch_attestation_transactions",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "repository_id",
            "operation_id",
            name="uq_registered_epoch_attestation_transactions_operation",
        ),
        sa.ForeignKeyConstraint(
            ["session_handle_digest"],
            [f"{_SCHEMA}.control_plane_sessions.handle_digest"],
            ondelete="CASCADE",
            name="fk_registered_epoch_attestation_transactions_session",
        ),
        _epoch_reference(
            "target_epoch_id",
            "fk_registered_epoch_attestation_transactions_target_epoch",
        ),
        _epoch_reference(
            "expected_active_epoch_id",
            "fk_registered_epoch_attestation_transactions_active_epoch",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_registered_epoch_attestation_transactions_expiry",
        _PENDING,
        ["expires_at", "transaction_digest"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_registered_epoch_attestation_transactions_session",
        _PENDING,
        ["session_handle_digest", "expires_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        _REVIEWS,
        sa.Column("installation_id", sa.BigInteger, nullable=False),
        sa.Column("repository_id", sa.BigInteger, nullable=False),
        sa.Column("operation_id", sa.String(256), nullable=False),
        sa.Column("target_epoch_id", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("registration_operation_id", sa.String(256), nullable=False),
        sa.Column("provider_revision", sa.String(40), nullable=False),
        sa.Column("inventory_digest", sa.String(64), nullable=False),
        sa.Column("base_epoch_id", sa.String(64), nullable=True),
        sa.Column("base_revision", sa.BigInteger, nullable=True),
        sa.Column("semantic_diff_version", sa.String(64), nullable=False),
        sa.Column("semantic_diff_canonical_json", sa.LargeBinary, nullable=False),
        sa.Column("semantic_diff_hash", sa.String(64), nullable=False),
        sa.Column("changed_pointer_count", sa.SmallInteger, nullable=False),
        sa.Column("attestation_transaction_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("session_handle_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("initiating_actor", sa.String(82), nullable=False),
        sa.Column("reviewer_user_id", sa.BigInteger, nullable=False),
        sa.Column("reviewer_login", sa.String(39), nullable=False),
        sa.Column("reviewer_permission", sa.String(8), nullable=False),
        sa.Column("attestation_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("permission_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authority_profile_digest", sa.String(64), nullable=False),
        sa.Column("audit_event_id", sa.String(38), nullable=False),
        sa.Column("audit_input_hash", sa.LargeBinary(32), nullable=False),
        sa.CheckConstraint(
            f"installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} "
            f"AND repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}",
            name="ck_registered_epoch_reviews_scope",
        ),
        sa.CheckConstraint(
            "octet_length(operation_id) BETWEEN 1 AND 256 "
            "AND octet_length(registration_operation_id) BETWEEN 1 AND 256",
            name="ck_registered_epoch_reviews_operations",
        ),
        sa.CheckConstraint(
            "target_epoch_id ~ '^[0-9a-f]{64}$' AND source_hash ~ '^[0-9a-f]{64}$' "
            "AND provider_revision ~ '^[0-9a-f]{40}$' AND inventory_digest ~ '^[0-9a-f]{64}$'",
            name="ck_registered_epoch_reviews_subject",
        ),
        sa.CheckConstraint(
            "(base_epoch_id IS NULL AND base_revision IS NULL) OR "
            "(base_epoch_id ~ '^[0-9a-f]{64}$' "
            f"AND base_revision BETWEEN 1 AND {_SAFE_INTEGER_MAX})",
            name="ck_registered_epoch_reviews_base_shape",
        ),
        sa.CheckConstraint(
            "semantic_diff_version = 'policy-semantic-diff/v1' "
            "AND octet_length(semantic_diff_canonical_json) BETWEEN 1 AND 65536 "
            "AND semantic_diff_hash ~ '^[0-9a-f]{64}$' "
            "AND changed_pointer_count BETWEEN 0 AND 1024",
            name="ck_registered_epoch_reviews_diff_evidence",
        ),
        sa.CheckConstraint(
            "audit_event_id ~ '^audit_[0-9a-f]{32}$' AND octet_length(audit_input_hash) = 32",
            name="ck_registered_epoch_reviews_audit_identity",
        ),
        sa.CheckConstraint(
            "octet_length(attestation_transaction_digest) = 32 "
            "AND octet_length(session_handle_digest) = 32 "
            "AND initiating_actor ~ '^keycloak-human:v1:[0-9a-f]{64}$' "
            f"AND reviewer_user_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} "
            "AND reviewer_login ~ '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$' "
            "AND reviewer_permission ~ '^(admin|maintain)$'",
            name="ck_registered_epoch_reviews_attestation_identity",
        ),
        sa.CheckConstraint(
            "isfinite(attestation_issued_at) AND isfinite(permission_observed_at) "
            "AND isfinite(receipt_expires_at) "
            "AND permission_observed_at >= attestation_issued_at "
            "AND receipt_expires_at > permission_observed_at "
            "AND receipt_expires_at <= attestation_issued_at + INTERVAL '300 seconds' "
            "AND authority_profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_registered_epoch_reviews_attestation_time",
        ),
        sa.PrimaryKeyConstraint(
            "installation_id",
            "repository_id",
            "operation_id",
            name="pk_registered_epoch_reviews",
        ),
        sa.UniqueConstraint("audit_event_id", name="uq_registered_epoch_reviews_audit_event"),
        sa.UniqueConstraint(
            "attestation_transaction_digest",
            name="uq_registered_epoch_reviews_attestation_transaction",
        ),
        _epoch_reference("target_epoch_id", "fk_registered_epoch_reviews_target_epoch"),
        _epoch_reference("base_epoch_id", "fk_registered_epoch_reviews_base_epoch"),
        sa.ForeignKeyConstraint(
            ["installation_id", "repository_id", "registration_operation_id"],
            [
                f"{_SCHEMA}.config_epoch_registrations.installation_id",
                f"{_SCHEMA}.config_epoch_registrations.repository_id",
                f"{_SCHEMA}.config_epoch_registrations.operation_id",
            ],
            ondelete="RESTRICT",
            name="fk_registered_epoch_reviews_registration",
        ),
        sa.ForeignKeyConstraint(
            ["audit_event_id"],
            [f"{_SCHEMA}.audit_events.audit_event_id"],
            ondelete="RESTRICT",
            name="fk_registered_epoch_reviews_audit_event",
        ),
        schema=_SCHEMA,
    )
    # The review owner's existing immutability routine guards this second review subject.
    op.execute(
        sa.text(
            f"CREATE TRIGGER tr_registered_epoch_reviews_immutable "
            f"BEFORE UPDATE OR DELETE ON {_SCHEMA}.{_REVIEWS} "
            f"FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_proposal_review_mutation()"
        )
    )
    for table in (_PENDING, _REVIEWS):
        op.execute(sa.text(f"REVOKE ALL ON TABLE {_SCHEMA}.{table} FROM PUBLIC"))
        # PostgreSQL creates a separately privileged composite row type for every table.
        op.execute(sa.text(f"REVOKE ALL ON TYPE {_SCHEMA}.{table} FROM PUBLIC"))
    connection = op.get_bind()
    apply_forward_declaration(
        connection,
        load_bundled_profile(),
        previous_revision_id=_PREDECESSOR.revision_id,
        proposed=_SUCCESSOR,
        attest_resulting_capabilities=lambda admitted: attest_resulting_capabilities(
            connection, admitted
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Registered-epoch review requires forward repair or admitted restore")
