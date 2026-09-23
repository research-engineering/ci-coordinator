from dataclasses import replace
from typing import Final

from ci_coordinator.persistence.ci_economics_schema_contract import (
    V1_CATALOG,
    ColumnFact,
    ConstraintFact,
)

_COLLECTION: Final = "ci_workflow_attempt_collections"
_SNAPSHOT: Final = "ci_workflow_attempt_snapshots"
_REPORT: Final = "ci_job_measurement_reports"
_RELATIONS: Final = tuple(sorted((*V1_CATALOG.relations, _REPORT)))
_OLD_LINK: Final = (_COLLECTION, "fk_ci_workflow_attempt_collections_subject")
_NEW_COLUMNS: Final[dict[str, tuple[ColumnFact, ...]]] = {
    _REPORT: (
        (_REPORT, "report_id", "character varying(64)", False, None),
        (_REPORT, "subject_id", "character varying(64)", False, None),
        (_REPORT, "source_kind", "character varying(32)", False, None),
        (_REPORT, "report_digest", "character varying(64)", False, None),
        (_REPORT, "producer_claim_hash", "character varying(64)", False, None),
        (_REPORT, "provider_binding_digest", "character varying(64)", False, None),
        (_REPORT, "payload_canonical", "bytea", False, None),
        (_REPORT, "received_at", "timestamp with time zone", False, None),
        (_REPORT, "retain_until", "timestamp with time zone", False, None),
    ),
    _COLLECTION: (
        (_COLLECTION, "source_kind", "character varying(32)", False, None),
        (_COLLECTION, "legacy_subject_id", "character varying(64)", True, None),
        (_COLLECTION, "installation_id", "bigint", True, None),
        (_COLLECTION, "repository_id", "bigint", True, None),
        (_COLLECTION, "workflow_run_id", "bigint", True, None),
        (_COLLECTION, "run_attempt", "bigint", True, None),
        (_COLLECTION, "head_sha", "character varying(40)", True, None),
        (_COLLECTION, "provider_api_version", "character varying(10)", True, None),
        (_COLLECTION, "source_evidence_digest", "character varying(64)", True, None),
    ),
    _SNAPSHOT: ((_SNAPSHOT, "source_kind", "character varying(32)", False, None),),
}

_NEW_CONSTRAINTS: Final[frozenset[ConstraintFact]] = frozenset(
    (relation, name, kind, False, False, True, True)
    for relation, name, kind in (
        (_REPORT, "pk_ci_job_measurement_reports", "p"),
        (_REPORT, "ck_ci_job_measurement_reports_digests", "c"),
        (_REPORT, "ck_ci_job_measurement_reports_payload", "c"),
        (_REPORT, "ck_ci_job_measurement_reports_retention", "c"),
        (_REPORT, "ck_ci_job_measurement_reports_source", "c"),
        (_REPORT, "fk_ci_job_measurement_reports_source", "f"),
        (_COLLECTION, "ck_ci_workflow_attempt_collections_source", "c"),
        (_COLLECTION, "fk_ci_workflow_attempt_collections_legacy_subject", "f"),
        (_SNAPSHOT, "ck_ci_workflow_attempt_snapshots_source", "c"),
    )
)
_DEFINITIONS: Final = {
    (_REPORT, "pk_ci_job_measurement_reports"): "PRIMARY KEY (report_id)",
    (_REPORT, "ck_ci_job_measurement_reports_digests"): (
        "CHECK (report_id::text ~ '^[0-9a-f]{64}$'::text AND subject_id::text ~ "
        "'^[0-9a-f]{64}$'::text AND report_digest::text ~ '^[0-9a-f]{64}$'::text AND "
        "producer_claim_hash::text ~ '^[0-9a-f]{64}$'::text AND "
        "provider_binding_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (_REPORT, "ck_ci_job_measurement_reports_payload"): (
        "CHECK (octet_length(payload_canonical) >= 1 AND octet_length(payload_canonical) <= 131072)"
    ),
    (_REPORT, "ck_ci_job_measurement_reports_retention"): "CHECK (received_at < retain_until)",
    (
        _REPORT,
        "ck_ci_job_measurement_reports_source",
    ): "CHECK (source_kind::text = 'provider_run'::text)",
    (_REPORT, "fk_ci_job_measurement_reports_source"): (
        "FOREIGN KEY (subject_id, retain_until, source_kind) REFERENCES "
        "ci_coordinator.ci_workflow_attempt_collections(subject_id, evidence_retain_until, "
        "source_kind) ON DELETE RESTRICT"
    ),
    (
        _COLLECTION,
        "ck_ci_workflow_attempt_collections_source",
    ): (
        "CHECK (source_kind::text = 'reconciliation'::text AND "
        "legacy_subject_id IS NOT NULL AND legacy_subject_id::text = subject_id::text AND "
        "installation_id IS NULL AND repository_id IS NULL AND workflow_run_id IS NULL AND "
        "run_attempt IS NULL AND head_sha IS NULL AND provider_api_version IS NULL AND "
        "source_evidence_digest IS NULL OR source_kind::text = 'provider_run'::text AND "
        "legacy_subject_id IS NULL AND installation_id IS NOT NULL AND "
        "repository_id IS NOT NULL AND workflow_run_id IS NOT NULL AND run_attempt IS NOT NULL "
        "AND head_sha IS NOT NULL AND provider_api_version IS NOT NULL AND "
        "source_evidence_digest IS NOT NULL AND installation_id >= 1 AND "
        "installation_id <= '9007199254740991'::bigint AND repository_id >= 1 AND "
        "repository_id <= '9007199254740991'::bigint AND workflow_run_id >= 1 AND "
        "workflow_run_id <= '9007199254740991'::bigint AND run_attempt >= 1 AND "
        "run_attempt <= '9007199254740991'::bigint AND head_sha::text ~ '^[0-9a-f]{40}$'::text "
        "AND provider_api_version::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'::text AND "
        "source_evidence_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (
        _COLLECTION,
        "fk_ci_workflow_attempt_collections_legacy_subject",
    ): (
        "FOREIGN KEY (legacy_subject_id) REFERENCES "
        "ci_coordinator.reconciliation_subjects(subject_id) ON DELETE RESTRICT"
    ),
    (
        _COLLECTION,
        "uq_ci_workflow_attempt_collections_retention",
    ): "UNIQUE (subject_id, evidence_retain_until, source_kind)",
    (
        _SNAPSHOT,
        "ck_ci_workflow_attempt_snapshots_hashes",
    ): (
        "CHECK (subject_id::text ~ '^[0-9a-f]{64}$'::text AND "
        "head_sha::text ~ '^[0-9a-f]{40}$'::text AND "
        "snapshot_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (
        _SNAPSHOT,
        "ck_ci_workflow_attempt_snapshots_source",
    ): (
        "CHECK (source_kind::text = 'reconciliation'::text AND contract_hash IS NOT NULL AND "
        "contract_hash::text ~ '^[0-9a-f]{64}$'::text OR source_kind::text = 'provider_run'::text "
        "AND contract_hash IS NULL AND planned_route::text = 'unknown'::text)"
    ),
    (
        _SNAPSHOT,
        "fk_ci_workflow_attempt_snapshots_collection",
    ): (
        "FOREIGN KEY (subject_id, retain_until, source_kind) REFERENCES "
        "ci_coordinator.ci_workflow_attempt_collections(subject_id, "
        "evidence_retain_until, source_kind) ON DELETE RESTRICT"
    ),
}
_PROVIDER_INDEX: Final = "uq_ci_workflow_attempt_collections_provider_attempt"

V2_CATALOG: Final = replace(
    V1_CATALOG,
    relations=_RELATIONS,
    columns=tuple(
        column
        for relation in _RELATIONS
        for column in (
            *(
                (table, name, sql_type, True, default)
                if (table, name) == (_SNAPSHOT, "contract_hash")
                else (table, name, sql_type, nullable, default)
                for table, name, sql_type, nullable, default in V1_CATALOG.columns
                if table == relation
            ),
            *_NEW_COLUMNS.get(relation, ()),
        )
    ),
    constraints=frozenset(row for row in V1_CATALOG.constraints if (row[0], row[1]) != _OLD_LINK)
    | _NEW_CONSTRAINTS,
    constraint_definitions={
        **{
            key: value
            for key, value in V1_CATALOG.constraint_definitions.items()
            if key != _OLD_LINK
        },
        **_DEFINITIONS,
    },
    indexes=V1_CATALOG.indexes
    | {
        (_COLLECTION, _PROVIDER_INDEX, True, False, True, True, True, True),
        (_REPORT, "ix_ci_job_measurement_reports_source", False, False, True, True, True, False),
    },
    index_definitions={
        **V1_CATALOG.index_definitions,
        (_REPORT, "ix_ci_job_measurement_reports_source"): (
            "CREATE INDEX ix_ci_job_measurement_reports_source ON "
            "ci_coordinator.ci_job_measurement_reports "
            "USING btree (subject_id, report_id)"
        ),
        (_COLLECTION, _PROVIDER_INDEX): (
            "CREATE UNIQUE INDEX uq_ci_workflow_attempt_collections_provider_attempt ON "
            "ci_coordinator.ci_workflow_attempt_collections USING btree "
            "(installation_id, repository_id, workflow_run_id, run_attempt) WHERE "
            "((source_kind)::text = 'provider_run'::text)"
        ),
    },
    tables=tuple((relation, "r", "p", False, False, True, True) for relation in _RELATIONS),
    triggers=(
        (
            _REPORT,
            "tr_ci_job_measurement_reports_retention_guard",
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
        *V1_CATALOG.triggers,
    ),
)
