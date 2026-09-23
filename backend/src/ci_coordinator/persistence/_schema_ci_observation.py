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

ci_observation_subscriptions = Table(
    "ci_observation_subscriptions",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("snapshot_digest", String(64), nullable=False),
    Column("snapshot_canonical", LargeBinary, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("preferred_lane", String(8), nullable=False),
    Column("detail_truncated_until", DateTime(timezone=True)),
    PrimaryKeyConstraint(
        "installation_id", "repository_id", name="pk_ci_observation_subscriptions"
    ),
    CheckConstraint(
        "installation_id BETWEEN 1 AND 9007199254740991 AND "
        "repository_id BETWEEN 1 AND 9007199254740991 AND "
        "revision BETWEEN 1 AND 9007199254740991",
        name="ck_ci_observation_subscriptions_integers",
    ),
    CheckConstraint(
        "snapshot_digest ~ '^[0-9a-f]{64}$'",
        name="ck_ci_observation_subscriptions_digest",
    ),
    CheckConstraint(
        "octet_length(snapshot_canonical) BETWEEN 1 AND 2048",
        name="ck_ci_observation_subscriptions_payload",
    ),
    CheckConstraint(
        "preferred_lane IN ('recent', 'backfill')",
        name="ck_ci_observation_subscriptions_lane",
    ),
    schema=APPLICATION_SCHEMA,
)

ci_observation_scans = Table(
    "ci_observation_scans",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("lane", String(8), nullable=False),
    Column("config_revision", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("state_canonical", LargeBinary, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("last_completed_through", DateTime(timezone=True)),
    Column("last_page_at", DateTime(timezone=True)),
    Column("pages_seen", BigInteger, nullable=False),
    Column("sources_registered", BigInteger, nullable=False),
    Column("last_outcome", String(32)),
    PrimaryKeyConstraint(
        "installation_id", "repository_id", "lane", name="pk_ci_observation_scans"
    ),
    ForeignKeyConstraint(
        ["installation_id", "repository_id"],
        [
            f"{APPLICATION_SCHEMA}.ci_observation_subscriptions.{name}"
            for name in ("installation_id", "repository_id")
        ],
        name="fk_ci_observation_scans_subscription",
        ondelete="RESTRICT",
    ),
    CheckConstraint("lane IN ('recent', 'backfill')", name="ck_ci_observation_scans_lane"),
    CheckConstraint(
        "config_revision BETWEEN 1 AND 9007199254740991 AND "
        "revision BETWEEN 1 AND 9007199254740991 AND "
        "pages_seen BETWEEN 0 AND 9007199254740991 AND "
        "sources_registered BETWEEN 0 AND 9007199254740991",
        name="ck_ci_observation_scans_integers",
    ),
    CheckConstraint(
        "octet_length(state_canonical) BETWEEN 1 AND 2048",
        name="ck_ci_observation_scans_payload",
    ),
    CheckConstraint(
        "last_outcome IS NULL OR last_outcome IN "
        "('page_recorded', 'capacity_reached', 'provider_unavailable', "
        "'provider_binding_mismatch', 'provider_malformed', 'provider_incomplete', "
        "'provider_not_terminal', 'provider_unstable', 'access_unavailable', 'timed_out')",
        name="ck_ci_observation_scans_outcome",
    ),
    CheckConstraint(
        "last_completed_through IS NULL OR "
        "last_completed_through = date_trunc('second', last_completed_through)",
        name="ck_ci_observation_scans_completed_precision",
    ),
    schema=APPLICATION_SCHEMA,
)

ci_observation_gaps = Table(
    "ci_observation_gaps",
    metadata,
    Column("gap_id", String(64), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("gap_canonical", LargeBinary, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("gap_id", name="pk_ci_observation_gaps"),
    ForeignKeyConstraint(
        ["installation_id", "repository_id"],
        [
            f"{APPLICATION_SCHEMA}.ci_observation_subscriptions.{name}"
            for name in ("installation_id", "repository_id")
        ],
        name="fk_ci_observation_gaps_subscription",
        ondelete="RESTRICT",
    ),
    CheckConstraint("gap_id ~ '^[0-9a-f]{64}$'", name="ck_ci_observation_gaps_digest"),
    CheckConstraint(
        "octet_length(gap_canonical) BETWEEN 1 AND 2048",
        name="ck_ci_observation_gaps_payload",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_ci_observation_subscriptions_due",
    ci_observation_subscriptions.c.enabled,
    ci_observation_subscriptions.c.next_attempt_at,
    ci_observation_subscriptions.c.installation_id,
    ci_observation_subscriptions.c.repository_id,
)
Index(
    "ix_ci_observation_gaps_scope",
    ci_observation_gaps.c.installation_id,
    ci_observation_gaps.c.repository_id,
    ci_observation_gaps.c.gap_id.collate("C"),
)
Index(
    "ix_ci_observation_gaps_expiry",
    ci_observation_gaps.c.installation_id,
    ci_observation_gaps.c.repository_id,
    ci_observation_gaps.c.expires_at,
)
