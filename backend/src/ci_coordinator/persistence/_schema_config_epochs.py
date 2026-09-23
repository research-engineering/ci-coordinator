from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)

from ci_coordinator.config_control.limits import MAX_CONFIG_CONTRACT_ID_UTF8_BYTES
from ci_coordinator.persistence._schema_core import (
    APPLICATION_SCHEMA,
    _safe_integer_max,
    metadata,
)

config_epochs = Table(
    "config_epochs",
    metadata,
    Column("epoch_id", String(64), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("source_format", String(8), nullable=False),
    Column("source_bytes", LargeBinary, nullable=False),
    Column("normalized_document_bytes", LargeBinary, nullable=False),
    Column("compiled_policy_bytes", LargeBinary, nullable=False),
    Column("document_schema_id", String(MAX_CONFIG_CONTRACT_ID_UTF8_BYTES), nullable=False),
    Column("document_profile_id", String(MAX_CONFIG_CONTRACT_ID_UTF8_BYTES), nullable=False),
    Column("semantic_profile_id", String(MAX_CONFIG_CONTRACT_ID_UTF8_BYTES), nullable=False),
    Column("compiled_schema_id", String(MAX_CONFIG_CONTRACT_ID_UTF8_BYTES), nullable=False),
    Column(
        "producer_resource_profile_id", String(MAX_CONFIG_CONTRACT_ID_UTF8_BYTES), nullable=False
    ),
    Column("producer_byte_profile_id", String(MAX_CONFIG_CONTRACT_ID_UTF8_BYTES), nullable=False),
    Column(
        "producer_feasibility_profile_id",
        String(MAX_CONFIG_CONTRACT_ID_UTF8_BYTES),
        nullable=False,
    ),
    Column("source_hash", String(64), nullable=False),
    Column("document_hash", String(64), nullable=False),
    Column("epoch_hash", String(64), nullable=False),
    CheckConstraint("epoch_id ~ '^[0-9a-f]{64}$'", name="ck_config_epochs_epoch_id_hash"),
    CheckConstraint(
        f"installation_id >= 1 AND installation_id <= {_safe_integer_max}",
        name="ck_config_epochs_installation_id_safe",
    ),
    CheckConstraint(
        f"repository_id >= 1 AND repository_id <= {_safe_integer_max}",
        name="ck_config_epochs_repository_id_safe",
    ),
    CheckConstraint("source_format IN ('json', 'yaml-1.2')", name="ck_config_epochs_source_format"),
    CheckConstraint(
        "octet_length(source_bytes) <= 2097152", name="ck_config_epochs_source_bytes_limit"
    ),
    CheckConstraint(
        "octet_length(normalized_document_bytes) BETWEEN 1 AND 4194304",
        name="ck_config_epochs_normalized_bytes_limit",
    ),
    CheckConstraint(
        "octet_length(compiled_policy_bytes) BETWEEN 1 AND 4194304",
        name="ck_config_epochs_compiled_bytes_limit",
    ),
    CheckConstraint(
        f"octet_length(document_schema_id) BETWEEN 1 AND {MAX_CONFIG_CONTRACT_ID_UTF8_BYTES}",
        name="ck_config_epochs_document_schema_id_limit",
    ),
    CheckConstraint(
        f"octet_length(document_profile_id) BETWEEN 1 AND {MAX_CONFIG_CONTRACT_ID_UTF8_BYTES}",
        name="ck_config_epochs_document_profile_id_limit",
    ),
    CheckConstraint(
        f"octet_length(semantic_profile_id) BETWEEN 1 AND {MAX_CONFIG_CONTRACT_ID_UTF8_BYTES}",
        name="ck_config_epochs_semantic_profile_id_limit",
    ),
    CheckConstraint(
        f"octet_length(compiled_schema_id) BETWEEN 1 AND {MAX_CONFIG_CONTRACT_ID_UTF8_BYTES}",
        name="ck_config_epochs_compiled_schema_id_limit",
    ),
    CheckConstraint(
        "octet_length(producer_resource_profile_id) BETWEEN 1 AND "
        f"{MAX_CONFIG_CONTRACT_ID_UTF8_BYTES}",
        name="ck_config_epochs_producer_resource_profile_id_limit",
    ),
    CheckConstraint(
        f"octet_length(producer_byte_profile_id) BETWEEN 1 AND {MAX_CONFIG_CONTRACT_ID_UTF8_BYTES}",
        name="ck_config_epochs_producer_byte_profile_id_limit",
    ),
    CheckConstraint(
        "octet_length(producer_feasibility_profile_id) BETWEEN 1 AND "
        f"{MAX_CONFIG_CONTRACT_ID_UTF8_BYTES}",
        name="ck_config_epochs_producer_feasibility_profile_id_limit",
    ),
    CheckConstraint(
        "source_hash ~ '^[0-9a-f]{64}$' AND document_hash ~ '^[0-9a-f]{64}$' "
        "AND epoch_hash ~ '^[0-9a-f]{64}$'",
        name="ck_config_epochs_hashes",
    ),
    PrimaryKeyConstraint("epoch_id", name="pk_config_epochs"),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "epoch_id",
        name="uq_config_epochs_scope_epoch",
    ),
    schema=APPLICATION_SCHEMA,
)

config_epoch_registrations = Table(
    "config_epoch_registrations",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("operation_id", String(256), nullable=False),
    Column("epoch_id", String(64), nullable=False),
    Column("audit_event_id", String(38), nullable=False),
    Column("audit_input_hash", LargeBinary(32), nullable=False),
    CheckConstraint(
        f"installation_id >= 1 AND installation_id <= {_safe_integer_max}",
        name="ck_config_epoch_registrations_installation_id_safe",
    ),
    CheckConstraint(
        f"repository_id >= 1 AND repository_id <= {_safe_integer_max}",
        name="ck_config_epoch_registrations_repository_id_safe",
    ),
    CheckConstraint(
        "octet_length(operation_id) BETWEEN 1 AND 256",
        name="ck_config_epoch_registrations_operation_id_limit",
    ),
    CheckConstraint(
        "epoch_id ~ '^[0-9a-f]{64}$'",
        name="ck_config_epoch_registrations_epoch_id_hash",
    ),
    CheckConstraint(
        "audit_event_id ~ '^audit_[0-9a-f]{32}$' AND octet_length(audit_input_hash) = 32",
        name="ck_config_epoch_registrations_audit_identity",
    ),
    PrimaryKeyConstraint(
        "installation_id",
        "repository_id",
        "operation_id",
        name="pk_config_epoch_registrations",
    ),
    UniqueConstraint("audit_event_id", name="uq_config_epoch_registrations_audit_event"),
    ForeignKeyConstraint(
        ["installation_id", "repository_id", "epoch_id"],
        [
            f"{APPLICATION_SCHEMA}.config_epochs.installation_id",
            f"{APPLICATION_SCHEMA}.config_epochs.repository_id",
            f"{APPLICATION_SCHEMA}.config_epochs.epoch_id",
        ],
        ondelete="RESTRICT",
        name="fk_config_epoch_registrations_epoch",
    ),
    ForeignKeyConstraint(
        ["audit_event_id"],
        [f"{APPLICATION_SCHEMA}.audit_events.audit_event_id"],
        ondelete="RESTRICT",
        name="fk_config_epoch_registrations_audit_event",
    ),
    schema=APPLICATION_SCHEMA,
)

active_config_epochs = Table(
    "active_config_epochs",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("epoch_id", String(64), nullable=False),
    Column("revision", BigInteger, nullable=False),
    CheckConstraint(
        f"installation_id >= 1 AND installation_id <= {_safe_integer_max}",
        name="ck_active_config_epochs_installation_id_safe",
    ),
    CheckConstraint(
        f"repository_id >= 1 AND repository_id <= {_safe_integer_max}",
        name="ck_active_config_epochs_repository_id_safe",
    ),
    CheckConstraint("epoch_id ~ '^[0-9a-f]{64}$'", name="ck_active_config_epochs_epoch_id_hash"),
    CheckConstraint(
        f"revision >= 1 AND revision <= {_safe_integer_max}",
        name="ck_active_config_epochs_revision_safe",
    ),
    PrimaryKeyConstraint("installation_id", "repository_id", name="pk_active_config_epochs"),
    ForeignKeyConstraint(
        ["installation_id", "repository_id", "epoch_id"],
        [
            f"{APPLICATION_SCHEMA}.config_epochs.installation_id",
            f"{APPLICATION_SCHEMA}.config_epochs.repository_id",
            f"{APPLICATION_SCHEMA}.config_epochs.epoch_id",
        ],
        ondelete="RESTRICT",
        name="fk_active_config_epochs_epoch",
    ),
    schema=APPLICATION_SCHEMA,
)

config_epoch_activations = Table(
    "config_epoch_activations",
    metadata,
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("operation_id", String(256), nullable=False),
    Column("expected_revision", BigInteger, nullable=True),
    Column("previous_epoch_id", String(64), nullable=True),
    Column("previous_revision", BigInteger, nullable=True),
    Column("target_epoch_id", String(64), nullable=False),
    Column("result_revision", BigInteger, nullable=False),
    Column("audit_event_id", String(38), nullable=False),
    Column("audit_input_hash", LargeBinary(32), nullable=False),
    CheckConstraint(
        f"installation_id >= 1 AND installation_id <= {_safe_integer_max}",
        name="ck_config_epoch_activations_installation_id_safe",
    ),
    CheckConstraint(
        f"repository_id >= 1 AND repository_id <= {_safe_integer_max}",
        name="ck_config_epoch_activations_repository_id_safe",
    ),
    CheckConstraint(
        "octet_length(operation_id) BETWEEN 1 AND 256",
        name="ck_config_epoch_activations_operation_id_limit",
    ),
    CheckConstraint(
        "target_epoch_id ~ '^[0-9a-f]{64}$' AND "
        "(previous_epoch_id IS NULL OR previous_epoch_id ~ '^[0-9a-f]{64}$')",
        name="ck_config_epoch_activations_epoch_hashes",
    ),
    CheckConstraint(
        "audit_event_id ~ '^audit_[0-9a-f]{32}$' AND octet_length(audit_input_hash) = 32",
        name="ck_config_epoch_activations_audit_identity",
    ),
    CheckConstraint(
        "(expected_revision IS NULL AND previous_epoch_id IS NULL "
        "AND previous_revision IS NULL AND result_revision = 1) OR "
        "(expected_revision >= 1 AND previous_epoch_id IS NOT NULL "
        "AND previous_revision = expected_revision AND result_revision = previous_revision + 1)",
        name="ck_config_epoch_activations_revision_successor",
    ),
    CheckConstraint(
        f"result_revision >= 1 AND result_revision <= {_safe_integer_max} AND "
        f"(expected_revision IS NULL OR expected_revision <= {_safe_integer_max}) AND "
        f"(previous_revision IS NULL OR previous_revision <= {_safe_integer_max})",
        name="ck_config_epoch_activations_revisions_safe",
    ),
    PrimaryKeyConstraint(
        "installation_id",
        "repository_id",
        "operation_id",
        name="pk_config_epoch_activations",
    ),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "result_revision",
        name="uq_config_epoch_activations_scope_revision",
    ),
    UniqueConstraint("audit_event_id", name="uq_config_epoch_activations_audit_event"),
    ForeignKeyConstraint(
        ["installation_id", "repository_id", "target_epoch_id"],
        [
            f"{APPLICATION_SCHEMA}.config_epochs.installation_id",
            f"{APPLICATION_SCHEMA}.config_epochs.repository_id",
            f"{APPLICATION_SCHEMA}.config_epochs.epoch_id",
        ],
        ondelete="RESTRICT",
        name="fk_config_epoch_activations_target_epoch",
    ),
    ForeignKeyConstraint(
        ["installation_id", "repository_id", "previous_epoch_id"],
        [
            f"{APPLICATION_SCHEMA}.config_epochs.installation_id",
            f"{APPLICATION_SCHEMA}.config_epochs.repository_id",
            f"{APPLICATION_SCHEMA}.config_epochs.epoch_id",
        ],
        ondelete="RESTRICT",
        name="fk_config_epoch_activations_previous_epoch",
    ),
    ForeignKeyConstraint(
        ["audit_event_id"],
        [f"{APPLICATION_SCHEMA}.audit_events.audit_event_id"],
        ondelete="RESTRICT",
        name="fk_config_epoch_activations_audit_event",
    ),
    schema=APPLICATION_SCHEMA,
)
