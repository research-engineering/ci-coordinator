from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
)

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

compatibility_declarations = Table(
    "database_compatibility_declarations",
    metadata,
    Column("generation", BigInteger, nullable=False, autoincrement=False),
    Column("revision_id", String(32), nullable=False),
    Column("parent_revision_id", String(32), nullable=True),
    Column("transition_kind", String(16), nullable=False),
    Column("lineage_id", String(128), nullable=False),
    Column("protocol_version", SmallInteger, nullable=False),
    Column("declaration_hash", String(64), nullable=False),
    CheckConstraint("generation >= 1", name="ck_database_compatibility_generation_positive"),
    CheckConstraint(
        "transition_kind IN ('bootstrap', 'expand', 'contract')",
        name="ck_database_compatibility_transition_kind",
    ),
    CheckConstraint(
        "(generation = 1 AND parent_revision_id IS NULL AND transition_kind = 'bootstrap') "
        "OR (generation > 1 AND parent_revision_id IS NOT NULL "
        "AND transition_kind IN ('expand', 'contract'))",
        name="ck_database_compatibility_parent_shape",
    ),
    CheckConstraint(
        "declaration_hash ~ '^[0-9a-f]{64}$'",
        name="ck_database_compatibility_declaration_hash",
    ),
    UniqueConstraint("revision_id", name="uq_database_compatibility_revision"),
    PrimaryKeyConstraint("generation", name="pk_database_compatibility_declarations"),
    ForeignKeyConstraint(
        ["parent_revision_id"],
        [f"{APPLICATION_SCHEMA}.database_compatibility_declarations.revision_id"],
        name="fk_database_compatibility_parent_revision",
    ),
    schema=APPLICATION_SCHEMA,
)

compatibility_capabilities = Table(
    "database_compatibility_capabilities",
    metadata,
    Column("revision_id", String(32), nullable=False),
    Column("capability_id", String(128), nullable=False),
    Column("descriptor_hash", String(64), nullable=False),
    CheckConstraint(
        "descriptor_hash ~ '^[0-9a-f]{64}$'",
        name="ck_database_capability_descriptor_hash",
    ),
    ForeignKeyConstraint(
        ["revision_id"],
        [f"{APPLICATION_SCHEMA}.database_compatibility_declarations.revision_id"],
        ondelete="RESTRICT",
        name="fk_database_capability_revision",
    ),
    PrimaryKeyConstraint(
        "revision_id",
        "capability_id",
        name="pk_database_compatibility_capability",
    ),
    schema=APPLICATION_SCHEMA,
)
