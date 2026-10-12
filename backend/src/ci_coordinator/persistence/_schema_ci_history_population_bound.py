from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    Table,
)

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

# Append-only provenance of each backfill lower bound set within one dataset generation.
ci_history_population_bounds = Table(
    "ci_history_population_bounds",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("created_from", DateTime(timezone=True), nullable=False),
    Column("source", String(32), nullable=False),
    Column("repository_created_at", DateTime(timezone=True), nullable=True),
    Column("configuration_revision", BigInteger, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint(
        "installation_id",
        "repository_id",
        "generation",
        "created_from",
        name="pk_ci_history_population_bounds",
    ),
    ForeignKeyConstraint(
        ["installation_id", "repository_id"],
        [
            f"{APPLICATION_SCHEMA}.ci_history_datasets.installation_id",
            f"{APPLICATION_SCHEMA}.ci_history_datasets.repository_id",
        ],
        ondelete="RESTRICT",
        name="fk_ci_history_population_bounds_dataset",
    ),
    CheckConstraint(
        "installation_id BETWEEN 1 AND 9007199254740991 AND "
        "repository_id BETWEEN 1 AND 9007199254740991 AND "
        "generation BETWEEN 1 AND 9007199254740991 AND "
        "configuration_revision BETWEEN 1 AND 9007199254740991 AND "
        "isfinite(created_from) AND isfinite(recorded_at) AND "
        "created_from = date_trunc('second', created_from) AND "
        "created_from <= recorded_at",
        name="ck_ci_history_population_bounds_identity",
    ),
    CheckConstraint(
        "source = 'explicit' AND repository_created_at IS NULL OR "
        "source = 'repository_created_at' AND repository_created_at IS NOT NULL",
        name="ck_ci_history_population_bounds_source",
    ),
    CheckConstraint(
        "repository_created_at IS NULL OR "
        "isfinite(repository_created_at) AND repository_created_at <= recorded_at AND "
        "created_from = "
        "(date_trunc('day', repository_created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')",
        name="ck_ci_history_population_bounds_catalog_floor",
    ),
    schema=APPLICATION_SCHEMA,
)
