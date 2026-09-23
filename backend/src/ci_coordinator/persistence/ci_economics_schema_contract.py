"""Independent PostgreSQL catalog contract for CI economics evidence."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

type ColumnFact = tuple[str, str, str, bool, str | None]
type ConstraintFact = tuple[str, str, str, bool, bool, bool, bool]
type IndexFact = tuple[str, str, bool, bool, bool, bool, bool, bool]


@dataclass(frozen=True, slots=True)
class EconomicsCatalogContract:
    relations: tuple[str, ...]
    columns: tuple[ColumnFact, ...]
    constraints: frozenset[ConstraintFact]
    constraint_definitions: Mapping[tuple[str, str], str]
    indexes: frozenset[IndexFact]
    index_definitions: Mapping[tuple[str, str], str]
    tables: tuple[tuple[object, ...], ...]
    triggers: tuple[tuple[object, ...], ...]
    rewrite_rules: tuple[tuple[object, ...], ...]
    routine_source: str | None


EXPECTED_RELATIONS: Final = (
    "ci_workflow_attempt_collections",
    "ci_workflow_attempt_snapshot_jobs",
    "ci_workflow_attempt_snapshots",
    "ci_workflow_observations",
)
EXPECTED_COLUMNS: Final[tuple[ColumnFact, ...]] = (
    (
        "ci_workflow_attempt_collections",
        "subject_id",
        "character varying(64)",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "policy_hash",
        "character varying(64)",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "source_created_at",
        "timestamp with time zone",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "deadline_at",
        "timestamp with time zone",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "evidence_retain_until",
        "timestamp with time zone",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "tombstone_retain_until",
        "timestamp with time zone",
        False,
        None,
    ),
    ("ci_workflow_attempt_collections", "status", "character varying(32)", False, None),
    ("ci_workflow_attempt_collections", "revision", "bigint", False, None),
    ("ci_workflow_attempt_collections", "attempt_count", "bigint", False, None),
    ("ci_workflow_attempt_collections", "max_attempts", "bigint", False, None),
    ("ci_workflow_attempt_collections", "backoff_seconds", "bigint", False, None),
    (
        "ci_workflow_attempt_collections",
        "max_backoff_seconds",
        "bigint",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "next_attempt_at",
        "timestamp with time zone",
        True,
        None,
    ),
    ("ci_workflow_attempt_collections", "claim_generation", "bigint", False, None),
    (
        "ci_workflow_attempt_collections",
        "lease_owner_id",
        "character varying(64)",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "lease_token",
        "character varying(64)",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "lease_acquired_at",
        "timestamp with time zone",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "lease_expires_at",
        "timestamp with time zone",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "last_failure_reason",
        "character varying(64)",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "final_outcome",
        "character varying(32)",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "terminal_reason",
        "character varying(32)",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "completed_at",
        "timestamp with time zone",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "expired_at",
        "timestamp with time zone",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_collections",
        "created_at",
        "timestamp with time zone",
        False,
        "statement_timestamp()",
    ),
    (
        "ci_workflow_attempt_collections",
        "updated_at",
        "timestamp with time zone",
        False,
        "statement_timestamp()",
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "subject_id",
        "character varying(64)",
        False,
        None,
    ),
    ("ci_workflow_attempt_snapshot_jobs", "provider_job_id", "bigint", False, None),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "name",
        "character varying(512)",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "conclusion",
        "character varying(32)",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "created_at",
        "timestamp with time zone",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "started_at",
        "timestamp with time zone",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "completed_at",
        "timestamp with time zone",
        True,
        None,
    ),
    ("ci_workflow_attempt_snapshot_jobs", "runner_id", "bigint", True, None),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "runner_name",
        "character varying(256)",
        True,
        None,
    ),
    ("ci_workflow_attempt_snapshot_jobs", "runner_group_id", "bigint", True, None),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "runner_group_name",
        "character varying(256)",
        True,
        None,
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "labels_canonical_json",
        "bytea",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "semantic_hash",
        "character varying(64)",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "job_canonical_json",
        "bytea",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_snapshots",
        "subject_id",
        "character varying(64)",
        False,
        None,
    ),
    ("ci_workflow_attempt_snapshots", "installation_id", "bigint", False, None),
    ("ci_workflow_attempt_snapshots", "repository_id", "bigint", False, None),
    ("ci_workflow_attempt_snapshots", "workflow_run_id", "bigint", False, None),
    ("ci_workflow_attempt_snapshots", "run_attempt", "bigint", False, None),
    (
        "ci_workflow_attempt_snapshots",
        "head_sha",
        "character varying(40)",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_snapshots",
        "contract_hash",
        "character varying(64)",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_snapshots",
        "planned_route",
        "character varying(32)",
        False,
        None,
    ),
    ("ci_workflow_attempt_snapshots", "job_count", "bigint", False, None),
    (
        "ci_workflow_attempt_snapshots",
        "snapshot_digest",
        "character varying(64)",
        False,
        None,
    ),
    (
        "ci_workflow_attempt_snapshots",
        "recorded_at",
        "timestamp with time zone",
        False,
        "statement_timestamp()",
    ),
    (
        "ci_workflow_attempt_snapshots",
        "retain_until",
        "timestamp with time zone",
        False,
        None,
    ),
    (
        "ci_workflow_observations",
        "delivery_id",
        "character varying(128)",
        False,
        None,
    ),
    (
        "ci_workflow_observations",
        "observation_kind",
        "character varying(32)",
        False,
        None,
    ),
    ("ci_workflow_observations", "installation_id", "bigint", False, None),
    ("ci_workflow_observations", "repository_id", "bigint", False, None),
    ("ci_workflow_observations", "workflow_run_id", "bigint", False, None),
    ("ci_workflow_observations", "run_attempt", "bigint", False, None),
    (
        "ci_workflow_observations",
        "head_sha",
        "character varying(40)",
        False,
        None,
    ),
    ("ci_workflow_observations", "provider_job_id", "bigint", True, None),
    (
        "ci_workflow_observations",
        "semantic_hash",
        "character varying(64)",
        False,
        None,
    ),
    ("ci_workflow_observations", "observation_canonical_json", "bytea", False, None),
    (
        "ci_workflow_observations",
        "recorded_at",
        "timestamp with time zone",
        False,
        "statement_timestamp()",
    ),
    (
        "ci_workflow_observations",
        "retain_until",
        "timestamp with time zone",
        False,
        "(statement_timestamp() + '90 days'::interval)",
    ),
)
EXPECTED_CONSTRAINTS: Final[frozenset[ConstraintFact]] = frozenset(
    {
        (relation, name, kind, False, False, True, True)
        for relation, name, kind in (
            (
                "ci_workflow_attempt_collections",
                "ck_ci_workflow_attempt_collections_audit_times",
                "c",
            ),
            (
                "ci_workflow_attempt_collections",
                "ck_ci_workflow_attempt_collections_counters",
                "c",
            ),
            (
                "ci_workflow_attempt_collections",
                "ck_ci_workflow_attempt_collections_causal_history",
                "c",
            ),
            (
                "ci_workflow_attempt_collections",
                "ck_ci_workflow_attempt_collections_enums",
                "c",
            ),
            (
                "ci_workflow_attempt_collections",
                "ck_ci_workflow_attempt_collections_lease",
                "c",
            ),
            (
                "ci_workflow_attempt_collections",
                "ck_ci_workflow_attempt_collections_lifetime",
                "c",
            ),
            (
                "ci_workflow_attempt_collections",
                "ck_ci_workflow_attempt_collections_state_shape",
                "c",
            ),
            (
                "ci_workflow_attempt_collections",
                "ck_ci_workflow_attempt_collections_subject",
                "c",
            ),
            (
                "ci_workflow_attempt_collections",
                "ck_ci_workflow_attempt_collections_transition_times",
                "c",
            ),
            (
                "ci_workflow_attempt_collections",
                "fk_ci_workflow_attempt_collections_subject",
                "f",
            ),
            (
                "ci_workflow_attempt_collections",
                "pk_ci_workflow_attempt_collections",
                "p",
            ),
            (
                "ci_workflow_attempt_collections",
                "uq_ci_workflow_attempt_collections_retention",
                "u",
            ),
            (
                "ci_workflow_attempt_snapshot_jobs",
                "ck_ci_workflow_attempt_snapshot_jobs_canonical",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshot_jobs",
                "ck_ci_workflow_attempt_snapshot_jobs_conclusion",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshot_jobs",
                "ck_ci_workflow_attempt_snapshot_jobs_identity",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshot_jobs",
                "ck_ci_workflow_attempt_snapshot_jobs_labels",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshot_jobs",
                "ck_ci_workflow_attempt_snapshot_jobs_runner",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshot_jobs",
                "ck_ci_workflow_attempt_snapshot_jobs_temporal_order",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshot_jobs",
                "fk_ci_workflow_attempt_snapshot_jobs_snapshot",
                "f",
            ),
            (
                "ci_workflow_attempt_snapshot_jobs",
                "pk_ci_workflow_attempt_snapshot_jobs",
                "p",
            ),
            (
                "ci_workflow_attempt_snapshots",
                "ck_ci_workflow_attempt_snapshots_hashes",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshots",
                "ck_ci_workflow_attempt_snapshots_identity_safe",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshots",
                "ck_ci_workflow_attempt_snapshots_planned_route",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshots",
                "ck_ci_workflow_attempt_snapshots_retention",
                "c",
            ),
            (
                "ci_workflow_attempt_snapshots",
                "fk_ci_workflow_attempt_snapshots_collection",
                "f",
            ),
            (
                "ci_workflow_attempt_snapshots",
                "pk_ci_workflow_attempt_snapshots",
                "p",
            ),
            (
                "ci_workflow_attempt_snapshots",
                "uq_ci_workflow_attempt_snapshots_attempt",
                "u",
            ),
            (
                "ci_workflow_observations",
                "ck_ci_workflow_observations_canonical_byte_limit",
                "c",
            ),
            ("ci_workflow_observations", "ck_ci_workflow_observations_hashes", "c"),
            (
                "ci_workflow_observations",
                "ck_ci_workflow_observations_identity_safe",
                "c",
            ),
            (
                "ci_workflow_observations",
                "ck_ci_workflow_observations_job_identity",
                "c",
            ),
            ("ci_workflow_observations", "ck_ci_workflow_observations_kind", "c"),
            (
                "ci_workflow_observations",
                "ck_ci_workflow_observations_retention",
                "c",
            ),
            (
                "ci_workflow_observations",
                "fk_ci_workflow_observations_delivery",
                "f",
            ),
            ("ci_workflow_observations", "pk_ci_workflow_observations", "p"),
        )
    }
)
EXPECTED_INDEXES: Final[frozenset[IndexFact]] = frozenset(
    {
        (relation, name, False, False, True, True, True, partial)
        for relation, name, partial in (
            (
                "ci_workflow_attempt_collections",
                "ix_ci_workflow_attempt_collections_due",
                True,
            ),
            (
                "ci_workflow_attempt_collections",
                "ix_ci_workflow_attempt_collections_expiry",
                True,
            ),
            (
                "ci_workflow_attempt_collections",
                "ix_ci_workflow_attempt_collections_lease_expiry",
                True,
            ),
            (
                "ci_workflow_attempt_collections",
                "ix_ci_workflow_attempt_collections_tombstone",
                True,
            ),
            (
                "ci_workflow_attempt_snapshots",
                "ix_ci_workflow_attempt_snapshots_retention",
                False,
            ),
            (
                "ci_workflow_attempt_snapshots",
                "ix_ci_workflow_attempt_snapshots_scope_recorded",
                False,
            ),
            (
                "ci_workflow_observations",
                "ix_ci_workflow_observations_attempt_jobs",
                True,
            ),
            (
                "ci_workflow_observations",
                "ix_ci_workflow_observations_retention",
                False,
            ),
        )
    }
)


EXPECTED_TABLES: Final = tuple(
    (relation, "r", "p", False, False, True, True) for relation in EXPECTED_RELATIONS
)
EXPECTED_TRIGGERS: Final = (
    (
        "ci_workflow_attempt_collections",
        "tr_ci_workflow_attempt_collections_retention_guard",
        "guard_ci_economics_mutation",
        "ci_coordinator",
        False,
        "O",
        11,
        True,
        True,
        0,
        0,
        "",
        True,
        True,
        False,
        False,
    ),
    (
        "ci_workflow_attempt_snapshots",
        "tr_ci_workflow_attempt_snapshots_retention_guard",
        "guard_ci_economics_mutation",
        "ci_coordinator",
        False,
        "O",
        27,
        True,
        True,
        0,
        0,
        "",
        True,
        True,
        False,
        False,
    ),
    (
        "ci_workflow_observations",
        "tr_ci_workflow_observations_retention_guard",
        "guard_ci_economics_mutation",
        "ci_coordinator",
        False,
        "O",
        27,
        True,
        True,
        0,
        0,
        "",
        True,
        True,
        False,
        False,
    ),
)
EXPECTED_REWRITE_RULES: Final[tuple[tuple[object, ...], ...]] = ()

EXPECTED_CONSTRAINT_DEFINITIONS: Final = {
    (
        "ci_workflow_attempt_collections",
        "ck_ci_workflow_attempt_collections_audit_times",
    ): "CHECK (updated_at >= created_at)",
    (
        "ci_workflow_attempt_collections",
        "ck_ci_workflow_attempt_collections_counters",
    ): (
        "CHECK (revision >= 0 AND revision <= '9007199254740991'::bigint AND "
        "attempt_count >= 0 AND attempt_count <= max_attempts AND max_attempts >= 1 AND "
        "max_attempts <= 20 AND claim_generation = attempt_count AND claim_generation <= "
        "'9007199254740991'::bigint AND revision >= claim_generation AND "
        "backoff_seconds >= 1 AND backoff_seconds <= max_backoff_seconds AND "
        "max_backoff_seconds >= 1 AND max_backoff_seconds <= 3600)"
    ),
    (
        "ci_workflow_attempt_collections",
        "ck_ci_workflow_attempt_collections_causal_history",
    ): (
        "CHECK ((NOT last_failure_reason::text IS DISTINCT FROM "
        "'evidence_conflict'::text) = (NOT terminal_reason::text IS DISTINCT FROM "
        "'evidence_conflict'::text) AND (terminal_reason::text IS DISTINCT FROM "
        "'attempts_exhausted'::text OR attempt_count = max_attempts) AND "
        "(final_outcome::text IS DISTINCT FROM 'captured'::text OR attempt_count > 0) AND "
        "(terminal_reason::text IS DISTINCT FROM 'evidence_conflict'::text OR "
        "attempt_count > 0))"
    ),
    (
        "ci_workflow_attempt_collections",
        "ck_ci_workflow_attempt_collections_enums",
    ): (
        "CHECK ((status::text = ANY (ARRAY['pending'::character varying, "
        "'leased'::character varying, 'deferred'::character varying, "
        "'captured'::character varying, 'terminal_unavailable'::character varying, "
        "'expired'::character varying]::text[])) AND (last_failure_reason IS NULL OR "
        "(last_failure_reason::text = ANY (ARRAY['provider_unavailable'::character varying, "
        "'provider_binding_mismatch'::character varying, 'provider_malformed'::character varying, "
        "'provider_incomplete'::character varying, 'provider_not_terminal'::character varying, "
        "'provider_unstable'::character varying, 'evidence_conflict'::character varying, "
        "'unexpected_error'::character varying]::text[]))) AND (final_outcome IS NULL OR "
        "(final_outcome::text = ANY (ARRAY['captured'::character varying, "
        "'terminal_unavailable'::character varying]::text[]))) AND (terminal_reason IS NULL OR "
        "(terminal_reason::text = ANY (ARRAY['deadline_exceeded'::character varying, "
        "'attempts_exhausted'::character varying, "
        "'evidence_conflict'::character varying]::text[]))))"
    ),
    (
        "ci_workflow_attempt_collections",
        "ck_ci_workflow_attempt_collections_lease",
    ): (
        "CHECK (lease_owner_id IS NULL AND lease_token IS NULL AND "
        "lease_acquired_at IS NULL AND lease_expires_at IS NULL OR "
        "lease_owner_id::text ~ '^[0-9a-f]{64}$'::text AND "
        "lease_token::text ~ '^[0-9a-f]{64}$'::text AND lease_acquired_at IS NOT NULL AND "
        "lease_expires_at IS NOT NULL AND lease_acquired_at >= source_created_at AND "
        "lease_expires_at > lease_acquired_at AND lease_expires_at < deadline_at)"
    ),
    (
        "ci_workflow_attempt_collections",
        "ck_ci_workflow_attempt_collections_lifetime",
    ): (
        "CHECK (source_created_at < deadline_at AND deadline_at < evidence_retain_until AND "
        "evidence_retain_until < tombstone_retain_until AND deadline_at = "
        "(source_created_at + '168:00:00'::interval) AND evidence_retain_until = "
        "(source_created_at + '90 days'::interval) AND tombstone_retain_until = "
        "(evidence_retain_until + '24:00:00'::interval))"
    ),
    (
        "ci_workflow_attempt_collections",
        "ck_ci_workflow_attempt_collections_state_shape",
    ): (
        "CHECK (status::text = 'pending'::text AND revision = 0 AND attempt_count = 0 AND "
        "next_attempt_at IS NOT NULL AND lease_token IS NULL AND last_failure_reason IS NULL AND "
        "final_outcome IS NULL AND terminal_reason IS NULL AND completed_at IS NULL AND "
        "expired_at IS NULL OR status::text = 'deferred'::text AND attempt_count > 0 AND "
        "next_attempt_at IS NOT NULL AND lease_token IS NULL AND last_failure_reason IS NOT NULL "
        "AND final_outcome IS NULL AND terminal_reason IS NULL AND completed_at IS NULL AND "
        "expired_at IS NULL OR status::text = 'leased'::text AND attempt_count > 0 AND "
        "next_attempt_at IS NULL AND lease_token IS NOT NULL AND final_outcome IS NULL AND "
        "terminal_reason IS NULL AND completed_at IS NULL AND expired_at IS NULL OR "
        "status::text = 'captured'::text AND next_attempt_at IS NULL AND lease_token IS NULL AND "
        "final_outcome::text = 'captured'::text AND terminal_reason IS NULL AND "
        "completed_at IS NOT NULL AND completed_at < evidence_retain_until AND "
        "expired_at IS NULL OR status::text = 'terminal_unavailable'::text AND "
        "next_attempt_at IS NULL AND lease_token IS NULL AND "
        "final_outcome::text = 'terminal_unavailable'::text AND terminal_reason IS NOT NULL AND "
        "completed_at IS NOT NULL AND expired_at IS NULL OR status::text = 'expired'::text AND "
        "next_attempt_at IS NULL AND lease_token IS NULL AND completed_at IS NOT NULL AND "
        "expired_at IS NOT NULL AND (final_outcome::text = 'captured'::text AND "
        "terminal_reason IS NULL AND completed_at < evidence_retain_until OR "
        "final_outcome::text = 'terminal_unavailable'::text AND terminal_reason IS NOT NULL))"
    ),
    (
        "ci_workflow_attempt_collections",
        "ck_ci_workflow_attempt_collections_subject",
    ): (
        "CHECK (subject_id::text ~ '^[0-9a-f]{64}$'::text AND "
        "policy_hash::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (
        "ci_workflow_attempt_collections",
        "ck_ci_workflow_attempt_collections_transition_times",
    ): (
        "CHECK ((next_attempt_at IS NULL OR next_attempt_at >= source_created_at AND "
        "next_attempt_at < deadline_at) AND (completed_at IS NULL OR "
        "completed_at >= source_created_at) AND (expired_at IS NULL OR "
        "expired_at >= evidence_retain_until))"
    ),
    (
        "ci_workflow_attempt_collections",
        "fk_ci_workflow_attempt_collections_subject",
    ): (
        "FOREIGN KEY (subject_id) REFERENCES "
        "ci_coordinator.reconciliation_subjects(subject_id) ON DELETE RESTRICT"
    ),
    (
        "ci_workflow_attempt_collections",
        "pk_ci_workflow_attempt_collections",
    ): "PRIMARY KEY (subject_id)",
    (
        "ci_workflow_attempt_collections",
        "uq_ci_workflow_attempt_collections_retention",
    ): "UNIQUE (subject_id, evidence_retain_until)",
    (
        "ci_workflow_attempt_snapshot_jobs",
        "ck_ci_workflow_attempt_snapshot_jobs_canonical",
    ): (
        "CHECK (semantic_hash::text ~ '^[0-9a-f]{64}$'::text AND "
        "octet_length(job_canonical_json) >= 1 AND octet_length(job_canonical_json) <= 8192)"
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "ck_ci_workflow_attempt_snapshot_jobs_conclusion",
    ): (
        "CHECK (conclusion::text = ANY (ARRAY['success'::character varying, "
        "'failure'::character varying, 'cancelled'::character varying, "
        "'timed_out'::character varying, 'skipped'::character varying, "
        "'neutral'::character varying, 'action_required'::character varying, "
        "'startup_failure'::character varying, 'stale'::character varying]::text[]))"
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "ck_ci_workflow_attempt_snapshot_jobs_identity",
    ): (
        "CHECK (provider_job_id >= 1 AND provider_job_id <= '9007199254740991'::bigint AND "
        "char_length(name::text) >= 1 AND char_length(name::text) <= 512)"
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "ck_ci_workflow_attempt_snapshot_jobs_labels",
    ): (
        "CHECK (jsonb_typeof(convert_from(labels_canonical_json, 'UTF8'::name)::jsonb) = "
        "'array'::text AND jsonb_array_length(convert_from(labels_canonical_json, "
        "'UTF8'::name)::jsonb) <= 32)"
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "ck_ci_workflow_attempt_snapshot_jobs_runner",
    ): (
        "CHECK ((runner_id IS NULL OR runner_id >= 1 AND runner_id <= "
        "'9007199254740991'::bigint) AND (runner_group_id IS NULL OR "
        "runner_group_id >= 1 AND runner_group_id <= '9007199254740991'::bigint) AND "
        "(runner_name IS NULL OR char_length(runner_name::text) >= 1 AND "
        "char_length(runner_name::text) <= 256) AND (runner_group_name IS NULL OR "
        "char_length(runner_group_name::text) >= 1 AND "
        "char_length(runner_group_name::text) <= 256) AND "
        "(runner_id IS NULL) = (runner_name IS NULL) AND "
        "(runner_group_id IS NULL) = (runner_group_name IS NULL) AND "
        "(runner_group_id IS NULL OR runner_id IS NOT NULL))"
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "ck_ci_workflow_attempt_snapshot_jobs_temporal_order",
    ): (
        "CHECK ((created_at IS NULL OR started_at IS NULL OR created_at <= started_at) AND "
        "(started_at IS NULL OR completed_at IS NULL OR started_at <= completed_at) AND "
        "(created_at IS NULL OR completed_at IS NULL OR created_at <= completed_at))"
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "fk_ci_workflow_attempt_snapshot_jobs_snapshot",
    ): (
        "FOREIGN KEY (subject_id) REFERENCES "
        "ci_coordinator.ci_workflow_attempt_snapshots(subject_id) ON DELETE CASCADE"
    ),
    (
        "ci_workflow_attempt_snapshot_jobs",
        "pk_ci_workflow_attempt_snapshot_jobs",
    ): "PRIMARY KEY (subject_id, provider_job_id)",
    (
        "ci_workflow_attempt_snapshots",
        "ck_ci_workflow_attempt_snapshots_hashes",
    ): (
        "CHECK (subject_id::text ~ '^[0-9a-f]{64}$'::text AND "
        "head_sha::text ~ '^[0-9a-f]{40}$'::text AND "
        "contract_hash::text ~ '^[0-9a-f]{64}$'::text AND "
        "snapshot_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (
        "ci_workflow_attempt_snapshots",
        "ck_ci_workflow_attempt_snapshots_identity_safe",
    ): (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint AND "
        "repository_id >= 1 AND repository_id <= '9007199254740991'::bigint AND "
        "workflow_run_id >= 1 AND workflow_run_id <= '9007199254740991'::bigint AND "
        "run_attempt >= 1 AND run_attempt <= '9007199254740991'::bigint AND "
        "job_count >= 1 AND job_count <= 2000)"
    ),
    (
        "ci_workflow_attempt_snapshots",
        "ck_ci_workflow_attempt_snapshots_planned_route",
    ): (
        "CHECK (planned_route::text = ANY (ARRAY['selected'::character varying, "
        "'full_ci_counterfactual'::character varying, "
        "'unknown'::character varying]::text[]))"
    ),
    (
        "ci_workflow_attempt_snapshots",
        "ck_ci_workflow_attempt_snapshots_retention",
    ): "CHECK (recorded_at < retain_until)",
    (
        "ci_workflow_attempt_snapshots",
        "fk_ci_workflow_attempt_snapshots_collection",
    ): (
        "FOREIGN KEY (subject_id, retain_until) REFERENCES "
        "ci_coordinator.ci_workflow_attempt_collections(subject_id, "
        "evidence_retain_until) ON DELETE RESTRICT"
    ),
    (
        "ci_workflow_attempt_snapshots",
        "pk_ci_workflow_attempt_snapshots",
    ): "PRIMARY KEY (subject_id)",
    (
        "ci_workflow_attempt_snapshots",
        "uq_ci_workflow_attempt_snapshots_attempt",
    ): "UNIQUE (installation_id, repository_id, workflow_run_id, run_attempt, head_sha)",
    (
        "ci_workflow_observations",
        "ck_ci_workflow_observations_canonical_byte_limit",
    ): (
        "CHECK (octet_length(observation_canonical_json) >= 1 AND "
        "octet_length(observation_canonical_json) <= 16384)"
    ),
    (
        "ci_workflow_observations",
        "ck_ci_workflow_observations_hashes",
    ): (
        "CHECK (head_sha::text ~ '^[0-9a-f]{40}$'::text AND "
        "semantic_hash::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (
        "ci_workflow_observations",
        "ck_ci_workflow_observations_identity_safe",
    ): (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint AND "
        "repository_id >= 1 AND repository_id <= '9007199254740991'::bigint AND "
        "workflow_run_id >= 1 AND workflow_run_id <= '9007199254740991'::bigint AND "
        "run_attempt >= 1 AND run_attempt <= '9007199254740991'::bigint AND "
        "(provider_job_id IS NULL OR provider_job_id >= 1 AND provider_job_id <= "
        "'9007199254740991'::bigint))"
    ),
    (
        "ci_workflow_observations",
        "ck_ci_workflow_observations_job_identity",
    ): (
        "CHECK (observation_kind::text = 'workflow_job'::text AND provider_job_id IS NOT NULL OR "
        "observation_kind::text = 'workflow_run'::text AND provider_job_id IS NULL)"
    ),
    (
        "ci_workflow_observations",
        "ck_ci_workflow_observations_kind",
    ): (
        "CHECK (observation_kind::text = ANY (ARRAY['workflow_job'::character varying, "
        "'workflow_run'::character varying]::text[]))"
    ),
    (
        "ci_workflow_observations",
        "ck_ci_workflow_observations_retention",
    ): "CHECK (retain_until = (recorded_at + '90 days'::interval))",
    (
        "ci_workflow_observations",
        "fk_ci_workflow_observations_delivery",
    ): (
        "FOREIGN KEY (delivery_id) REFERENCES "
        "ci_coordinator.webhook_deliveries(delivery_id) ON DELETE RESTRICT"
    ),
    (
        "ci_workflow_observations",
        "pk_ci_workflow_observations",
    ): "PRIMARY KEY (delivery_id)",
}

EXPECTED_INDEX_DEFINITIONS: Final = {
    (
        "ci_workflow_attempt_collections",
        "ix_ci_workflow_attempt_collections_due",
    ): (
        "CREATE INDEX ix_ci_workflow_attempt_collections_due ON "
        "ci_coordinator.ci_workflow_attempt_collections USING btree "
        "(next_attempt_at, source_created_at, subject_id) WHERE ((status)::text = ANY "
        "((ARRAY['pending'::character varying, 'deferred'::character varying])::text[]))"
    ),
    (
        "ci_workflow_attempt_collections",
        "ix_ci_workflow_attempt_collections_expiry",
    ): (
        "CREATE INDEX ix_ci_workflow_attempt_collections_expiry ON "
        "ci_coordinator.ci_workflow_attempt_collections USING btree "
        "(evidence_retain_until, subject_id) WHERE ((status)::text = ANY "
        "((ARRAY['captured'::character varying, "
        "'terminal_unavailable'::character varying])::text[]))"
    ),
    (
        "ci_workflow_attempt_collections",
        "ix_ci_workflow_attempt_collections_lease_expiry",
    ): (
        "CREATE INDEX ix_ci_workflow_attempt_collections_lease_expiry ON "
        "ci_coordinator.ci_workflow_attempt_collections USING btree "
        "(lease_expires_at, source_created_at, subject_id) WHERE "
        "((status)::text = 'leased'::text)"
    ),
    (
        "ci_workflow_attempt_collections",
        "ix_ci_workflow_attempt_collections_tombstone",
    ): (
        "CREATE INDEX ix_ci_workflow_attempt_collections_tombstone ON "
        "ci_coordinator.ci_workflow_attempt_collections USING btree "
        "(tombstone_retain_until, subject_id) WHERE ((status)::text = 'expired'::text)"
    ),
    (
        "ci_workflow_attempt_snapshots",
        "ix_ci_workflow_attempt_snapshots_retention",
    ): (
        "CREATE INDEX ix_ci_workflow_attempt_snapshots_retention ON "
        "ci_coordinator.ci_workflow_attempt_snapshots USING btree (retain_until, subject_id)"
    ),
    (
        "ci_workflow_attempt_snapshots",
        "ix_ci_workflow_attempt_snapshots_scope_recorded",
    ): (
        "CREATE INDEX ix_ci_workflow_attempt_snapshots_scope_recorded ON "
        "ci_coordinator.ci_workflow_attempt_snapshots USING btree "
        "(installation_id, repository_id, recorded_at DESC, subject_id DESC)"
    ),
    (
        "ci_workflow_observations",
        "ix_ci_workflow_observations_attempt_jobs",
    ): (
        "CREATE INDEX ix_ci_workflow_observations_attempt_jobs ON "
        "ci_coordinator.ci_workflow_observations USING btree (installation_id, "
        "repository_id, workflow_run_id, run_attempt, head_sha, provider_job_id, "
        "semantic_hash, delivery_id) WHERE "
        "((observation_kind)::text = 'workflow_job'::text)"
    ),
    (
        "ci_workflow_observations",
        "ix_ci_workflow_observations_retention",
    ): (
        "CREATE INDEX ix_ci_workflow_observations_retention ON "
        "ci_coordinator.ci_workflow_observations USING btree (retain_until, delivery_id)"
    ),
}

EXPECTED_ROUTINE_SOURCE: Final = (
    "BEGIN IF TG_TABLE_NAME = 'ci_workflow_attempt_collections' THEN IF OLD.status <> "
    "'expired' OR OLD.tombstone_retain_until > statement_timestamp() THEN RAISE EXCEPTION "
    "'CI economics tombstone has not expired' USING ERRCODE = '55000'; END IF; RETURN OLD; "
    "END IF; IF TG_OP = 'UPDATE' THEN RAISE EXCEPTION 'CI economics evidence is immutable' "
    "USING ERRCODE = '55000'; END IF; IF OLD.retain_until > statement_timestamp() THEN "
    "RAISE EXCEPTION 'CI economics evidence has not expired' USING ERRCODE = '55000'; "
    "END IF; RETURN OLD; END;"
)

V1_CATALOG: Final = EconomicsCatalogContract(
    relations=EXPECTED_RELATIONS,
    columns=EXPECTED_COLUMNS,
    constraints=EXPECTED_CONSTRAINTS,
    constraint_definitions=EXPECTED_CONSTRAINT_DEFINITIONS,
    indexes=EXPECTED_INDEXES,
    index_definitions=EXPECTED_INDEX_DEFINITIONS,
    tables=EXPECTED_TABLES,
    triggers=EXPECTED_TRIGGERS,
    rewrite_rules=EXPECTED_REWRITE_RULES,
    routine_source=EXPECTED_ROUTINE_SOURCE,
)
