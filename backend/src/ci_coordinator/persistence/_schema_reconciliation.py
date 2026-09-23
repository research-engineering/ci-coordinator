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
    _shadow_reconciliation_state_profile,
    metadata,
)

reconciliation_subjects = Table(
    "reconciliation_subjects",
    metadata,
    Column("subject_id", String(64), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("subject_canonical_json", LargeBinary, nullable=False),
    Column("contract_canonical_json", LargeBinary, nullable=False),
    Column("identity_hash", String(64), nullable=False),
    Column("contract_hash", String(64), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("deadline_at", DateTime(timezone=True), nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("attempt_count", BigInteger, nullable=False),
    Column("max_attempts", BigInteger, nullable=False),
    Column("backoff_seconds", BigInteger, nullable=False),
    Column("max_backoff_seconds", BigInteger, nullable=False),
    Column("claim_generation", BigInteger, nullable=False),
    Column(
        "lease_token",
        String(_shadow_reconciliation_state_profile.reconciliation_claim_token_utf8_bytes),
        nullable=True,
    ),
    Column("lease_acquired_at", DateTime(timezone=True), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("execution_origin", String(8), nullable=False, server_default="legacy"),
    Column("production_generation", BigInteger),
    Column("production_authority_id", String(53)),
    CheckConstraint(
        "(execution_origin IN ('legacy', 'full_ci') AND "
        "production_generation IS NULL AND production_authority_id IS NULL) OR "
        "(execution_origin = 'selected' AND production_generation IS NOT NULL AND "
        f"production_generation BETWEEN 1 AND {_safe_integer_max} AND "
        "production_authority_id IS NOT NULL)",
        name="ck_reconciliation_subjects_production_origin",
    ),
    ForeignKeyConstraint(
        ("installation_id", "repository_id", "production_authority_id", "production_generation"),
        (
            f"{APPLICATION_SCHEMA}.production_staged_grants.installation_id",
            f"{APPLICATION_SCHEMA}.production_staged_grants.repository_id",
            f"{APPLICATION_SCHEMA}.production_staged_grants.authority_id",
            f"{APPLICATION_SCHEMA}.production_staged_grants.generation",
        ),
        name="fk_reconciliation_subjects_production_generation",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "subject_id ~ '^[0-9a-f]{64}$' AND identity_hash = subject_id AND "
        "contract_hash ~ '^[0-9a-f]{64}$'",
        name="ck_reconciliation_subjects_hashes",
    ),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"repository_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_reconciliation_subjects_scope_safe",
    ),
    CheckConstraint(
        "octet_length(subject_canonical_json) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.reconciliation_subject_canonical_bytes} AND "
        "octet_length(contract_canonical_json) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.reconciliation_contract_canonical_bytes}",
        name="ck_reconciliation_subjects_canonical_byte_limits",
    ),
    CheckConstraint(
        f"revision >= 0 AND revision <= {_safe_integer_max}",
        name="ck_reconciliation_subjects_revision_safe",
    ),
    CheckConstraint(
        "deadline_at > created_at AND next_attempt_at >= created_at AND "
        "next_attempt_at <= deadline_at AND deadline_at <= created_at + "
        f"INTERVAL '{_shadow_reconciliation_state_profile.reconciliation_max_deadline_seconds} "
        "seconds'",
        name="ck_reconciliation_subjects_convergence_lifetime",
    ),
    CheckConstraint(
        "attempt_count >= 0 AND attempt_count <= max_attempts AND max_attempts BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.reconciliation_max_attempts} AND "
        "backoff_seconds BETWEEN 1 AND max_backoff_seconds AND max_backoff_seconds BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.reconciliation_max_backoff_seconds} AND "
        f"claim_generation >= attempt_count AND claim_generation <= {_safe_integer_max}",
        name="ck_reconciliation_subjects_convergence_bounds",
    ),
    CheckConstraint(
        "(lease_token IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
        "(lease_token IS NOT NULL AND lease_token ~ '^[0-9a-f]{64}$' AND "
        "lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL AND "
        "lease_acquired_at >= created_at AND "
        "lease_expires_at > lease_acquired_at AND lease_expires_at <= lease_acquired_at + "
        f"INTERVAL '{_shadow_reconciliation_state_profile.reconciliation_max_lease_seconds} "
        "seconds')",
        name="ck_reconciliation_subjects_lease_shape",
    ),
    PrimaryKeyConstraint("subject_id", name="pk_reconciliation_subjects"),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_reconciliation_subjects_due",
    reconciliation_subjects.c.next_attempt_at,
    reconciliation_subjects.c.subject_id,
)

Index(
    "ix_reconciliation_subjects_scope_created",
    reconciliation_subjects.c.installation_id,
    reconciliation_subjects.c.repository_id,
    reconciliation_subjects.c.created_at,
    reconciliation_subjects.c.subject_id,
)

Index(
    "ix_reconciliation_subjects_production_drain",
    reconciliation_subjects.c.installation_id,
    reconciliation_subjects.c.repository_id,
    reconciliation_subjects.c.subject_id,
    postgresql_where=reconciliation_subjects.c.execution_origin != "full_ci",
)

reconciliation_observations = Table(
    "reconciliation_observations",
    metadata,
    Column("subject_id", String(64), nullable=False),
    Column(
        "observation_id",
        String(_shadow_reconciliation_state_profile.reconciliation_observation_id_utf8_bytes),
        nullable=False,
    ),
    Column("revision", BigInteger, nullable=False),
    Column("observation_canonical_json", LargeBinary, nullable=False),
    Column("semantic_hash", String(64), nullable=False),
    CheckConstraint(
        "subject_id ~ '^[0-9a-f]{64}$' AND octet_length(observation_id) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.reconciliation_observation_id_utf8_bytes}",
        name="ck_reconciliation_observations_identity_limits",
    ),
    CheckConstraint(
        f"revision >= 1 AND revision <= {_safe_integer_max}",
        name="ck_reconciliation_observations_revision_safe",
    ),
    CheckConstraint(
        "octet_length(observation_canonical_json) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.reconciliation_observation_canonical_bytes}",
        name="ck_reconciliation_observations_canonical_byte_limit",
    ),
    CheckConstraint(
        "semantic_hash ~ '^[0-9a-f]{64}$'",
        name="ck_reconciliation_observations_semantic_hash",
    ),
    PrimaryKeyConstraint("subject_id", "observation_id", name="pk_reconciliation_observations"),
    UniqueConstraint(
        "subject_id",
        "revision",
        name="uq_reconciliation_observations_subject_revision",
    ),
    ForeignKeyConstraint(
        ["subject_id"],
        [f"{APPLICATION_SCHEMA}.reconciliation_subjects.subject_id"],
        ondelete="RESTRICT",
        name="fk_reconciliation_observations_subject",
    ),
    schema=APPLICATION_SCHEMA,
)

reconciliation_results = Table(
    "reconciliation_results",
    metadata,
    Column("subject_id", String(64), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("result_canonical_json", LargeBinary, nullable=False),
    Column("semantic_hash", String(64), nullable=False),
    CheckConstraint(
        "subject_id ~ '^[0-9a-f]{64}$'",
        name="ck_reconciliation_results_subject_hash",
    ),
    CheckConstraint(
        f"revision >= 0 AND revision <= {_safe_integer_max}",
        name="ck_reconciliation_results_revision_safe",
    ),
    CheckConstraint(
        "octet_length(result_canonical_json) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.reconciliation_result_canonical_bytes}",
        name="ck_reconciliation_results_canonical_byte_limit",
    ),
    CheckConstraint(
        "semantic_hash ~ '^[0-9a-f]{64}$'",
        name="ck_reconciliation_results_semantic_hash",
    ),
    PrimaryKeyConstraint("subject_id", "revision", name="pk_reconciliation_results"),
    ForeignKeyConstraint(
        ["subject_id"],
        [f"{APPLICATION_SCHEMA}.reconciliation_subjects.subject_id"],
        ondelete="RESTRICT",
        name="fk_reconciliation_results_subject",
    ),
    schema=APPLICATION_SCHEMA,
)
