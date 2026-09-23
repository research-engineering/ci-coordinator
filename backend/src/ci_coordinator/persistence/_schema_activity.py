from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    String,
    Table,
)

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

activity_events = Table(
    "activity_events",
    metadata,
    Column("sequence", BigInteger, Identity(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("retain_until", DateTime(timezone=True), nullable=False),
    Column("issuer", String(2048), nullable=False),
    Column("subject", String(512), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("action", String(32), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("operation_ref", String(36), nullable=False),
    CheckConstraint("sequence BETWEEN 1 AND 9007199254740991", name="ck_activity_sequence"),
    CheckConstraint(
        "octet_length(issuer) BETWEEN 1 AND 2048 AND octet_length(subject) BETWEEN 1 AND 512 "
        "AND actor ~ '^keycloak-(human|workload):v1:[0-9a-f]{64}$'",
        name="ck_activity_identity",
    ),
    CheckConstraint(
        "(action IN ('login', 'logout', 'expired', 'revoked', 'replaced') "
        "AND outcome = 'committed') "
        "OR (action = 'role_denied' AND outcome = 'denied') "
        "OR (action = 'export' AND outcome = 'attempted')",
        name="ck_activity_outcome",
    ),
    CheckConstraint(
        "retain_until = occurred_at + INTERVAL '2592000 seconds'", name="ck_activity_retention"
    ),
    CheckConstraint(
        "operation_ref ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
        name="ck_activity_operation",
    ),
    schema=APPLICATION_SCHEMA,
)
Index("ix_activity_issuer_sequence", activity_events.c.issuer, activity_events.c.sequence)
Index("ix_activity_retention", activity_events.c.retain_until, activity_events.c.sequence)

activity_diagnostic_buckets = Table(
    "activity_diagnostic_buckets",
    metadata,
    Column("bucket", DateTime(timezone=True), primary_key=True),
    Column("action", String(32), primary_key=True),
    Column("count", BigInteger, nullable=False),
    CheckConstraint(
        "action IN ('login_rejected', 'login_unavailable', 'role_denied', 'export')",
        name="ck_activity_diagnostic_action",
    ),
    CheckConstraint("count BETWEEN 1 AND 1000000", name="ck_activity_diagnostic_count"),
    CheckConstraint(
        "bucket = date_trunc('hour', bucket AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
        name="ck_activity_diagnostic_bucket",
    ),
    schema=APPLICATION_SCHEMA,
)
