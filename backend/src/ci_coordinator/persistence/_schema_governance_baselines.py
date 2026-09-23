from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)

from ci_coordinator.governance_baseline import MAX_BASELINE_COMMAND_BYTES
from ci_coordinator.governance_observation import MAX_GOVERNANCE_STATE_BYTES
from ci_coordinator.persistence._schema_core import (
    APPLICATION_SCHEMA,
    _safe_integer_max,
    metadata,
)

governance_baselines = Table(
    "governance_baselines",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("version", BigInteger, nullable=False),
    Column("baseline_id", String(84), nullable=False),
    Column("operation_id", String(256), nullable=False),
    Column("state_digest", String(64), nullable=False),
    Column("state_canonical_json", LargeBinary, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("approved_at", DateTime(timezone=True), nullable=False),
    Column("actor", String(256), nullable=False),
    Column("reason", String(1024), nullable=False),
    Column("supersedes_baseline_id", String(84), nullable=True),
    Column("supersedes_version", BigInteger, nullable=True),
    Column("supersedes_state_digest", String(64), nullable=True),
    Column("audit_event_id", String(38), nullable=False),
    Column("audit_input_hash", LargeBinary(32), nullable=False),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_governance_baselines_installation_id_safe",
    ),
    CheckConstraint(
        f"repository_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_governance_baselines_repository_id_safe",
    ),
    CheckConstraint(
        "(version = 1 AND supersedes_baseline_id IS NULL "
        "AND supersedes_version IS NULL AND supersedes_state_digest IS NULL) OR "
        f"(version BETWEEN 2 AND {_safe_integer_max} "
        "AND supersedes_baseline_id ~ '^governance-baseline:[0-9a-f]{64}$' "
        "AND supersedes_version = version - 1 "
        "AND supersedes_state_digest ~ '^[0-9a-f]{64}$')",
        name="ck_governance_baselines_chain_shape",
    ),
    CheckConstraint(
        "baseline_id ~ '^governance-baseline:[0-9a-f]{64}$' AND state_digest ~ '^[0-9a-f]{64}$'",
        name="ck_governance_baselines_identity",
    ),
    CheckConstraint(
        "octet_length(operation_id) BETWEEN 1 AND 256",
        name="ck_governance_baselines_operation_id_limit",
    ),
    CheckConstraint(
        f"octet_length(state_canonical_json) BETWEEN 1 AND {MAX_GOVERNANCE_STATE_BYTES}",
        name="ck_governance_baselines_state_bytes",
    ),
    CheckConstraint(
        "observed_at <= approved_at",
        name="ck_governance_baselines_time_order",
    ),
    CheckConstraint(
        "octet_length(actor) BETWEEN 1 AND 256 "
        "AND octet_length(reason) BETWEEN 1 AND 1024 "
        "AND reason = btrim(reason)",
        name="ck_governance_baselines_approval_text",
    ),
    CheckConstraint(
        "audit_event_id ~ '^audit_[0-9a-f]{32}$' AND octet_length(audit_input_hash) = 32",
        name="ck_governance_baselines_audit_identity",
    ),
    PrimaryKeyConstraint(
        "installation_id",
        "repository_id",
        "version",
        name="pk_governance_baselines",
    ),
    UniqueConstraint(
        "baseline_id",
        name="uq_governance_baselines_baseline_id",
    ),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "operation_id",
        name="uq_governance_baselines_scope_operation",
    ),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "version",
        "baseline_id",
        name="uq_governance_baselines_scope_version_identity",
    ),
    UniqueConstraint(
        "audit_event_id",
        name="uq_governance_baselines_audit_event",
    ),
    ForeignKeyConstraint(
        [
            "installation_id",
            "repository_id",
            "supersedes_version",
            "supersedes_baseline_id",
        ],
        [
            f"{APPLICATION_SCHEMA}.governance_baselines.installation_id",
            f"{APPLICATION_SCHEMA}.governance_baselines.repository_id",
            f"{APPLICATION_SCHEMA}.governance_baselines.version",
            f"{APPLICATION_SCHEMA}.governance_baselines.baseline_id",
        ],
        ondelete="RESTRICT",
        name="fk_governance_baselines_predecessor",
    ),
    ForeignKeyConstraint(
        ["audit_event_id"],
        [f"{APPLICATION_SCHEMA}.audit_events.audit_event_id"],
        ondelete="RESTRICT",
        name="fk_governance_baselines_audit_event",
    ),
    schema=APPLICATION_SCHEMA,
)

governance_baseline_operations = Table(
    "governance_baseline_operations",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("operation_id", String(256), nullable=False),
    Column("command_canonical_json", LargeBinary, nullable=False),
    Column("result_kind", String(9), nullable=False),
    Column("result_baseline_id", String(84), nullable=False),
    Column("result_version", BigInteger, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_governance_baseline_operations_installation_id_safe",
    ),
    CheckConstraint(
        f"repository_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_governance_baseline_operations_repository_id_safe",
    ),
    CheckConstraint(
        "octet_length(operation_id) BETWEEN 1 AND 256",
        name="ck_governance_baseline_operations_operation_id_limit",
    ),
    CheckConstraint(
        f"octet_length(command_canonical_json) BETWEEN 1 AND {MAX_BASELINE_COMMAND_BYTES}",
        name="ck_governance_baseline_operations_command_bytes",
    ),
    CheckConstraint(
        "result_kind IN ('accepted', 'unchanged') "
        "AND result_baseline_id ~ '^governance-baseline:[0-9a-f]{64}$' "
        f"AND result_version BETWEEN 1 AND {_safe_integer_max}",
        name="ck_governance_baseline_operations_result",
    ),
    PrimaryKeyConstraint(
        "installation_id",
        "repository_id",
        "operation_id",
        name="pk_governance_baseline_operations",
    ),
    ForeignKeyConstraint(
        [
            "installation_id",
            "repository_id",
            "result_version",
            "result_baseline_id",
        ],
        [
            f"{APPLICATION_SCHEMA}.governance_baselines.installation_id",
            f"{APPLICATION_SCHEMA}.governance_baselines.repository_id",
            f"{APPLICATION_SCHEMA}.governance_baselines.version",
            f"{APPLICATION_SCHEMA}.governance_baselines.baseline_id",
        ],
        ondelete="RESTRICT",
        name="fk_governance_baseline_operations_result",
    ),
    schema=APPLICATION_SCHEMA,
)
