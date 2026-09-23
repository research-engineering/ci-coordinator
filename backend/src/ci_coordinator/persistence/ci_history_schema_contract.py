from typing import Final

from ci_coordinator.persistence.ci_economics_schema_contract import (
    ColumnFact,
    EconomicsCatalogContract,
)
from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG

_DEFAULT: Final = "ci_history_defaults"
_DATASET: Final = "ci_history_datasets"
_SCAN: Final = "ci_history_scans"
_ATTEMPT: Final = "ci_history_attempts"
_JOB: Final = "ci_history_jobs"
_DETAIL: Final = "ci_history_details"
_GAP: Final = "ci_history_gaps"
_RECHECK: Final = "ci_history_rechecks"
_INBOX: Final = "ci_history_delivery_inbox"
_SCOPE: Final = ("installation_id", "repository_id")
_IDENTITY: Final = (*_SCOPE, "generation", "workflow_run_id", "run_attempt")
_SHAPES: Final = {
    _DEFAULT: (
        ("singleton", "boolean", False),
        ("revision", "bigint", False),
        ("detail_policy_canonical", "bytea", False),
        ("updated_at", "timestamp with time zone", False),
    ),
    _DATASET: (
        *(
            (name, "bigint", False)
            for name in (*_SCOPE, "generation", "configuration_revision", "data_revision")
        ),
        ("configured_at", "timestamp with time zone", False),
        ("state", "character varying(16)", False),
        ("configuration_canonical", "bytea", False),
        *(
            (name, "bigint", False)
            for name in ("attempt_count", "job_count", "gap_count", "canonical_bytes")
        ),
    ),
    _SCAN: (
        *(
            (name, "bigint", False)
            for name in (*_SCOPE, "generation", "configuration_revision", "revision")
        ),
        ("lane", "character varying(16)", False),
        ("state_canonical", "bytea", False),
        ("traversal_complete", "boolean", False),
        ("next_attempt_at", "timestamp with time zone", False),
        ("lease_worker_id", "character varying(64)", True),
        ("lease_token", "character varying(64)", True),
        ("lease_acquired_at", "timestamp with time zone", True),
        ("lease_expires_at", "timestamp with time zone", True),
    ),
    _ATTEMPT: (
        *((name, "bigint", False) for name in _IDENTITY),
        ("head_sha", "character varying(40)", False),
        ("workflow_id", "bigint", False),
        ("run_created_at", "timestamp with time zone", False),
        ("header_canonical", "bytea", False),
        ("statistics_digest", "character varying(64)", False),
        ("job_count", "bigint", False),
        ("statistics_bytes", "bigint", False),
        ("has_conflict", "boolean", False),
        ("first_imported_at", "timestamp with time zone", False),
        ("detail_state", "character varying(16)", False),
        ("detail_first_imported_at", "timestamp with time zone", True),
        ("detail_policy_canonical", "bytea", True),
        ("detail_policy_source", "character varying(32)", True),
        ("detail_policy_revision", "bigint", True),
        ("detail_expires_at", "timestamp with time zone", True),
    ),
    _JOB: (
        *((name, "bigint", False) for name in (*_IDENTITY, "provider_job_id")),
        ("name", "character varying(512)", False),
        ("conclusion", "character varying(32)", False),
        ("created_at", "timestamp with time zone", True),
        ("started_at", "timestamp with time zone", True),
        ("completed_at", "timestamp with time zone", True),
        ("job_canonical", "bytea", False),
    ),
    _DETAIL: (
        *((name, "bigint", False) for name in _IDENTITY),
        ("detail_canonical", "bytea", False),
    ),
    _GAP: (
        *((name, "bigint", False) for name in (*_SCOPE, "generation")),
        ("gap_id", "character varying(64)", False),
        ("gap_canonical", "bytea", False),
        ("recorded_at", "timestamp with time zone", False),
    ),
    _RECHECK: (
        *(
            (name, "bigint", False)
            for name in (*_SCOPE, "generation", "workflow_run_id", "workflow_id")
        ),
        ("source", "character varying(8)", False),
        ("revision", "bigint", False),
        ("state_canonical", "bytea", False),
        ("next_attempt_at", "timestamp with time zone", False),
        ("acquisition_count", "bigint", False),
        ("lease_worker_id", "character varying(64)", True),
        ("lease_token", "character varying(64)", True),
        ("lease_acquired_at", "timestamp with time zone", True),
        ("lease_expires_at", "timestamp with time zone", True),
    ),
    _INBOX: (
        ("delivery_id", "character varying(128)", False),
        ("source_fingerprint", "character varying(64)", False),
        *(
            (name, "bigint", False)
            for name in (*_SCOPE, "workflow_run_id", "run_attempt", "workflow_id")
        ),
        ("run_created_at", "timestamp with time zone", False),
        ("source_recorded_at", "timestamp with time zone", False),
        ("source_retain_until", "timestamp with time zone", False),
        ("delivered_generation", "bigint", False),
        ("delivered_at", "timestamp with time zone", True),
    ),
}
_RELATIONS: Final = tuple(sorted(_SHAPES))
_COLUMNS: Final[tuple[ColumnFact, ...]] = tuple(
    (table, name, sql_type, nullable, None)
    for table in _RELATIONS
    for name, sql_type, nullable in _SHAPES[table]
)


def _safe_integers(names: tuple[str, ...], minimum: int = 1) -> str:
    return " AND ".join(
        f"{name} >= {minimum} AND {name} <= '9007199254740991'::bigint" for name in names
    )


def _byte_bound(name: str, maximum: int) -> str:
    return f"octet_length({name}) >= 1 AND octet_length({name}) <= {maximum}"


_CHECKS: Final = {
    (_DEFAULT, "singleton"): "singleton IS TRUE",
    (_DEFAULT, "revision"): _safe_integers(("revision",)),
    (_DEFAULT, "payload"): _byte_bound("detail_policy_canonical", 1024),
    (_DATASET, "identity"): _safe_integers(
        (*_SCOPE, "generation", "configuration_revision", "data_revision")
    ),
    (_DATASET, "state"): (
        "state::text = ANY (ARRAY['active'::character varying, 'paused'::character varying, "
        "'erasing'::character varying, 'erased'::character varying]::text[])"
    ),
    (_DATASET, "usage"): _safe_integers(
        ("attempt_count", "job_count", "gap_count", "canonical_bytes"), 0
    ),
    (_DATASET, "payload"): _byte_bound("configuration_canonical", 4096),
    (_SCAN, "revision"): _safe_integers(("generation", "configuration_revision", "revision")),
    (_SCAN, "lane"): (
        "lane::text = ANY (ARRAY['backfill'::character varying, "
        "'discovery'::character varying]::text[])"
    ),
    (_SCAN, "payload"): _byte_bound("state_canonical", 65536),
    (_SCAN, "lease"): (
        "lease_worker_id IS NULL AND lease_token IS NULL AND "
        "lease_acquired_at IS NULL AND lease_expires_at IS NULL OR "
        "lease_worker_id IS NOT NULL AND lease_token IS NOT NULL AND "
        "lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL AND "
        "lease_worker_id::text ~ '^[0-9a-f]{64}$'::text AND "
        "lease_token::text ~ '^[0-9a-f]{64}$'::text AND lease_acquired_at < lease_expires_at"
    ),
    (_ATTEMPT, "integers"): _safe_integers(
        ("generation", "workflow_run_id", "run_attempt", "workflow_id")
    )
    + " AND job_count >= 0 AND job_count <= 2000",
    (_ATTEMPT, "digests"): (
        "head_sha::text ~ '^[0-9a-f]{40}$'::text AND "
        "statistics_digest::text ~ '^[0-9a-f]{64}$'::text"
    ),
    (_ATTEMPT, "payload"): _byte_bound("header_canonical", 8192)
    + " AND statistics_bytes >= 1 AND statistics_bytes <= 8388608",
    (_ATTEMPT, "detail"): (
        "detail_state::text = 'not_imported'::text AND detail_first_imported_at IS NULL AND "
        "detail_policy_canonical IS NULL AND detail_policy_source IS NULL AND "
        "detail_policy_revision IS NULL AND detail_expires_at IS NULL OR "
        "(detail_state::text = ANY (ARRAY['retained'::character varying, "
        "'expired'::character varying]::text[])) AND "
        "detail_first_imported_at IS NOT NULL AND detail_policy_canonical IS NOT NULL AND "
        "detail_policy_revision IS NOT NULL AND "
        "detail_policy_source IS NOT NULL AND "
        "(detail_policy_source::text = ANY (ARRAY['service_default'::character varying, "
        "'repository_override'::character varying]::text[])) AND "
        "detail_policy_revision >= 1 AND detail_policy_revision <= '9007199254740991'::bigint AND "
        "octet_length(detail_policy_canonical) >= 1 AND "
        "octet_length(detail_policy_canonical) <= 1024 AND "
        "(detail_expires_at IS NULL OR detail_expires_at > detail_first_imported_at)"
    ),
    (_JOB, "identity"): _safe_integers(("provider_job_id",)),
    (_JOB, "name"): "length(name::text) >= 1 AND length(name::text) <= 512",
    (_JOB, "conclusion"): (
        "conclusion::text = ANY (ARRAY['success'::character varying, 'failure'::character varying, "
        "'cancelled'::character varying, 'timed_out'::character varying, "
        "'skipped'::character varying, 'neutral'::character varying, "
        "'action_required'::character varying, "
        "'startup_failure'::character varying, 'stale'::character varying]::text[])"
    ),
    (_JOB, "payload"): _byte_bound("job_canonical", 32768),
    (_DETAIL, "payload"): _byte_bound("detail_canonical", 8388608),
    (_GAP, "identity"): _safe_integers(("generation",))
    + " AND gap_id::text ~ '^[0-9a-f]{64}$'::text",
    (_GAP, "payload"): _byte_bound("gap_canonical", 4096),
    (_RECHECK, "identity"): _safe_integers(
        ("generation", "workflow_run_id", "workflow_id", "revision")
    ),
    (_RECHECK, "source"): (
        "source::text = ANY (ARRAY['recent'::character varying, "
        "'repair'::character varying]::text[])"
    ),
    (_RECHECK, "attempts"): (
        "acquisition_count >= 0 AND acquisition_count <= 3 AND revision > acquisition_count AND "
        "(acquisition_count < 3 OR lease_expires_at IS NOT NULL)"
    ),
    (_RECHECK, "payload"): _byte_bound("state_canonical", 4096),
    (_RECHECK, "lease"): (
        "lease_worker_id IS NULL AND lease_token IS NULL AND "
        "lease_acquired_at IS NULL AND lease_expires_at IS NULL OR "
        "lease_worker_id IS NOT NULL AND lease_token IS NOT NULL AND "
        "lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL AND "
        "lease_worker_id::text ~ '^[0-9a-f]{64}$'::text AND "
        "lease_token::text ~ '^[0-9a-f]{64}$'::text AND "
        "acquisition_count >= 1 AND next_attempt_at <= lease_acquired_at AND "
        "(lease_expires_at - lease_acquired_at) = '00:01:00'::interval"
    ),
    (_INBOX, "source"): (
        "octet_length(delivery_id::text) >= 1 AND octet_length(delivery_id::text) <= 128 AND "
        "source_fingerprint::text ~ '^[0-9a-f]{64}$'::text"
    ),
    (_INBOX, "identity"): _safe_integers((*_SCOPE, "workflow_run_id", "run_attempt", "workflow_id"))
    + " AND "
    + _safe_integers(("delivered_generation",), 0),
    (_INBOX, "retention"): ("source_retain_until = (source_recorded_at + '90 days'::interval)"),
    (_INBOX, "receipt"): (
        "delivered_generation = 0 AND delivered_at IS NULL OR "
        "delivered_generation > 0 AND delivered_at IS NOT NULL AND "
        "source_recorded_at <= delivered_at AND delivered_at < source_retain_until"
    ),
}
_PRIMARY: Final = {
    _DEFAULT: ("singleton",),
    _DATASET: _SCOPE,
    _SCAN: (*_SCOPE, "lane"),
    _ATTEMPT: _IDENTITY,
    _JOB: (*_IDENTITY, "provider_job_id"),
    _DETAIL: _IDENTITY,
    _GAP: (*_SCOPE, "generation", "gap_id"),
    _RECHECK: (*_SCOPE, "generation", "workflow_run_id"),
    _INBOX: ("delivery_id",),
}
_DEFINITIONS: Final = {
    **{
        (table, f"ck_{table}_{suffix}"): f"CHECK ({predicate})"
        for (table, suffix), predicate in _CHECKS.items()
    },
    **{
        (table, f"pk_{table}"): f"PRIMARY KEY ({', '.join(names)})"
        for table, names in _PRIMARY.items()
    },
    **{
        (table, f"fk_{table}_dataset"): (
            "FOREIGN KEY (installation_id, repository_id) REFERENCES "
            "ci_coordinator.ci_history_datasets(installation_id, repository_id) ON DELETE RESTRICT"
        )
        for table in (_SCAN, _ATTEMPT, _GAP, _RECHECK)
    },
    **{
        (table, f"fk_{table}_attempt"): (
            f"FOREIGN KEY ({', '.join(_IDENTITY)}) REFERENCES "
            f"ci_coordinator.ci_history_attempts({', '.join(_IDENTITY)}) ON DELETE RESTRICT"
        )
        for table in (_JOB, _DETAIL)
    },
    (_INBOX, "fk_ci_history_delivery_inbox_source"): (
        "FOREIGN KEY (delivery_id) REFERENCES "
        "ci_coordinator.ci_workflow_observations(delivery_id) ON DELETE CASCADE"
    ),
}
_INDEXES: Final = {
    (_SCAN, "ix_ci_history_scans_due"): (
        "lane, next_attempt_at, installation_id, repository_id",
        None,
    ),
    (_ATTEMPT, "ix_ci_history_attempts_time"): (
        "installation_id, repository_id, generation, run_created_at, workflow_run_id, run_attempt",
        None,
    ),
    (_ATTEMPT, "ix_ci_history_attempts_workflow"): (
        "installation_id, repository_id, generation, workflow_id, run_created_at",
        None,
    ),
    (_ATTEMPT, "ix_ci_history_attempts_detail_expiry"): (
        "installation_id, repository_id, generation, detail_expires_at",
        "((detail_state)::text = 'retained'::text)",
    ),
    (_JOB, "ix_ci_history_jobs_name"): ("installation_id, repository_id, generation, name", None),
    (_RECHECK, "ix_ci_history_rechecks_due"): (
        "source, next_attempt_at, installation_id, repository_id, workflow_run_id",
        None,
    ),
    (_INBOX, "ix_ci_history_delivery_inbox_pending"): (
        "installation_id, repository_id, delivered_generation, delivery_id",
        None,
    ),
    (_INBOX, "ix_ci_history_delivery_inbox_workflow"): (
        "installation_id, repository_id, workflow_id, delivered_generation, delivery_id",
        None,
    ),
}

HISTORY_CATALOG: Final = EconomicsCatalogContract(
    relations=_RELATIONS,
    columns=_COLUMNS,
    constraints=frozenset(
        (table, name, {"ck": "c", "pk": "p", "fk": "f"}[name[:2]], False, False, True, True)
        for table, name in _DEFINITIONS
    ),
    constraint_definitions=_DEFINITIONS,
    indexes=frozenset(
        (table, name, False, False, True, True, True, predicate is not None)
        for (table, name), (_, predicate) in _INDEXES.items()
    ),
    index_definitions={
        (table, name): f"CREATE INDEX {name} ON ci_coordinator.{table} USING btree ({columns})"
        + ("" if predicate is None else f" WHERE {predicate}")
        for (table, name), (columns, predicate) in _INDEXES.items()
    },
    tables=tuple((table, "r", "p", False, False, True, True) for table in _RELATIONS),
    triggers=(),
    rewrite_rules=(),
    routine_source=V3_CATALOG.routine_source,
)
