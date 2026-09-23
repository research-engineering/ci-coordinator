"""Independent catalog expectations for atomic report budget signals."""

from dataclasses import replace
from typing import Final

from ci_coordinator.persistence.ci_economics_schema_contract import ColumnFact, ConstraintFact
from ci_coordinator.persistence.ci_economics_v2_schema_contract import V2_CATALOG

_POLICY: Final = "ci_economics_budget_policies"
_SIGNAL: Final = "ci_economics_budget_signals"
_REPORT: Final = "ci_job_measurement_reports"
_RELATIONS: Final = tuple(sorted((*V2_CATALOG.relations, _POLICY, _SIGNAL)))
_NEW_COLUMNS: Final[dict[str, tuple[ColumnFact, ...]]] = {
    _POLICY: tuple(
        (_POLICY, name, sql_type, False, None)
        for name, sql_type in (
            ("installation_id", "bigint"),
            ("repository_id", "bigint"),
            ("policy_key", "character varying(128)"),
            ("revision", "bigint"),
            ("policy_digest", "character varying(64)"),
            ("policy_canonical", "bytea"),
        )
    ),
    _SIGNAL: tuple(
        (_SIGNAL, name, sql_type, nullable, None)
        for name, sql_type, nullable in (
            ("signal_id", "character varying(64)", False),
            ("installation_id", "bigint", False),
            ("repository_id", "bigint", False),
            ("policy_key", "character varying(128)", False),
            ("policy_revision", "bigint", False),
            ("policy_canonical", "bytea", False),
            ("report_id", "character varying(64)", False),
            ("subject_id", "character varying(64)", False),
            ("report_digest", "character varying(64)", False),
            ("counter", "character varying(16)", False),
            ("value_us", "bigint", True),
            ("unavailable_reason", "character varying(32)", True),
            ("command_exit_code", "bigint", False),
            ("outcome", "character varying(32)", False),
            ("received_at", "timestamp with time zone", False),
            ("retain_until", "timestamp with time zone", False),
        )
    ),
}
_NEW_CONSTRAINTS: Final[frozenset[ConstraintFact]] = frozenset(
    (relation, name, kind, False, False, True, True)
    for relation, name, kind in (
        (_POLICY, "pk_ci_economics_budget_policies", "p"),
        (_POLICY, "ck_ci_economics_budget_policies_integers", "c"),
        (_POLICY, "ck_ci_economics_budget_policies_key", "c"),
        (_POLICY, "ck_ci_economics_budget_policies_digest", "c"),
        (_POLICY, "ck_ci_economics_budget_policies_payload", "c"),
        (_REPORT, "uq_ci_job_measurement_reports_budget_identity", "u"),
        (_SIGNAL, "pk_ci_economics_budget_signals", "p"),
        (_SIGNAL, "uq_ci_economics_budget_signals_slot", "u"),
        (_SIGNAL, "ck_ci_economics_budget_signals_digests", "c"),
        (_SIGNAL, "ck_ci_economics_budget_signals_integers", "c"),
        (_SIGNAL, "ck_ci_economics_budget_signals_counter", "c"),
        (_SIGNAL, "ck_ci_economics_budget_signals_measurement", "c"),
        (_SIGNAL, "ck_ci_economics_budget_signals_payload", "c"),
        (_SIGNAL, "ck_ci_economics_budget_signals_retention", "c"),
        (_SIGNAL, "fk_ci_economics_budget_signals_policy", "f"),
        (_SIGNAL, "fk_ci_economics_budget_signals_report", "f"),
    )
)
_DEFINITIONS: Final = {
    (
        _POLICY,
        "pk_ci_economics_budget_policies",
    ): "PRIMARY KEY (installation_id, repository_id, policy_key)",
    (_POLICY, "ck_ci_economics_budget_policies_integers"): (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint AND "
        "repository_id >= 1 AND repository_id <= '9007199254740991'::bigint AND "
        "revision >= 1 AND revision <= '9007199254740991'::bigint)"
    ),
    (_POLICY, "ck_ci_economics_budget_policies_key"): (
        "CHECK (policy_key::text ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'::text)"
    ),
    (_POLICY, "ck_ci_economics_budget_policies_digest"): (
        "CHECK (policy_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (_POLICY, "ck_ci_economics_budget_policies_payload"): (
        "CHECK (octet_length(policy_canonical) >= 1 AND octet_length(policy_canonical) <= 2048)"
    ),
    (_REPORT, "uq_ci_job_measurement_reports_budget_identity"): (
        "UNIQUE (report_id, subject_id, report_digest, retain_until)"
    ),
    (_SIGNAL, "pk_ci_economics_budget_signals"): "PRIMARY KEY (signal_id)",
    (_SIGNAL, "uq_ci_economics_budget_signals_slot"): "UNIQUE (report_id, policy_key)",
    (_SIGNAL, "ck_ci_economics_budget_signals_digests"): (
        "CHECK (signal_id::text ~ '^[0-9a-f]{64}$'::text AND report_id::text ~ "
        "'^[0-9a-f]{64}$'::text AND subject_id::text ~ '^[0-9a-f]{64}$'::text AND "
        "report_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (_SIGNAL, "ck_ci_economics_budget_signals_integers"): (
        "CHECK (policy_revision >= 1 AND policy_revision <= '9007199254740991'::bigint AND "
        "command_exit_code >= '-9007199254740991'::bigint AND "
        "command_exit_code <= '9007199254740991'::bigint)"
    ),
    (_SIGNAL, "ck_ci_economics_budget_signals_counter"): (
        "CHECK (counter::text = ANY (ARRAY['cpu_user'::character varying, "
        "'cpu_system'::character varying, 'elapsed'::character varying]::text[]))"
    ),
    (_SIGNAL, "ck_ci_economics_budget_signals_measurement"): (
        "CHECK (value_us IS NOT NULL AND value_us >= 0 AND value_us <= '9007199254740991'::bigint "
        "AND unavailable_reason IS NULL AND (outcome::text = ANY (ARRAY['breached'::character "
        "varying, 'within_budget'::character varying]::text[])) OR value_us IS NULL AND "
        "unavailable_reason IS NOT NULL AND (unavailable_reason::text = ANY "
        "(ARRAY['unsupported_platform'::character varying, 'counter_error'::character varying, "
        "'out_of_range'::character varying, 'incomplete_scope'::character varying]::text[])) "
        "AND outcome::text = 'insufficient_evidence'::text)"
    ),
    (_SIGNAL, "ck_ci_economics_budget_signals_payload"): (
        "CHECK (octet_length(policy_canonical) >= 1 AND octet_length(policy_canonical) <= 2048)"
    ),
    (_SIGNAL, "ck_ci_economics_budget_signals_retention"): "CHECK (received_at < retain_until)",
    (_SIGNAL, "fk_ci_economics_budget_signals_policy"): (
        "FOREIGN KEY (installation_id, repository_id, policy_key) REFERENCES "
        "ci_coordinator.ci_economics_budget_policies(installation_id, repository_id, policy_key) "
        "ON DELETE RESTRICT"
    ),
    (_SIGNAL, "fk_ci_economics_budget_signals_report"): (
        "FOREIGN KEY (report_id, subject_id, report_digest, retain_until) REFERENCES "
        "ci_coordinator.ci_job_measurement_reports(report_id, subject_id, report_digest, "
        "retain_until) ON DELETE CASCADE"
    ),
}
_INDEX_COLUMNS: Final = {
    "ix_ci_economics_budget_signals_scope": 'installation_id, repository_id, signal_id COLLATE "C"',
    "ix_ci_economics_budget_signals_policy": (
        'installation_id, repository_id, policy_key, policy_revision, signal_id COLLATE "C"'
    ),
}

V3_CATALOG: Final = replace(
    V2_CATALOG,
    relations=_RELATIONS,
    columns=tuple(
        column
        for relation in _RELATIONS
        for column in (
            *[row for row in V2_CATALOG.columns if row[0] == relation],
            *_NEW_COLUMNS.get(relation, ()),
        )
    ),
    constraints=V2_CATALOG.constraints | _NEW_CONSTRAINTS,
    constraint_definitions={**V2_CATALOG.constraint_definitions, **_DEFINITIONS},
    indexes=V2_CATALOG.indexes
    | {(_SIGNAL, name, False, False, True, True, True, False) for name in _INDEX_COLUMNS},
    index_definitions={
        **V2_CATALOG.index_definitions,
        **{
            (
                _SIGNAL,
                name,
            ): f"CREATE INDEX {name} ON ci_coordinator.{_SIGNAL} USING btree ({columns})"
            for name, columns in _INDEX_COLUMNS.items()
        },
    },
    tables=tuple((relation, "r", "p", False, False, True, True) for relation in _RELATIONS),
    triggers=(
        (
            _SIGNAL,
            "tr_ci_economics_budget_signals_retention_guard",
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
        *V2_CATALOG.triggers,
    ),
)
