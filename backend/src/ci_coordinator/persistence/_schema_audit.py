from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
)

from ci_coordinator.audit_replay.persistence_bytes import (
    MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
    MAX_AUDIT_TEXT_UTF8_BYTES_V1,
)
from ci_coordinator.persistence._schema_core import (
    APPLICATION_SCHEMA,
    _safe_integer_max,
    _subject_type_literals,
    metadata,
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("sequence", BigInteger, primary_key=True, autoincrement=False),
    Column("installation_id", BigInteger, nullable=True),
    Column("repository_id", BigInteger, nullable=True),
    Column("idempotency_key", LargeBinary, nullable=False),
    Column("idempotency_key_digest", LargeBinary(32), nullable=False),
    Column("subject_type", String(64), nullable=False),
    Column("subject_id", LargeBinary, nullable=False),
    Column("event_type", LargeBinary, nullable=False),
    Column("created_at", String(24), nullable=False),
    Column("actor", LargeBinary, nullable=False),
    Column("payload_canonical_json", LargeBinary, nullable=False),
    Column("schema_version", String(64), nullable=False),
    Column("audit_event_id", String(38), nullable=False),
    Column(
        "previous_event_hash",
        LargeBinary(32),
        ForeignKey(
            f"{APPLICATION_SCHEMA}.audit_events.event_hash",
            name="fk_audit_events_previous_event_hash",
        ),
        nullable=True,
    ),
    Column("payload_hash", LargeBinary(32), nullable=False),
    Column("input_hash", LargeBinary(32), nullable=False),
    Column("event_hash", LargeBinary(32), nullable=False),
    CheckConstraint(
        f"sequence >= 1 AND sequence <= {_safe_integer_max}",
        name="ck_audit_events_sequence_safe",
    ),
    CheckConstraint(
        "(installation_id IS NULL AND repository_id IS NULL) OR "
        f"(installation_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"repository_id BETWEEN 1 AND {_safe_integer_max})",
        name="ck_audit_events_scope_shape",
    ),
    CheckConstraint(
        "(sequence = 1 AND previous_event_hash IS NULL) OR "
        "(sequence > 1 AND previous_event_hash IS NOT NULL)",
        name="ck_audit_events_predecessor_shape",
    ),
    CheckConstraint(
        f"subject_type IN ({_subject_type_literals})",
        name="ck_audit_events_subject_type",
    ),
    CheckConstraint(
        "schema_version = 'ci-audit-event/v1'",
        name="ck_audit_events_schema_version",
    ),
    CheckConstraint(
        "octet_length(idempotency_key_digest) = 32",
        name="ck_audit_events_idempotency_digest_length",
    ),
    CheckConstraint(
        "octet_length(payload_hash) = 32 AND octet_length(input_hash) = 32 "
        "AND octet_length(event_hash) = 32",
        name="ck_audit_events_hash_lengths",
    ),
    CheckConstraint(
        "previous_event_hash IS NULL OR octet_length(previous_event_hash) = 32",
        name="ck_audit_events_previous_hash_length",
    ),
    CheckConstraint(
        f"octet_length(idempotency_key) >= 1 AND "
        f"octet_length(idempotency_key) <= {MAX_AUDIT_TEXT_UTF8_BYTES_V1}",
        name="ck_audit_events_idempotency_key_byte_limit",
    ),
    CheckConstraint(
        f"octet_length(subject_id) >= 1 AND "
        f"octet_length(subject_id) <= {MAX_AUDIT_TEXT_UTF8_BYTES_V1}",
        name="ck_audit_events_subject_id_byte_limit",
    ),
    CheckConstraint(
        f"octet_length(event_type) >= 1 AND "
        f"octet_length(event_type) <= {MAX_AUDIT_TEXT_UTF8_BYTES_V1}",
        name="ck_audit_events_event_type_byte_limit",
    ),
    CheckConstraint(
        f"octet_length(actor) >= 1 AND octet_length(actor) <= {MAX_AUDIT_TEXT_UTF8_BYTES_V1}",
        name="ck_audit_events_actor_byte_limit",
    ),
    CheckConstraint(
        f"octet_length(payload_canonical_json) >= 1 AND "
        "octet_length(payload_canonical_json) <= "
        f"{MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1}",
        name="ck_audit_events_payload_canonical_json_byte_limit",
    ),
    UniqueConstraint("idempotency_key_digest", name="uq_audit_events_idempotency_digest"),
    UniqueConstraint("audit_event_id", name="uq_audit_events_audit_event_id"),
    UniqueConstraint("event_hash", name="uq_audit_events_event_hash"),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_audit_events_scope_sequence",
    audit_events.c.installation_id,
    audit_events.c.repository_id,
    audit_events.c.sequence,
)

audit_ledger_head = Table(
    "audit_ledger_head",
    metadata,
    Column("head_id", SmallInteger, primary_key=True, autoincrement=False),
    Column("revision", BigInteger, nullable=False),
    Column("last_sequence", BigInteger, nullable=True),
    Column(
        "last_event_hash",
        LargeBinary(32),
        ForeignKey(
            f"{APPLICATION_SCHEMA}.audit_events.event_hash",
            name="fk_audit_ledger_head_last_event_hash",
        ),
        nullable=True,
    ),
    CheckConstraint("head_id = 1", name="ck_audit_ledger_head_singleton"),
    CheckConstraint(
        f"revision >= 0 AND revision <= {_safe_integer_max}",
        name="ck_audit_ledger_head_revision_safe",
    ),
    CheckConstraint(
        "(revision = 0 AND last_sequence IS NULL AND last_event_hash IS NULL) OR "
        "(revision >= 1 AND revision = last_sequence AND last_event_hash IS NOT NULL)",
        name="ck_audit_ledger_head_state",
    ),
    CheckConstraint(
        "last_event_hash IS NULL OR octet_length(last_event_hash) = 32",
        name="ck_audit_ledger_head_hash_length",
    ),
    schema=APPLICATION_SCHEMA,
)
