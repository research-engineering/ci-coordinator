from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Table,
)

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

ci_history_delivery_inbox = Table(
    "ci_history_delivery_inbox",
    metadata,
    Column("delivery_id", String(128), nullable=False),
    Column("source_fingerprint", String(64), nullable=False),
    *(
        Column(name, BigInteger, nullable=False)
        for name in (
            "installation_id",
            "repository_id",
            "workflow_run_id",
            "run_attempt",
            "workflow_id",
        )
    ),
    Column("run_created_at", DateTime(timezone=True), nullable=False),
    Column("source_recorded_at", DateTime(timezone=True), nullable=False),
    Column("source_retain_until", DateTime(timezone=True), nullable=False),
    Column("delivered_generation", BigInteger, nullable=False),
    Column("delivered_at", DateTime(timezone=True)),
    PrimaryKeyConstraint("delivery_id", name="pk_ci_history_delivery_inbox"),
    ForeignKeyConstraint(
        ["delivery_id"],
        [f"{APPLICATION_SCHEMA}.ci_workflow_observations.delivery_id"],
        name="fk_ci_history_delivery_inbox_source",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "octet_length(delivery_id) BETWEEN 1 AND 128 AND source_fingerprint ~ '^[0-9a-f]{64}$'",
        name="ck_ci_history_delivery_inbox_source",
    ),
    CheckConstraint(
        "installation_id BETWEEN 1 AND 9007199254740991 AND "
        "repository_id BETWEEN 1 AND 9007199254740991 AND "
        "workflow_run_id BETWEEN 1 AND 9007199254740991 AND "
        "run_attempt BETWEEN 1 AND 9007199254740991 AND "
        "workflow_id BETWEEN 1 AND 9007199254740991 AND "
        "delivered_generation BETWEEN 0 AND 9007199254740991",
        name="ck_ci_history_delivery_inbox_identity",
    ),
    CheckConstraint(
        "source_retain_until = source_recorded_at + INTERVAL '90 days'",
        name="ck_ci_history_delivery_inbox_retention",
    ),
    CheckConstraint(
        "(delivered_generation = 0 AND delivered_at IS NULL) OR "
        "(delivered_generation > 0 AND delivered_at IS NOT NULL AND "
        "source_recorded_at <= delivered_at AND delivered_at < source_retain_until)",
        name="ck_ci_history_delivery_inbox_receipt",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_ci_history_delivery_inbox_pending",
    ci_history_delivery_inbox.c.installation_id,
    ci_history_delivery_inbox.c.repository_id,
    ci_history_delivery_inbox.c.delivered_generation,
    ci_history_delivery_inbox.c.delivery_id,
)
Index(
    "ix_ci_history_delivery_inbox_workflow",
    ci_history_delivery_inbox.c.installation_id,
    ci_history_delivery_inbox.c.repository_id,
    ci_history_delivery_inbox.c.workflow_id,
    ci_history_delivery_inbox.c.delivered_generation,
    ci_history_delivery_inbox.c.delivery_id,
)
