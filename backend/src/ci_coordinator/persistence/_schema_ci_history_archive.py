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

_PARENT_KEY = ("installation_id", "repository_id", "generation", "workflow_run_id", "run_attempt")
_PARENT_REFERENCES = tuple(
    f"{APPLICATION_SCHEMA}.ci_history_attempts.{name}" for name in _PARENT_KEY
)

ci_history_attempts = Table(
    "ci_history_attempts",
    metadata,
    *(Column(name, BigInteger, nullable=False) for name in _PARENT_KEY),
    Column("head_sha", String(40), nullable=False),
    Column("workflow_id", BigInteger, nullable=False),
    Column("run_created_at", DateTime(timezone=True), nullable=False),
    Column("header_canonical", LargeBinary, nullable=False),
    Column("statistics_digest", String(64), nullable=False),
    Column("job_count", BigInteger, nullable=False),
    Column("statistics_bytes", BigInteger, nullable=False),
    Column("has_conflict", Boolean, nullable=False),
    Column("first_imported_at", DateTime(timezone=True), nullable=False),
    Column("detail_state", String(16), nullable=False),
    Column("detail_first_imported_at", DateTime(timezone=True)),
    Column("detail_policy_canonical", LargeBinary),
    Column("detail_policy_source", String(32)),
    Column("detail_policy_revision", BigInteger),
    Column("detail_expires_at", DateTime(timezone=True)),
    PrimaryKeyConstraint(*_PARENT_KEY, name="pk_ci_history_attempts"),
    ForeignKeyConstraint(
        ["installation_id", "repository_id"],
        [
            f"{APPLICATION_SCHEMA}.ci_history_datasets.installation_id",
            f"{APPLICATION_SCHEMA}.ci_history_datasets.repository_id",
        ],
        name="fk_ci_history_attempts_dataset",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "generation BETWEEN 1 AND 9007199254740991 AND "
        "workflow_run_id BETWEEN 1 AND 9007199254740991 AND "
        "run_attempt BETWEEN 1 AND 9007199254740991 AND "
        "workflow_id BETWEEN 1 AND 9007199254740991 AND job_count BETWEEN 0 AND 2000",
        name="ck_ci_history_attempts_integers",
    ),
    CheckConstraint(
        "head_sha ~ '^[0-9a-f]{40}$' AND statistics_digest ~ '^[0-9a-f]{64}$'",
        name="ck_ci_history_attempts_digests",
    ),
    CheckConstraint(
        "octet_length(header_canonical) BETWEEN 1 AND 8192 AND "
        "statistics_bytes BETWEEN 1 AND 8388608",
        name="ck_ci_history_attempts_payload",
    ),
    CheckConstraint(
        "(detail_state = 'not_imported' AND detail_first_imported_at IS NULL AND "
        "detail_policy_canonical IS NULL AND detail_policy_source IS NULL AND "
        "detail_policy_revision IS NULL AND "
        "detail_expires_at IS NULL) OR "
        "(detail_state IN ('retained', 'expired') AND detail_first_imported_at IS NOT NULL AND "
        "detail_policy_canonical IS NOT NULL AND detail_policy_revision IS NOT NULL AND "
        "detail_policy_source IS NOT NULL AND "
        "detail_policy_source IN ('service_default', 'repository_override') AND "
        "detail_policy_revision BETWEEN 1 AND 9007199254740991 AND "
        "octet_length(detail_policy_canonical) BETWEEN 1 AND 1024 AND "
        "(detail_expires_at IS NULL OR detail_expires_at > detail_first_imported_at))",
        name="ck_ci_history_attempts_detail",
    ),
    schema=APPLICATION_SCHEMA,
)

ci_history_jobs = Table(
    "ci_history_jobs",
    metadata,
    *(Column(name, BigInteger, nullable=False) for name in _PARENT_KEY),
    Column("provider_job_id", BigInteger, nullable=False),
    Column("name", String(512), nullable=False),
    Column("conclusion", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("job_canonical", LargeBinary, nullable=False),
    PrimaryKeyConstraint(*_PARENT_KEY, "provider_job_id", name="pk_ci_history_jobs"),
    ForeignKeyConstraint(
        _PARENT_KEY, _PARENT_REFERENCES, name="fk_ci_history_jobs_attempt", ondelete="RESTRICT"
    ),
    CheckConstraint(
        "provider_job_id BETWEEN 1 AND 9007199254740991", name="ck_ci_history_jobs_identity"
    ),
    CheckConstraint("length(name) BETWEEN 1 AND 512", name="ck_ci_history_jobs_name"),
    CheckConstraint(
        "conclusion IN ('success', 'failure', 'cancelled', 'timed_out', 'skipped', "
        "'neutral', 'action_required', 'startup_failure', 'stale')",
        name="ck_ci_history_jobs_conclusion",
    ),
    CheckConstraint(
        "octet_length(job_canonical) BETWEEN 1 AND 32768", name="ck_ci_history_jobs_payload"
    ),
    schema=APPLICATION_SCHEMA,
)

ci_history_details = Table(
    "ci_history_details",
    metadata,
    *(Column(name, BigInteger, nullable=False) for name in _PARENT_KEY),
    Column("detail_canonical", LargeBinary, nullable=False),
    PrimaryKeyConstraint(*_PARENT_KEY, name="pk_ci_history_details"),
    ForeignKeyConstraint(
        _PARENT_KEY, _PARENT_REFERENCES, name="fk_ci_history_details_attempt", ondelete="RESTRICT"
    ),
    CheckConstraint(
        "octet_length(detail_canonical) BETWEEN 1 AND 8388608", name="ck_ci_history_details_payload"
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_ci_history_attempts_time",
    ci_history_attempts.c.installation_id,
    ci_history_attempts.c.repository_id,
    ci_history_attempts.c.generation,
    ci_history_attempts.c.run_created_at,
    ci_history_attempts.c.workflow_run_id,
    ci_history_attempts.c.run_attempt,
)
Index(
    "ix_ci_history_attempts_workflow",
    ci_history_attempts.c.installation_id,
    ci_history_attempts.c.repository_id,
    ci_history_attempts.c.generation,
    ci_history_attempts.c.workflow_id,
    ci_history_attempts.c.run_created_at,
)
Index(
    "ix_ci_history_attempts_detail_expiry",
    ci_history_attempts.c.installation_id,
    ci_history_attempts.c.repository_id,
    ci_history_attempts.c.generation,
    ci_history_attempts.c.detail_expires_at,
    postgresql_where=ci_history_attempts.c.detail_state == "retained",
)
Index(
    "ix_ci_history_jobs_name",
    ci_history_jobs.c.installation_id,
    ci_history_jobs.c.repository_id,
    ci_history_jobs.c.generation,
    ci_history_jobs.c.name,
)
