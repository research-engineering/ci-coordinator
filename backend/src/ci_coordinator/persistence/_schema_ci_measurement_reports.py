from sqlalchemy import (
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

from ci_coordinator.ci_economics.reports import MAX_REPORT_BYTES
from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

ci_job_measurement_reports = Table(
    "ci_job_measurement_reports",
    metadata,
    Column("report_id", String(64), nullable=False),
    Column("subject_id", String(64), nullable=False),
    Column("source_kind", String(32), nullable=False),
    Column("report_digest", String(64), nullable=False),
    Column("producer_claim_hash", String(64), nullable=False),
    Column("provider_binding_digest", String(64), nullable=False),
    Column("payload_canonical", LargeBinary, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("retain_until", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("report_id", name="pk_ci_job_measurement_reports"),
    UniqueConstraint(
        "report_id",
        "subject_id",
        "report_digest",
        "retain_until",
        name="uq_ci_job_measurement_reports_budget_identity",
    ),
    CheckConstraint(
        "report_id ~ '^[0-9a-f]{64}$' AND subject_id ~ '^[0-9a-f]{64}$' AND "
        "report_digest ~ '^[0-9a-f]{64}$' AND producer_claim_hash ~ '^[0-9a-f]{64}$' AND "
        "provider_binding_digest ~ '^[0-9a-f]{64}$'",
        name="ck_ci_job_measurement_reports_digests",
    ),
    CheckConstraint("source_kind = 'provider_run'", name="ck_ci_job_measurement_reports_source"),
    CheckConstraint(
        f"octet_length(payload_canonical) BETWEEN 1 AND {MAX_REPORT_BYTES}",
        name="ck_ci_job_measurement_reports_payload",
    ),
    CheckConstraint("received_at < retain_until", name="ck_ci_job_measurement_reports_retention"),
    ForeignKeyConstraint(
        ["subject_id", "retain_until", "source_kind"],
        [
            f"{APPLICATION_SCHEMA}.ci_workflow_attempt_collections.subject_id",
            f"{APPLICATION_SCHEMA}.ci_workflow_attempt_collections.evidence_retain_until",
            f"{APPLICATION_SCHEMA}.ci_workflow_attempt_collections.source_kind",
        ],
        name="fk_ci_job_measurement_reports_source",
        ondelete="RESTRICT",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_ci_job_measurement_reports_source",
    ci_job_measurement_reports.c.subject_id,
    ci_job_measurement_reports.c.report_id,
)
