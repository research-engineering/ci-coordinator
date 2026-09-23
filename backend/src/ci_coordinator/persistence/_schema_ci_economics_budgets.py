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

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

ci_economics_budget_policies = Table(
    "ci_economics_budget_policies",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("policy_key", String(128), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("policy_digest", String(64), nullable=False),
    Column("policy_canonical", LargeBinary, nullable=False),
    PrimaryKeyConstraint(
        "installation_id", "repository_id", "policy_key", name="pk_ci_economics_budget_policies"
    ),
    CheckConstraint(
        "installation_id BETWEEN 1 AND 9007199254740991 AND "
        "repository_id BETWEEN 1 AND 9007199254740991 AND "
        "revision BETWEEN 1 AND 9007199254740991",
        name="ck_ci_economics_budget_policies_integers",
    ),
    CheckConstraint(
        "policy_key ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'",
        name="ck_ci_economics_budget_policies_key",
    ),
    CheckConstraint(
        "policy_digest ~ '^[0-9a-f]{64}$'", name="ck_ci_economics_budget_policies_digest"
    ),
    CheckConstraint(
        "octet_length(policy_canonical) BETWEEN 1 AND 2048",
        name="ck_ci_economics_budget_policies_payload",
    ),
    schema=APPLICATION_SCHEMA,
)

ci_economics_budget_signals = Table(
    "ci_economics_budget_signals",
    metadata,
    Column("signal_id", String(64), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("policy_key", String(128), nullable=False),
    Column("policy_revision", BigInteger, nullable=False),
    Column("policy_canonical", LargeBinary, nullable=False),
    Column("report_id", String(64), nullable=False),
    Column("subject_id", String(64), nullable=False),
    Column("report_digest", String(64), nullable=False),
    Column("counter", String(16), nullable=False),
    Column("value_us", BigInteger),
    Column("unavailable_reason", String(32)),
    Column("command_exit_code", BigInteger, nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("retain_until", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("signal_id", name="pk_ci_economics_budget_signals"),
    UniqueConstraint("report_id", "policy_key", name="uq_ci_economics_budget_signals_slot"),
    CheckConstraint(
        "signal_id ~ '^[0-9a-f]{64}$' AND report_id ~ '^[0-9a-f]{64}$' AND "
        "subject_id ~ '^[0-9a-f]{64}$' AND report_digest ~ '^[0-9a-f]{64}$'",
        name="ck_ci_economics_budget_signals_digests",
    ),
    CheckConstraint(
        "policy_revision BETWEEN 1 AND 9007199254740991 AND "
        "command_exit_code BETWEEN -9007199254740991 AND 9007199254740991",
        name="ck_ci_economics_budget_signals_integers",
    ),
    CheckConstraint(
        "counter IN ('cpu_user', 'cpu_system', 'elapsed')",
        name="ck_ci_economics_budget_signals_counter",
    ),
    CheckConstraint(
        "(value_us IS NOT NULL AND value_us BETWEEN 0 AND 9007199254740991 AND "
        "unavailable_reason IS NULL AND outcome IN ('breached', 'within_budget')) OR "
        "(value_us IS NULL AND unavailable_reason IS NOT NULL AND unavailable_reason IN "
        "('unsupported_platform', 'counter_error', 'out_of_range', 'incomplete_scope') AND "
        "outcome = 'insufficient_evidence')",
        name="ck_ci_economics_budget_signals_measurement",
    ),
    CheckConstraint(
        "octet_length(policy_canonical) BETWEEN 1 AND 2048",
        name="ck_ci_economics_budget_signals_payload",
    ),
    CheckConstraint("received_at < retain_until", name="ck_ci_economics_budget_signals_retention"),
    ForeignKeyConstraint(
        ["installation_id", "repository_id", "policy_key"],
        [
            f"{APPLICATION_SCHEMA}.ci_economics_budget_policies.{name}"
            for name in ("installation_id", "repository_id", "policy_key")
        ],
        name="fk_ci_economics_budget_signals_policy",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["report_id", "subject_id", "report_digest", "retain_until"],
        [
            f"{APPLICATION_SCHEMA}.ci_job_measurement_reports.{name}"
            for name in ("report_id", "subject_id", "report_digest", "retain_until")
        ],
        name="fk_ci_economics_budget_signals_report",
        ondelete="CASCADE",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_ci_economics_budget_signals_scope",
    ci_economics_budget_signals.c.installation_id,
    ci_economics_budget_signals.c.repository_id,
    ci_economics_budget_signals.c.signal_id.collate("C"),
)
Index(
    "ix_ci_economics_budget_signals_policy",
    ci_economics_budget_signals.c.installation_id,
    ci_economics_budget_signals.c.repository_id,
    ci_economics_budget_signals.c.policy_key,
    ci_economics_budget_signals.c.policy_revision,
    ci_economics_budget_signals.c.signal_id.collate("C"),
)
