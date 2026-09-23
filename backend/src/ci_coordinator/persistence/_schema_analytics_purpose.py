from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    LargeBinary,
    PrimaryKeyConstraint,
    Table,
)

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

analytics_purpose_settings = Table(
    "analytics_purpose_settings",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("snapshot_canonical", LargeBinary, nullable=False),
    PrimaryKeyConstraint("installation_id", "repository_id", name="pk_analytics_purpose_settings"),
    ForeignKeyConstraint(
        ["installation_id", "repository_id"],
        [
            f"{APPLICATION_SCHEMA}.ci_history_datasets.installation_id",
            f"{APPLICATION_SCHEMA}.ci_history_datasets.repository_id",
        ],
        name="fk_analytics_purpose_dataset",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "generation BETWEEN 1 AND 9007199254740991 AND revision BETWEEN 1 AND 9007199254740991",
        name="ck_analytics_purpose_revision",
    ),
    CheckConstraint(
        "octet_length(snapshot_canonical) BETWEEN 1 AND 262144",
        name="ck_analytics_purpose_payload",
    ),
    schema=APPLICATION_SCHEMA,
)
