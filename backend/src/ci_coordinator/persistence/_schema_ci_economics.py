"""SQLAlchemy metadata for retained CI economics evidence."""

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
    text,
)

from ci_coordinator.ci_economics.model import (
    MAX_JOB_LABELS,
    MAX_JOB_NAME_CODE_POINTS,
    MAX_RUNNER_TEXT_CODE_POINTS,
)
from ci_coordinator.ci_economics.profile import load_bundled_ci_economics_profile
from ci_coordinator.persistence._schema_core import (
    APPLICATION_SCHEMA,
    _safe_integer_max,
    metadata,
)

_MAX_OBSERVATION_CANONICAL_BYTES = 16_384
_MAX_SNAPSHOT_JOB_CANONICAL_BYTES = 8_192
_ci_economics_profile = load_bundled_ci_economics_profile()

ci_workflow_observations = Table(
    "ci_workflow_observations",
    metadata,
    Column("delivery_id", String(128), nullable=False),
    Column("observation_kind", String(32), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("workflow_run_id", BigInteger, nullable=False),
    Column("run_attempt", BigInteger, nullable=False),
    Column("head_sha", String(40), nullable=False),
    Column("provider_job_id", BigInteger, nullable=True),
    Column("semantic_hash", String(64), nullable=False),
    Column("observation_canonical_json", LargeBinary, nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("statement_timestamp()"),
    ),
    Column(
        "retain_until",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("statement_timestamp() + INTERVAL '90 days'"),
    ),
    CheckConstraint(
        "observation_kind IN ('workflow_job', 'workflow_run')",
        name="ck_ci_workflow_observations_kind",
    ),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"repository_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"workflow_run_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"run_attempt BETWEEN 1 AND {_safe_integer_max} AND "
        f"(provider_job_id IS NULL OR provider_job_id BETWEEN 1 AND {_safe_integer_max})",
        name="ck_ci_workflow_observations_identity_safe",
    ),
    CheckConstraint(
        "head_sha ~ '^[0-9a-f]{40}$' AND semantic_hash ~ '^[0-9a-f]{64}$'",
        name="ck_ci_workflow_observations_hashes",
    ),
    CheckConstraint(
        "(observation_kind = 'workflow_job' AND provider_job_id IS NOT NULL) OR "
        "(observation_kind = 'workflow_run' AND provider_job_id IS NULL)",
        name="ck_ci_workflow_observations_job_identity",
    ),
    CheckConstraint(
        f"octet_length(observation_canonical_json) BETWEEN 1 AND "
        f"{_MAX_OBSERVATION_CANONICAL_BYTES}",
        name="ck_ci_workflow_observations_canonical_byte_limit",
    ),
    CheckConstraint(
        "retain_until = recorded_at + INTERVAL '90 days'",
        name="ck_ci_workflow_observations_retention",
    ),
    PrimaryKeyConstraint("delivery_id", name="pk_ci_workflow_observations"),
    ForeignKeyConstraint(
        ["delivery_id"],
        [f"{APPLICATION_SCHEMA}.webhook_deliveries.delivery_id"],
        ondelete="RESTRICT",
        name="fk_ci_workflow_observations_delivery",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_ci_workflow_observations_attempt_jobs",
    ci_workflow_observations.c.installation_id,
    ci_workflow_observations.c.repository_id,
    ci_workflow_observations.c.workflow_run_id,
    ci_workflow_observations.c.run_attempt,
    ci_workflow_observations.c.head_sha,
    ci_workflow_observations.c.provider_job_id,
    ci_workflow_observations.c.semantic_hash,
    ci_workflow_observations.c.delivery_id,
    postgresql_where=text("observation_kind = 'workflow_job'"),
)

Index(
    "ix_ci_workflow_observations_retention",
    ci_workflow_observations.c.retain_until,
    ci_workflow_observations.c.delivery_id,
)

ci_workflow_attempt_collections = Table(
    "ci_workflow_attempt_collections",
    metadata,
    Column("subject_id", String(64), nullable=False),
    Column("policy_hash", String(64), nullable=False),
    Column("source_created_at", DateTime(timezone=True), nullable=False),
    Column("deadline_at", DateTime(timezone=True), nullable=False),
    Column("evidence_retain_until", DateTime(timezone=True), nullable=False),
    Column("tombstone_retain_until", DateTime(timezone=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("attempt_count", BigInteger, nullable=False),
    Column("max_attempts", BigInteger, nullable=False),
    Column("backoff_seconds", BigInteger, nullable=False),
    Column("max_backoff_seconds", BigInteger, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=True),
    Column("claim_generation", BigInteger, nullable=False),
    Column("lease_owner_id", String(64), nullable=True),
    Column("lease_token", String(64), nullable=True),
    Column("lease_acquired_at", DateTime(timezone=True), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("last_failure_reason", String(64), nullable=True),
    Column("final_outcome", String(32), nullable=True),
    Column("terminal_reason", String(32), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("expired_at", DateTime(timezone=True), nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("statement_timestamp()"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("statement_timestamp()"),
    ),
    Column("source_kind", String(32), nullable=False),
    Column("legacy_subject_id", String(64), nullable=True),
    Column("installation_id", BigInteger, nullable=True),
    Column("repository_id", BigInteger, nullable=True),
    Column("workflow_run_id", BigInteger, nullable=True),
    Column("run_attempt", BigInteger, nullable=True),
    Column("head_sha", String(40), nullable=True),
    Column("provider_api_version", String(10), nullable=True),
    Column("source_evidence_digest", String(64), nullable=True),
    CheckConstraint(
        "subject_id ~ '^[0-9a-f]{64}$' AND policy_hash ~ '^[0-9a-f]{64}$'",
        name="ck_ci_workflow_attempt_collections_subject",
    ),
    CheckConstraint(
        "(source_kind = 'reconciliation' AND legacy_subject_id IS NOT NULL AND "
        "legacy_subject_id = subject_id AND installation_id IS NULL AND "
        "repository_id IS NULL AND workflow_run_id IS NULL AND run_attempt IS NULL AND "
        "head_sha IS NULL AND provider_api_version IS NULL AND source_evidence_digest IS NULL) "
        "OR (source_kind = 'provider_run' AND legacy_subject_id IS NULL AND "
        "installation_id IS NOT NULL AND repository_id IS NOT NULL AND "
        "workflow_run_id IS NOT NULL AND run_attempt IS NOT NULL AND head_sha IS NOT NULL AND "
        "provider_api_version IS NOT NULL AND source_evidence_digest IS NOT NULL AND "
        f"installation_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"repository_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"workflow_run_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"run_attempt BETWEEN 1 AND {_safe_integer_max} AND "
        "head_sha ~ '^[0-9a-f]{40}$' AND provider_api_version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' "
        "AND source_evidence_digest ~ '^[0-9a-f]{64}$')",
        name="ck_ci_workflow_attempt_collections_source",
    ),
    CheckConstraint(
        "source_created_at < deadline_at AND deadline_at < evidence_retain_until AND "
        "evidence_retain_until < tombstone_retain_until AND "
        f"deadline_at = source_created_at + INTERVAL "
        f"'{_ci_economics_profile.collection_policy.collection_window_seconds} seconds' AND "
        f"evidence_retain_until = source_created_at + INTERVAL "
        f"'{_ci_economics_profile.collection_policy.evidence_days} days' AND "
        f"tombstone_retain_until = evidence_retain_until + INTERVAL "
        f"'{_ci_economics_profile.collection_policy.tombstone_grace_seconds} seconds'",
        name="ck_ci_workflow_attempt_collections_lifetime",
    ),
    CheckConstraint(
        f"revision BETWEEN 0 AND {_safe_integer_max} AND "
        f"attempt_count BETWEEN 0 AND max_attempts AND max_attempts BETWEEN 1 AND "
        f"{_ci_economics_profile.collection_policy.maximum_attempts} AND "
        f"claim_generation = attempt_count AND claim_generation <= {_safe_integer_max} AND "
        "revision >= claim_generation AND backoff_seconds BETWEEN 1 AND max_backoff_seconds AND "
        f"max_backoff_seconds BETWEEN 1 AND "
        f"{_ci_economics_profile.collection_policy.maximum_backoff_seconds}",
        name="ck_ci_workflow_attempt_collections_counters",
    ),
    CheckConstraint(
        "status IN ('pending', 'leased', 'deferred', 'captured', "
        "'terminal_unavailable', 'expired') AND "
        "(last_failure_reason IS NULL OR last_failure_reason IN "
        "('provider_unavailable', 'provider_binding_mismatch', 'provider_malformed', "
        "'provider_incomplete', 'provider_not_terminal', 'provider_unstable', "
        "'evidence_conflict', 'unexpected_error')) AND "
        "(final_outcome IS NULL OR final_outcome IN ('captured', 'terminal_unavailable')) AND "
        "(terminal_reason IS NULL OR terminal_reason IN "
        "('deadline_exceeded', 'attempts_exhausted', 'evidence_conflict'))",
        name="ck_ci_workflow_attempt_collections_enums",
    ),
    CheckConstraint(
        "(last_failure_reason IS NOT DISTINCT FROM 'evidence_conflict') = "
        "(terminal_reason IS NOT DISTINCT FROM 'evidence_conflict') AND "
        "(terminal_reason IS DISTINCT FROM 'attempts_exhausted' OR "
        "attempt_count = max_attempts) AND "
        "(final_outcome IS DISTINCT FROM 'captured' OR attempt_count > 0) AND "
        "(terminal_reason IS DISTINCT FROM 'evidence_conflict' OR attempt_count > 0)",
        name="ck_ci_workflow_attempt_collections_causal_history",
    ),
    CheckConstraint(
        "((lease_owner_id IS NULL AND lease_token IS NULL AND lease_acquired_at IS NULL AND "
        "lease_expires_at IS NULL) OR (lease_owner_id ~ '^[0-9a-f]{64}$' AND "
        "lease_token ~ '^[0-9a-f]{64}$' AND lease_acquired_at IS NOT NULL AND "
        "lease_expires_at IS NOT NULL AND lease_acquired_at >= source_created_at AND "
        "lease_expires_at > lease_acquired_at AND lease_expires_at < deadline_at)) IS TRUE",
        name="ck_ci_workflow_attempt_collections_lease",
    ),
    CheckConstraint(
        "(next_attempt_at IS NULL OR (next_attempt_at >= source_created_at AND "
        "next_attempt_at < deadline_at)) AND (completed_at IS NULL OR "
        "completed_at >= source_created_at) AND "
        "(expired_at IS NULL OR expired_at >= evidence_retain_until)",
        name="ck_ci_workflow_attempt_collections_transition_times",
    ),
    CheckConstraint(
        "((status = 'pending' AND revision = 0 AND attempt_count = 0 AND "
        "next_attempt_at IS NOT NULL AND lease_token IS NULL AND last_failure_reason IS NULL AND "
        "final_outcome IS NULL AND terminal_reason IS NULL AND completed_at IS NULL AND "
        "expired_at IS NULL) OR "
        "(status = 'deferred' AND attempt_count > 0 AND next_attempt_at IS NOT NULL AND "
        "lease_token IS NULL AND last_failure_reason IS NOT NULL AND final_outcome IS NULL AND "
        "terminal_reason IS NULL AND completed_at IS NULL AND expired_at IS NULL) OR "
        "(status = 'leased' AND attempt_count > 0 AND next_attempt_at IS NULL AND "
        "lease_token IS NOT NULL AND final_outcome IS NULL AND terminal_reason IS NULL AND "
        "completed_at IS NULL AND expired_at IS NULL) OR "
        "(status = 'captured' AND next_attempt_at IS NULL AND lease_token IS NULL AND "
        "final_outcome = 'captured' AND terminal_reason IS NULL AND completed_at IS NOT NULL AND "
        "completed_at < evidence_retain_until AND "
        "expired_at IS NULL) OR "
        "(status = 'terminal_unavailable' AND next_attempt_at IS NULL AND lease_token IS NULL AND "
        "final_outcome = 'terminal_unavailable' AND terminal_reason IS NOT NULL AND "
        "completed_at IS NOT NULL AND expired_at IS NULL) OR "
        "(status = 'expired' AND next_attempt_at IS NULL AND lease_token IS NULL AND "
        "completed_at IS NOT NULL AND expired_at IS NOT NULL AND "
        "((final_outcome = 'captured' AND terminal_reason IS NULL AND "
        "completed_at < evidence_retain_until) OR "
        "(final_outcome = 'terminal_unavailable' AND terminal_reason IS NOT NULL)))) IS TRUE",
        name="ck_ci_workflow_attempt_collections_state_shape",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_ci_workflow_attempt_collections_audit_times",
    ),
    PrimaryKeyConstraint("subject_id", name="pk_ci_workflow_attempt_collections"),
    UniqueConstraint(
        "subject_id",
        "evidence_retain_until",
        "source_kind",
        name="uq_ci_workflow_attempt_collections_retention",
    ),
    ForeignKeyConstraint(
        ["legacy_subject_id"],
        [f"{APPLICATION_SCHEMA}.reconciliation_subjects.subject_id"],
        ondelete="RESTRICT",
        name="fk_ci_workflow_attempt_collections_legacy_subject",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "uq_ci_workflow_attempt_collections_provider_attempt",
    ci_workflow_attempt_collections.c.installation_id,
    ci_workflow_attempt_collections.c.repository_id,
    ci_workflow_attempt_collections.c.workflow_run_id,
    ci_workflow_attempt_collections.c.run_attempt,
    unique=True,
    postgresql_where=text("source_kind = 'provider_run'"),
)

Index(
    "ix_ci_workflow_attempt_collections_due",
    ci_workflow_attempt_collections.c.next_attempt_at,
    ci_workflow_attempt_collections.c.source_created_at,
    ci_workflow_attempt_collections.c.subject_id,
    postgresql_where=text("status IN ('pending', 'deferred')"),
)

Index(
    "ix_ci_workflow_attempt_collections_lease_expiry",
    ci_workflow_attempt_collections.c.lease_expires_at,
    ci_workflow_attempt_collections.c.source_created_at,
    ci_workflow_attempt_collections.c.subject_id,
    postgresql_where=text("status = 'leased'"),
)

Index(
    "ix_ci_workflow_attempt_collections_expiry",
    ci_workflow_attempt_collections.c.evidence_retain_until,
    ci_workflow_attempt_collections.c.subject_id,
    postgresql_where=text("status IN ('captured', 'terminal_unavailable')"),
)

Index(
    "ix_ci_workflow_attempt_collections_tombstone",
    ci_workflow_attempt_collections.c.tombstone_retain_until,
    ci_workflow_attempt_collections.c.subject_id,
    postgresql_where=text("status = 'expired'"),
)

ci_workflow_attempt_snapshots = Table(
    "ci_workflow_attempt_snapshots",
    metadata,
    Column("subject_id", String(64), nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("workflow_run_id", BigInteger, nullable=False),
    Column("run_attempt", BigInteger, nullable=False),
    Column("head_sha", String(40), nullable=False),
    Column("contract_hash", String(64), nullable=True),
    Column("planned_route", String(32), nullable=False),
    Column("job_count", BigInteger, nullable=False),
    Column("snapshot_digest", String(64), nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("statement_timestamp()"),
    ),
    Column(
        "retain_until",
        DateTime(timezone=True),
        nullable=False,
    ),
    Column("source_kind", String(32), nullable=False),
    CheckConstraint(
        "subject_id ~ '^[0-9a-f]{64}$' AND head_sha ~ '^[0-9a-f]{40}$' AND "
        "snapshot_digest ~ '^[0-9a-f]{64}$'",
        name="ck_ci_workflow_attempt_snapshots_hashes",
    ),
    CheckConstraint(
        "(source_kind = 'reconciliation' AND contract_hash IS NOT NULL AND "
        "contract_hash ~ '^[0-9a-f]{64}$') OR "
        "(source_kind = 'provider_run' AND contract_hash IS NULL AND planned_route = 'unknown')",
        name="ck_ci_workflow_attempt_snapshots_source",
    ),
    CheckConstraint(
        f"installation_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"repository_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"workflow_run_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"run_attempt BETWEEN 1 AND {_safe_integer_max} AND "
        "job_count BETWEEN 1 AND 2000",
        name="ck_ci_workflow_attempt_snapshots_identity_safe",
    ),
    CheckConstraint(
        "planned_route IN ('selected', 'full_ci_counterfactual', 'unknown')",
        name="ck_ci_workflow_attempt_snapshots_planned_route",
    ),
    CheckConstraint(
        "recorded_at < retain_until",
        name="ck_ci_workflow_attempt_snapshots_retention",
    ),
    PrimaryKeyConstraint("subject_id", name="pk_ci_workflow_attempt_snapshots"),
    UniqueConstraint(
        "installation_id",
        "repository_id",
        "workflow_run_id",
        "run_attempt",
        "head_sha",
        name="uq_ci_workflow_attempt_snapshots_attempt",
    ),
    ForeignKeyConstraint(
        ["subject_id", "retain_until", "source_kind"],
        [
            f"{APPLICATION_SCHEMA}.ci_workflow_attempt_collections.subject_id",
            f"{APPLICATION_SCHEMA}.ci_workflow_attempt_collections.evidence_retain_until",
            f"{APPLICATION_SCHEMA}.ci_workflow_attempt_collections.source_kind",
        ],
        ondelete="RESTRICT",
        name="fk_ci_workflow_attempt_snapshots_collection",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_ci_workflow_attempt_snapshots_scope_recorded",
    ci_workflow_attempt_snapshots.c.installation_id,
    ci_workflow_attempt_snapshots.c.repository_id,
    ci_workflow_attempt_snapshots.c.recorded_at.desc(),
    ci_workflow_attempt_snapshots.c.subject_id.desc(),
)

Index(
    "ix_ci_workflow_attempt_snapshots_retention",
    ci_workflow_attempt_snapshots.c.retain_until,
    ci_workflow_attempt_snapshots.c.subject_id,
)

ci_workflow_attempt_snapshot_jobs = Table(
    "ci_workflow_attempt_snapshot_jobs",
    metadata,
    Column("subject_id", String(64), nullable=False),
    Column("provider_job_id", BigInteger, nullable=False),
    Column("name", String(MAX_JOB_NAME_CODE_POINTS), nullable=False),
    Column("conclusion", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("runner_id", BigInteger, nullable=True),
    Column("runner_name", String(MAX_RUNNER_TEXT_CODE_POINTS), nullable=True),
    Column("runner_group_id", BigInteger, nullable=True),
    Column("runner_group_name", String(MAX_RUNNER_TEXT_CODE_POINTS), nullable=True),
    Column("labels_canonical_json", LargeBinary, nullable=False),
    Column("semantic_hash", String(64), nullable=False),
    Column("job_canonical_json", LargeBinary, nullable=False),
    CheckConstraint(
        f"provider_job_id BETWEEN 1 AND {_safe_integer_max} AND "
        f"char_length(name) BETWEEN 1 AND {MAX_JOB_NAME_CODE_POINTS}",
        name="ck_ci_workflow_attempt_snapshot_jobs_identity",
    ),
    CheckConstraint(
        "conclusion IN ('success', 'failure', 'cancelled', 'timed_out', 'skipped', "
        "'neutral', 'action_required', 'startup_failure', 'stale')",
        name="ck_ci_workflow_attempt_snapshot_jobs_conclusion",
    ),
    CheckConstraint(
        "(created_at IS NULL OR started_at IS NULL OR created_at <= started_at) AND "
        "(started_at IS NULL OR completed_at IS NULL OR started_at <= completed_at) AND "
        "(created_at IS NULL OR completed_at IS NULL OR created_at <= completed_at)",
        name="ck_ci_workflow_attempt_snapshot_jobs_temporal_order",
    ),
    CheckConstraint(
        f"(runner_id IS NULL OR runner_id BETWEEN 1 AND {_safe_integer_max}) AND "
        f"(runner_group_id IS NULL OR runner_group_id BETWEEN 1 AND {_safe_integer_max}) AND "
        f"(runner_name IS NULL OR char_length(runner_name) BETWEEN 1 AND "
        f"{MAX_RUNNER_TEXT_CODE_POINTS}) AND "
        f"(runner_group_name IS NULL OR char_length(runner_group_name) BETWEEN 1 AND "
        f"{MAX_RUNNER_TEXT_CODE_POINTS}) AND "
        "((runner_id IS NULL) = (runner_name IS NULL)) AND "
        "((runner_group_id IS NULL) = (runner_group_name IS NULL)) AND "
        "(runner_group_id IS NULL OR runner_id IS NOT NULL)",
        name="ck_ci_workflow_attempt_snapshot_jobs_runner",
    ),
    CheckConstraint(
        f"jsonb_typeof(convert_from(labels_canonical_json, 'UTF8')::jsonb) = 'array' AND "
        f"jsonb_array_length(convert_from(labels_canonical_json, 'UTF8')::jsonb) <= "
        f"{MAX_JOB_LABELS}",
        name="ck_ci_workflow_attempt_snapshot_jobs_labels",
    ),
    CheckConstraint(
        f"semantic_hash ~ '^[0-9a-f]{{64}}$' AND "
        f"octet_length(job_canonical_json) BETWEEN 1 AND {_MAX_SNAPSHOT_JOB_CANONICAL_BYTES}",
        name="ck_ci_workflow_attempt_snapshot_jobs_canonical",
    ),
    PrimaryKeyConstraint(
        "subject_id",
        "provider_job_id",
        name="pk_ci_workflow_attempt_snapshot_jobs",
    ),
    ForeignKeyConstraint(
        ["subject_id"],
        [f"{APPLICATION_SCHEMA}.ci_workflow_attempt_snapshots.subject_id"],
        ondelete="CASCADE",
        name="fk_ci_workflow_attempt_snapshot_jobs_snapshot",
    ),
    schema=APPLICATION_SCHEMA,
)
