from typing import Final

from ci_coordinator.persistence.ci_economics_schema_contract import (
    ColumnFact,
    EconomicsCatalogContract,
)
from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG

_SUB: Final = "ci_observation_subscriptions"
_SCAN: Final = "ci_observation_scans"
_GAP: Final = "ci_observation_gaps"
_RELATIONS: Final = (_GAP, _SCAN, _SUB)
_COLUMN_SHAPES: Final = {
    _SUB: (
        ("installation_id", "bigint", False),
        ("repository_id", "bigint", False),
        ("revision", "bigint", False),
        ("enabled", "boolean", False),
        ("snapshot_digest", "character varying(64)", False),
        ("snapshot_canonical", "bytea", False),
        ("next_attempt_at", "timestamp with time zone", False),
        ("preferred_lane", "character varying(8)", False),
        ("detail_truncated_until", "timestamp with time zone", True),
    ),
    _SCAN: (
        ("installation_id", "bigint", False),
        ("repository_id", "bigint", False),
        ("lane", "character varying(8)", False),
        ("config_revision", "bigint", False),
        ("revision", "bigint", False),
        ("state_canonical", "bytea", False),
        ("next_attempt_at", "timestamp with time zone", False),
        ("lease_expires_at", "timestamp with time zone", True),
        ("last_completed_through", "timestamp with time zone", True),
        ("last_page_at", "timestamp with time zone", True),
        ("pages_seen", "bigint", False),
        ("sources_registered", "bigint", False),
        ("last_outcome", "character varying(32)", True),
    ),
    _GAP: (
        ("gap_id", "character varying(64)", False),
        ("installation_id", "bigint", False),
        ("repository_id", "bigint", False),
        ("gap_canonical", "bytea", False),
        ("expires_at", "timestamp with time zone", False),
    ),
}
_COLUMNS: Final[tuple[ColumnFact, ...]] = tuple(
    (relation, name, sql_type, nullable, None)
    for relation in _RELATIONS
    for name, sql_type, nullable in _COLUMN_SHAPES[relation]
)
_DEFINITIONS: Final = {
    (_SUB, "pk_ci_observation_subscriptions"): "PRIMARY KEY (installation_id, repository_id)",
    (_SUB, "ck_ci_observation_subscriptions_integers"): (
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint AND "
        "repository_id >= 1 AND repository_id <= '9007199254740991'::bigint AND "
        "revision >= 1 AND revision <= '9007199254740991'::bigint)"
    ),
    (_SUB, "ck_ci_observation_subscriptions_digest"): (
        "CHECK (snapshot_digest::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    (_SUB, "ck_ci_observation_subscriptions_payload"): (
        "CHECK (octet_length(snapshot_canonical) >= 1 AND octet_length(snapshot_canonical) <= 2048)"
    ),
    (_SUB, "ck_ci_observation_subscriptions_lane"): (
        "CHECK (preferred_lane::text = ANY "
        "(ARRAY['recent'::character varying, 'backfill'::character varying]::text[]))"
    ),
    (_SCAN, "pk_ci_observation_scans"): "PRIMARY KEY (installation_id, repository_id, lane)",
    (_SCAN, "fk_ci_observation_scans_subscription"): (
        "FOREIGN KEY (installation_id, repository_id) REFERENCES "
        "ci_coordinator.ci_observation_subscriptions(installation_id, repository_id) "
        "ON DELETE RESTRICT"
    ),
    (_SCAN, "ck_ci_observation_scans_lane"): (
        "CHECK (lane::text = ANY "
        "(ARRAY['recent'::character varying, 'backfill'::character varying]::text[]))"
    ),
    (_SCAN, "ck_ci_observation_scans_integers"): (
        "CHECK (config_revision >= 1 AND config_revision <= '9007199254740991'::bigint AND "
        "revision >= 1 AND revision <= '9007199254740991'::bigint AND "
        "pages_seen >= 0 AND pages_seen <= '9007199254740991'::bigint AND "
        "sources_registered >= 0 AND sources_registered <= '9007199254740991'::bigint)"
    ),
    (_SCAN, "ck_ci_observation_scans_payload"): (
        "CHECK (octet_length(state_canonical) >= 1 AND octet_length(state_canonical) <= 2048)"
    ),
    (_SCAN, "ck_ci_observation_scans_outcome"): (
        "CHECK (last_outcome IS NULL OR (last_outcome::text = ANY "
        "(ARRAY['page_recorded'::character varying, 'capacity_reached'::character varying, "
        "'provider_unavailable'::character varying, "
        "'provider_binding_mismatch'::character varying, "
        "'provider_malformed'::character varying, 'provider_incomplete'::character varying, "
        "'provider_not_terminal'::character varying, 'provider_unstable'::character varying, "
        "'access_unavailable'::character varying, 'timed_out'::character varying]::text[])))"
    ),
    (_SCAN, "ck_ci_observation_scans_completed_precision"): (
        "CHECK (last_completed_through IS NULL OR "
        "last_completed_through = date_trunc('second'::text, last_completed_through))"
    ),
    (_GAP, "pk_ci_observation_gaps"): "PRIMARY KEY (gap_id)",
    (_GAP, "fk_ci_observation_gaps_subscription"): (
        "FOREIGN KEY (installation_id, repository_id) REFERENCES "
        "ci_coordinator.ci_observation_subscriptions(installation_id, repository_id) "
        "ON DELETE RESTRICT"
    ),
    (_GAP, "ck_ci_observation_gaps_digest"): "CHECK (gap_id::text ~ '^[0-9a-f]{64}$'::text)",
    (_GAP, "ck_ci_observation_gaps_payload"): (
        "CHECK (octet_length(gap_canonical) >= 1 AND octet_length(gap_canonical) <= 2048)"
    ),
}
_INDEX_COLUMNS: Final = {
    (_SUB, "ix_ci_observation_subscriptions_due"): (
        "enabled, next_attempt_at, installation_id, repository_id"
    ),
    (_GAP, "ix_ci_observation_gaps_scope"): 'installation_id, repository_id, gap_id COLLATE "C"',
    (_GAP, "ix_ci_observation_gaps_expiry"): "installation_id, repository_id, expires_at",
}

OBSERVATION_CATALOG: Final = EconomicsCatalogContract(
    relations=_RELATIONS,
    columns=_COLUMNS,
    constraints=frozenset(
        (relation, name, {"pk": "p", "fk": "f", "ck": "c"}[name[:2]], False, False, True, True)
        for relation, name in _DEFINITIONS
    ),
    constraint_definitions=_DEFINITIONS,
    indexes=frozenset(
        (relation, name, False, False, True, True, True, False) for relation, name in _INDEX_COLUMNS
    ),
    index_definitions={
        (
            relation,
            name,
        ): f"CREATE INDEX {name} ON ci_coordinator.{relation} USING btree ({columns})"
        for (relation, name), columns in _INDEX_COLUMNS.items()
    },
    tables=tuple((relation, "r", "p", False, False, True, True) for relation in _RELATIONS),
    triggers=(),
    rewrite_rules=(),
    routine_source=V3_CATALOG.routine_source,
)
