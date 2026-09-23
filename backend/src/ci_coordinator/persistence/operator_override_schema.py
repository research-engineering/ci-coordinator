"""Operator override persistence schema and compatibility capability."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    LargeBinary,
    String,
    Table,
    UniqueConstraint,
)

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

operator_overrides = Table(
    "operator_overrides",
    metadata,
    Column("override_id", String(41), primary_key=True),
    Column("operation_id", String(512), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("kind", String(32), nullable=False),
    Column("target_subject_id", String(512), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("applied_at", DateTime(timezone=True), nullable=False),
    Column("record_canonical_json", LargeBinary, nullable=False),
    Column("semantic_hash", String(64), nullable=False),
    Column("audit_event_id", String(38), nullable=False),
    Column("audit_input_hash", LargeBinary(32), nullable=False),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "operation_id",
        name="uq_operator_overrides_operation",
    ),
    schema=APPLICATION_SCHEMA,
)
