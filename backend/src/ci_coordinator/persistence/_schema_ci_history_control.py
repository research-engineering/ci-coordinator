from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Table,
)

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

ci_history_defaults = Table(
    "ci_history_defaults",
    metadata,
    Column("singleton", Boolean, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("detail_policy_canonical", LargeBinary, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("singleton", name="pk_ci_history_defaults"),
    CheckConstraint("singleton IS TRUE", name="ck_ci_history_defaults_singleton"),
    CheckConstraint(
        "revision BETWEEN 1 AND 9007199254740991", name="ck_ci_history_defaults_revision"
    ),
    CheckConstraint(
        "octet_length(detail_policy_canonical) BETWEEN 1 AND 1024",
        name="ck_ci_history_defaults_payload",
    ),
    schema=APPLICATION_SCHEMA,
)

ci_history_datasets = Table(
    "ci_history_datasets",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("configuration_revision", BigInteger, nullable=False),
    Column("data_revision", BigInteger, nullable=False),
    Column("configured_at", DateTime(timezone=True), nullable=False),
    Column("state", String(16), nullable=False),
    Column("configuration_canonical", LargeBinary, nullable=False),
    Column("attempt_count", BigInteger, nullable=False),
    Column("job_count", BigInteger, nullable=False),
    Column("gap_count", BigInteger, nullable=False),
    Column("canonical_bytes", BigInteger, nullable=False),
    PrimaryKeyConstraint("installation_id", "repository_id", name="pk_ci_history_datasets"),
    CheckConstraint(
        "installation_id BETWEEN 1 AND 9007199254740991 AND "
        "repository_id BETWEEN 1 AND 9007199254740991 AND "
        "generation BETWEEN 1 AND 9007199254740991 AND "
        "configuration_revision BETWEEN 1 AND 9007199254740991 AND "
        "data_revision BETWEEN 1 AND 9007199254740991",
        name="ck_ci_history_datasets_identity",
    ),
    CheckConstraint(
        "state IN ('active', 'paused', 'erasing', 'erased')",
        name="ck_ci_history_datasets_state",
    ),
    CheckConstraint(
        "attempt_count BETWEEN 0 AND 9007199254740991 AND "
        "job_count BETWEEN 0 AND 9007199254740991 AND "
        "gap_count BETWEEN 0 AND 9007199254740991 AND "
        "canonical_bytes BETWEEN 0 AND 9007199254740991",
        name="ck_ci_history_datasets_usage",
    ),
    CheckConstraint(
        "octet_length(configuration_canonical) BETWEEN 1 AND 4096",
        name="ck_ci_history_datasets_payload",
    ),
    schema=APPLICATION_SCHEMA,
)

ci_history_scans = Table(
    "ci_history_scans",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("lane", String(16), nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("configuration_revision", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("state_canonical", LargeBinary, nullable=False),
    Column("traversal_complete", Boolean, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("lease_worker_id", String(64)),
    Column("lease_token", String(64)),
    Column("lease_acquired_at", DateTime(timezone=True)),
    Column("lease_expires_at", DateTime(timezone=True)),
    PrimaryKeyConstraint("installation_id", "repository_id", "lane", name="pk_ci_history_scans"),
    CheckConstraint("lane IN ('backfill', 'discovery')", name="ck_ci_history_scans_lane"),
    ForeignKeyConstraint(
        ["installation_id", "repository_id"],
        [
            f"{APPLICATION_SCHEMA}.ci_history_datasets.installation_id",
            f"{APPLICATION_SCHEMA}.ci_history_datasets.repository_id",
        ],
        name="fk_ci_history_scans_dataset",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "generation BETWEEN 1 AND 9007199254740991 AND "
        "configuration_revision BETWEEN 1 AND 9007199254740991 AND "
        "revision BETWEEN 1 AND 9007199254740991",
        name="ck_ci_history_scans_revision",
    ),
    CheckConstraint(
        "octet_length(state_canonical) BETWEEN 1 AND 65536",
        name="ck_ci_history_scans_payload",
    ),
    CheckConstraint(
        "(lease_worker_id IS NULL AND lease_token IS NULL AND "
        "lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
        "(lease_worker_id IS NOT NULL AND lease_token IS NOT NULL AND "
        "lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL AND "
        "lease_worker_id ~ '^[0-9a-f]{64}$' AND lease_token ~ '^[0-9a-f]{64}$' AND "
        "lease_acquired_at < lease_expires_at)",
        name="ck_ci_history_scans_lease",
    ),
    schema=APPLICATION_SCHEMA,
)

ci_history_gaps = Table(
    "ci_history_gaps",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("gap_id", String(64), nullable=False),
    Column("gap_canonical", LargeBinary, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint(
        "installation_id", "repository_id", "generation", "gap_id", name="pk_ci_history_gaps"
    ),
    ForeignKeyConstraint(
        ["installation_id", "repository_id"],
        [
            f"{APPLICATION_SCHEMA}.ci_history_datasets.installation_id",
            f"{APPLICATION_SCHEMA}.ci_history_datasets.repository_id",
        ],
        name="fk_ci_history_gaps_dataset",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "generation BETWEEN 1 AND 9007199254740991 AND gap_id ~ '^[0-9a-f]{64}$'",
        name="ck_ci_history_gaps_identity",
    ),
    CheckConstraint(
        "octet_length(gap_canonical) BETWEEN 1 AND 4096", name="ck_ci_history_gaps_payload"
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_ci_history_scans_due",
    ci_history_scans.c.lane,
    ci_history_scans.c.next_attempt_at,
    ci_history_scans.c.installation_id,
    ci_history_scans.c.repository_id,
)

ci_history_rechecks = Table(
    "ci_history_rechecks",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("workflow_run_id", BigInteger, nullable=False),
    Column("workflow_id", BigInteger, nullable=False),
    Column("source", String(8), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("state_canonical", LargeBinary, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("acquisition_count", BigInteger, nullable=False),
    Column("lease_worker_id", String(64)),
    Column("lease_token", String(64)),
    Column("lease_acquired_at", DateTime(timezone=True)),
    Column("lease_expires_at", DateTime(timezone=True)),
    PrimaryKeyConstraint(
        "installation_id",
        "repository_id",
        "generation",
        "workflow_run_id",
        name="pk_ci_history_rechecks",
    ),
    ForeignKeyConstraint(
        ["installation_id", "repository_id"],
        [
            f"{APPLICATION_SCHEMA}.ci_history_datasets.installation_id",
            f"{APPLICATION_SCHEMA}.ci_history_datasets.repository_id",
        ],
        name="fk_ci_history_rechecks_dataset",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "generation BETWEEN 1 AND 9007199254740991 AND "
        "workflow_run_id BETWEEN 1 AND 9007199254740991 AND "
        "workflow_id BETWEEN 1 AND 9007199254740991 AND "
        "revision BETWEEN 1 AND 9007199254740991",
        name="ck_ci_history_rechecks_identity",
    ),
    CheckConstraint("source IN ('recent', 'repair')", name="ck_ci_history_rechecks_source"),
    CheckConstraint(
        "acquisition_count BETWEEN 0 AND 3 AND revision > acquisition_count AND "
        "(acquisition_count < 3 OR lease_expires_at IS NOT NULL)",
        name="ck_ci_history_rechecks_attempts",
    ),
    CheckConstraint(
        "octet_length(state_canonical) BETWEEN 1 AND 4096",
        name="ck_ci_history_rechecks_payload",
    ),
    CheckConstraint(
        "(lease_worker_id IS NULL AND lease_token IS NULL AND "
        "lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
        "(lease_worker_id IS NOT NULL AND lease_token IS NOT NULL AND "
        "lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL AND "
        "lease_worker_id ~ '^[0-9a-f]{64}$' AND lease_token ~ '^[0-9a-f]{64}$' AND "
        "acquisition_count >= 1 AND next_attempt_at <= lease_acquired_at AND "
        "lease_expires_at - lease_acquired_at = interval '60 seconds')",
        name="ck_ci_history_rechecks_lease",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_ci_history_rechecks_due",
    ci_history_rechecks.c.source,
    ci_history_rechecks.c.next_attempt_at,
    ci_history_rechecks.c.installation_id,
    ci_history_rechecks.c.repository_id,
    ci_history_rechecks.c.workflow_run_id,
)
