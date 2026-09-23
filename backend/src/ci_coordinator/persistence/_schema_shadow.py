from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Table,
)

from ci_coordinator.persistence._schema_core import (
    APPLICATION_SCHEMA,
    _shadow_reconciliation_state_profile,
    metadata,
)

shadow_evidence = Table(
    "shadow_evidence",
    metadata,
    Column(
        "profile_id",
        String(_shadow_reconciliation_state_profile.profile_id_utf8_bytes),
        nullable=False,
    ),
    Column(
        "repository",
        String(_shadow_reconciliation_state_profile.shadow_repository_utf8_bytes),
        nullable=False,
    ),
    Column(
        "event",
        String(_shadow_reconciliation_state_profile.shadow_event_utf8_bytes),
        nullable=False,
    ),
    Column(
        "surface",
        String(_shadow_reconciliation_state_profile.shadow_surface_utf8_bytes),
        nullable=False,
    ),
    Column("record_canonical_json", LargeBinary, nullable=False),
    Column("semantic_hash", String(64), nullable=False),
    CheckConstraint(
        "octet_length(profile_id) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.profile_id_utf8_bytes} AND "
        "octet_length(repository) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.shadow_repository_utf8_bytes} AND "
        "octet_length(event) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.shadow_event_utf8_bytes} AND "
        "octet_length(surface) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.shadow_surface_utf8_bytes}",
        name="ck_shadow_evidence_key_byte_limits",
    ),
    CheckConstraint(
        "octet_length(record_canonical_json) BETWEEN 1 AND "
        f"{_shadow_reconciliation_state_profile.shadow_record_canonical_bytes}",
        name="ck_shadow_evidence_record_byte_limit",
    ),
    CheckConstraint(
        "semantic_hash ~ '^[0-9a-f]{64}$'",
        name="ck_shadow_evidence_semantic_hash",
    ),
    PrimaryKeyConstraint(
        "profile_id",
        "repository",
        "event",
        "surface",
        name="pk_shadow_evidence",
    ),
    schema=APPLICATION_SCHEMA,
)
