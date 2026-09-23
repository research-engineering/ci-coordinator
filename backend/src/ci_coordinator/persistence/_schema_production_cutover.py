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

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, _safe_integer_max, metadata
from ci_coordinator.production_admission.limits import MAX_PRODUCTION_STAGE_BYTES

production_evidence_bundles = Table(
    "production_evidence_bundles",
    metadata,
    Column("bundle_digest", String(64), primary_key=True),
    Column("content_sha256", String(64), nullable=False),
    Column("canonical_json", LargeBinary, nullable=False),
    Column("byte_count", BigInteger, nullable=False),
    Column("retained_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "bundle_digest ~ '^[0-9a-f]{64}$' AND "
        "content_sha256 = encode(sha256(canonical_json), 'hex')",
        name="ck_production_evidence_bundles_identity",
    ),
    CheckConstraint(
        f"byte_count BETWEEN 1 AND {MAX_PRODUCTION_STAGE_BYTES} AND "
        "byte_count = octet_length(canonical_json)",
        name="ck_production_evidence_bundles_bytes",
    ),
    schema=APPLICATION_SCHEMA,
)

production_staged_grants = Table(
    "production_staged_grants",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("authority_id", String(53), nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("bundle_digest", String(64), nullable=False),
    Column("lookup_canonical_json", LargeBinary, nullable=False),
    Column("staged_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint(
        "installation_id", "repository_id", "authority_id", name="pk_production_staged_grants"
    ),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "authority_id",
        "generation",
        name="uq_production_staged_grants_generation",
    ),
    ForeignKeyConstraint(
        ("authority_id", "installation_id", "repository_id"),
        (
            f"{APPLICATION_SCHEMA}.production_admission_scope_bindings.authority_id",
            f"{APPLICATION_SCHEMA}.production_admission_scope_bindings.installation_id",
            f"{APPLICATION_SCHEMA}.production_admission_scope_bindings.repository_id",
        ),
        name="fk_production_staged_grants_scope",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ("bundle_digest",),
        (f"{APPLICATION_SCHEMA}.production_evidence_bundles.bundle_digest",),
        name="fk_production_staged_grants_bundle",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        f"generation BETWEEN 1 AND {_safe_integer_max}",
        name="ck_production_staged_grants_generation",
    ),
    CheckConstraint(
        "octet_length(lookup_canonical_json) BETWEEN 1 AND 262144",
        name="ck_production_staged_grants_lookup_bytes",
    ),
    schema=APPLICATION_SCHEMA,
)

production_scope_states = Table(
    "production_scope_states",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("revoked_through_generation", BigInteger, nullable=False),
    Column("active_authority_id", String(53)),
    Column("staged_authority_id", String(53)),
    Column("latch_override_id", String(41)),
    Column("latch_applied_at", DateTime(timezone=True)),
    PrimaryKeyConstraint("installation_id", "repository_id", name="pk_production_scope_states"),
    ForeignKeyConstraint(
        ("installation_id", "repository_id", "active_authority_id", "generation"),
        (
            f"{APPLICATION_SCHEMA}.production_staged_grants.installation_id",
            f"{APPLICATION_SCHEMA}.production_staged_grants.repository_id",
            f"{APPLICATION_SCHEMA}.production_staged_grants.authority_id",
            f"{APPLICATION_SCHEMA}.production_staged_grants.generation",
        ),
        name="fk_production_scope_states_active",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ("installation_id", "repository_id", "staged_authority_id"),
        (
            f"{APPLICATION_SCHEMA}.production_staged_grants.installation_id",
            f"{APPLICATION_SCHEMA}.production_staged_grants.repository_id",
            f"{APPLICATION_SCHEMA}.production_staged_grants.authority_id",
        ),
        name="fk_production_scope_states_staged",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ("latch_override_id",),
        (f"{APPLICATION_SCHEMA}.operator_overrides.override_id",),
        name="fk_production_scope_states_latch",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"repository_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"revision BETWEEN 1 AND {_safe_integer_max} AND "
        f"generation BETWEEN 0 AND {_safe_integer_max} AND "
        "revoked_through_generation BETWEEN 0 AND generation",
        name="ck_production_scope_states_counters",
    ),
    CheckConstraint(
        "(generation = 0 AND active_authority_id IS NULL) OR "
        "(generation > 0 AND active_authority_id IS NOT NULL)",
        name="ck_production_scope_states_active",
    ),
    CheckConstraint(
        "(latch_override_id IS NULL AND latch_applied_at IS NULL) OR "
        "(latch_override_id IS NOT NULL AND latch_applied_at IS NOT NULL AND "
        "revoked_through_generation = generation)",
        name="ck_production_scope_states_latch",
    ),
    schema=APPLICATION_SCHEMA,
)
