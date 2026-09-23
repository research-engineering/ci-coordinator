from __future__ import annotations

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

from ci_coordinator.persistence._schema_core import (
    APPLICATION_SCHEMA,
    _runtime_state_profile,
    _safe_integer_max,
    metadata,
)

webhook_deliveries = Table(
    "webhook_deliveries",
    metadata,
    Column("delivery_id", String(_runtime_state_profile.delivery_id_utf8_bytes), primary_key=True),
    Column("body_sha256", String(64), nullable=False),
    CheckConstraint(
        f"octet_length(delivery_id) BETWEEN 1 AND {_runtime_state_profile.delivery_id_utf8_bytes}",
        name="ck_webhook_deliveries_delivery_id_byte_limit",
    ),
    CheckConstraint(
        "body_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_webhook_deliveries_body_sha256",
    ),
    UniqueConstraint("body_sha256", name="uq_webhook_deliveries_body_sha256"),
    schema=APPLICATION_SCHEMA,
)

production_admission_authorities = Table(
    "production_admission_authorities",
    metadata,
    Column(
        "authority_id",
        String(_runtime_state_profile.production_admission_authority_id_utf8_bytes),
        primary_key=True,
    ),
    Column(
        "key_id",
        String(_runtime_state_profile.production_admission_key_id_utf8_bytes),
        nullable=False,
    ),
    Column("public_key_spki_der", LargeBinary, nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("envelope_canonical_json", LargeBinary, nullable=False),
    CheckConstraint(
        "authority_id ~ '^production_admission_[0-9a-f]{32}$'",
        name="ck_production_admission_authorities_identity",
    ),
    CheckConstraint(
        "key_id ~ '^[A-Za-z0-9._-]{1,128}$'",
        name="ck_production_admission_authorities_key_id_shape",
    ),
    CheckConstraint(
        "octet_length(public_key_spki_der) = "
        f"{_runtime_state_profile.production_admission_public_key_der_bytes} AND "
        "substring(public_key_spki_der FROM 1 FOR 12) = "
        "decode('302a300506032b6570032100', 'hex')",
        name="ck_production_admission_authorities_public_key_ed25519",
    ),
    CheckConstraint(
        "octet_length(envelope_canonical_json) BETWEEN 1 AND "
        f"{_runtime_state_profile.production_admission_envelope_canonical_bytes}",
        name="ck_production_admission_authorities_envelope_byte_limit",
    ),
    CheckConstraint(
        "expires_at > issued_at AND expires_at <= issued_at + INTERVAL '7 days'",
        name="ck_production_admission_authorities_lifetime",
    ),
    schema=APPLICATION_SCHEMA,
)

production_admission_scope_bindings = Table(
    "production_admission_scope_bindings",
    metadata,
    Column(
        "authority_id",
        String(_runtime_state_profile.production_admission_authority_id_utf8_bytes),
        nullable=False,
    ),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("admission_subject_digest", String(64), nullable=False),
    Column("config_epoch_id", String(64), nullable=False),
    Column("target_registry_hash", String(64), nullable=False),
    PrimaryKeyConstraint(
        "authority_id",
        "installation_id",
        "repository_id",
        name="pk_production_admission_scope_bindings",
    ),
    ForeignKeyConstraint(
        ("authority_id",),
        (f"{APPLICATION_SCHEMA}.production_admission_authorities.authority_id",),
        name="fk_production_admission_scope_bindings_authority",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"repository_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_production_admission_scope_bindings_scope_safe",
    ),
    CheckConstraint(
        "admission_subject_digest ~ '^[0-9a-f]{64}$' AND "
        "config_epoch_id ~ '^[0-9a-f]{64}$' AND "
        "target_registry_hash ~ '^[0-9a-f]{64}$'",
        name="ck_production_admission_scope_bindings_digests",
    ),
    schema=APPLICATION_SCHEMA,
)

issued_plan_envelopes = Table(
    "issued_plan_envelopes",
    metadata,
    Column(
        "idempotency_key",
        String(_runtime_state_profile.issuance_idempotency_key_utf8_bytes),
        primary_key=True,
    ),
    Column(
        "record_id",
        String(_runtime_state_profile.issued_plan_record_id_utf8_bytes),
        nullable=False,
    ),
    Column("request_hash", String(64), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column(
        "production_admission_authority_id",
        String(_runtime_state_profile.production_admission_authority_id_utf8_bytes),
    ),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("envelope_canonical_json", LargeBinary, nullable=False),
    CheckConstraint(
        "octet_length(idempotency_key) BETWEEN 1 AND "
        f"{_runtime_state_profile.issuance_idempotency_key_utf8_bytes}",
        name="ck_issued_plan_envelopes_idempotency_key_byte_limit",
    ),
    CheckConstraint(
        "production_admission_authority_id IS NULL OR "
        "production_admission_authority_id ~ '^production_admission_[0-9a-f]{32}$'",
        name="ck_issued_plan_envelopes_authority_identity",
    ),
    CheckConstraint(
        "octet_length(record_id) BETWEEN 1 AND "
        f"{_runtime_state_profile.issued_plan_record_id_utf8_bytes}",
        name="ck_issued_plan_envelopes_record_id_byte_limit",
    ),
    CheckConstraint(
        "record_id ~ '^issued_plan_[0-9a-f]{32}$'",
        name="ck_issued_plan_envelopes_record_id_shape",
    ),
    CheckConstraint(
        "request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_issued_plan_envelopes_request_hash",
    ),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"repository_id BETWEEN 1 AND {_safe_integer_max}",
        name="ck_issued_plan_envelopes_scope_safe",
    ),
    CheckConstraint(
        "octet_length(envelope_canonical_json) BETWEEN 1 AND "
        f"{_runtime_state_profile.signed_envelope_canonical_bytes}",
        name="ck_issued_plan_envelopes_envelope_byte_limit",
    ),
    UniqueConstraint("record_id", name="uq_issued_plan_envelopes_record_id"),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "(isfinite(expires_at) AND expires_at > issued_at AND "
        "jsonb_typeof(convert_from(envelope_canonical_json, 'UTF8')::jsonb "
        "-> 'expiresAt') = 'string' AND "
        "(convert_from(envelope_canonical_json, 'UTF8')::jsonb ->> 'expiresAt') "
        "~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        "(\\.[0-9]{6})?[+-][0-9]{2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]{6})?)?$' AND "
        "expires_at = (convert_from(envelope_canonical_json, 'UTF8')::jsonb "
        "->> 'expiresAt')::timestamptz) IS TRUE",
        name="ck_issued_plan_envelopes_exact_expiry",
    ),
    ForeignKeyConstraint(
        (
            "production_admission_authority_id",
            "installation_id",
            "repository_id",
        ),
        (
            f"{APPLICATION_SCHEMA}.production_admission_scope_bindings.authority_id",
            f"{APPLICATION_SCHEMA}.production_admission_scope_bindings.installation_id",
            f"{APPLICATION_SCHEMA}.production_admission_scope_bindings.repository_id",
        ),
        name="fk_issued_plan_envelopes_admission_scope",
        ondelete="RESTRICT",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_issued_plan_envelopes_scope_issued",
    issued_plan_envelopes.c.installation_id,
    issued_plan_envelopes.c.repository_id,
    issued_plan_envelopes.c.issued_at,
    issued_plan_envelopes.c.record_id,
)

Index(
    "ix_issued_plan_envelopes_selected_expiry",
    issued_plan_envelopes.c.installation_id,
    issued_plan_envelopes.c.repository_id,
    issued_plan_envelopes.c.expires_at,
    postgresql_where=issued_plan_envelopes.c.production_admission_authority_id.is_not(None),
)
