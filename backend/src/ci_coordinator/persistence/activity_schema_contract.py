from typing import Final

from ci_coordinator.persistence.ci_economics_schema_contract import EconomicsCatalogContract

_EVENT: Final = "activity_events"
_BUCKET: Final = "activity_diagnostic_buckets"
_SHAPES: Final = {
    _BUCKET: (
        ("bucket", "timestamp with time zone"),
        ("action", "character varying(32)"),
        ("count", "bigint"),
    ),
    _EVENT: (
        ("sequence", "bigint"),
        ("occurred_at", "timestamp with time zone"),
        ("retain_until", "timestamp with time zone"),
        ("issuer", "character varying(2048)"),
        ("subject", "character varying(512)"),
        ("actor", "character varying(128)"),
        ("action", "character varying(32)"),
        ("outcome", "character varying(16)"),
        ("operation_ref", "character varying(36)"),
    ),
}
_CHECKS: Final = {
    (
        _EVENT,
        "ck_activity_sequence",
    ): "CHECK (sequence >= 1 AND sequence <= '9007199254740991'::bigint)",
    (
        _EVENT,
        "ck_activity_identity",
    ): "CHECK (octet_length(issuer::text) >= 1 AND octet_length(issuer::text) <= 2048 "
    "AND octet_length(subject::text) >= 1 AND octet_length(subject::text) <= 512 "
    "AND actor::text ~ '^keycloak-(human|workload):v1:[0-9a-f]{64}$'::text)",
    (
        _EVENT,
        "ck_activity_outcome",
    ): "CHECK ((action::text = ANY (ARRAY['login'::character varying, "
    "'logout'::character varying, 'expired'::character varying, 'revoked'::character varying, "
    "'replaced'::character varying]::text[])) AND outcome::text = 'committed'::text "
    "OR action::text = 'role_denied'::text AND outcome::text = 'denied'::text "
    "OR action::text = 'export'::text AND outcome::text = 'attempted'::text)",
    (
        _EVENT,
        "ck_activity_retention",
    ): "CHECK (retain_until = (occurred_at + '720:00:00'::interval))",
    (_EVENT, "ck_activity_operation"): "CHECK (operation_ref::text ~ "
    "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'::text)",
    (
        _BUCKET,
        "ck_activity_diagnostic_action",
    ): "CHECK (action::text = ANY (ARRAY['login_rejected'::character varying, "
    "'login_unavailable'::character varying, 'role_denied'::character varying, "
    "'export'::character varying]::text[]))",
    (_BUCKET, "ck_activity_diagnostic_count"): "CHECK (count >= 1 AND count <= 1000000)",
    (
        _BUCKET,
        "ck_activity_diagnostic_bucket",
    ): "CHECK (bucket = (date_trunc('hour'::text, (bucket AT TIME ZONE 'UTC'::text)) "
    "AT TIME ZONE 'UTC'::text))",
}
_PRIMARY: Final = {
    (_EVENT, "activity_events_pkey"): "PRIMARY KEY (sequence)",
    (_BUCKET, "activity_diagnostic_buckets_pkey"): "PRIMARY KEY (bucket, action)",
}
_INDEXES: Final = {
    (
        _EVENT,
        "ix_activity_issuer_sequence",
    ): "CREATE INDEX ix_activity_issuer_sequence ON ci_coordinator.activity_events "
    "USING btree (issuer, sequence)",
    (
        _EVENT,
        "ix_activity_retention",
    ): "CREATE INDEX ix_activity_retention ON ci_coordinator.activity_events "
    "USING btree (retain_until, sequence)",
}

ACTIVITY_CATALOG: Final = EconomicsCatalogContract(
    relations=tuple(_SHAPES),
    columns=tuple(
        (table, name, kind, False, None) for table, shape in _SHAPES.items() for name, kind in shape
    ),
    constraints=frozenset(
        (table, name, kind, False, False, True, True)
        for definitions, kind in ((_CHECKS, "c"), (_PRIMARY, "p"))
        for table, name in definitions
    ),
    constraint_definitions={**_CHECKS, **_PRIMARY},
    indexes=frozenset(
        (table, name, False, False, True, True, True, False) for table, name in _INDEXES
    ),
    index_definitions=_INDEXES,
    tables=tuple((table, "r", "p", False, False, True, True) for table in _SHAPES),
    triggers=(),
    rewrite_rules=(),
    routine_source=None,
)
