"""Independent PostgreSQL catalog contract for the durable logout revocation fence."""

from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_schema_contract import EconomicsCatalogContract

_FENCES: Final = "control_plane_logout_fences"
_GENERATION: Final = "control_plane_logout_fence_generation"
_SHAPES: Final = {
    _FENCES: (
        ("issuer", "character varying(512)"),
        ("target_kind", "character varying(16)"),
        ("target_value", "character varying(512)"),
        ("generation", "bigint"),
        ("fenced_at", "timestamp with time zone"),
    ),
    _GENERATION: (
        ("singleton", "boolean"),
        ("current_generation", "bigint"),
        ("deleted_generation_floor", "bigint"),
    ),
}
_RELATIONS: Final = tuple(sorted(_SHAPES))
_CHECKS: Final = {
    (
        _FENCES,
        "ck_control_plane_logout_fences_issuer",
    ): "CHECK (octet_length(issuer::text) >= 1 AND octet_length(issuer::text) <= 512)",
    (
        _FENCES,
        "ck_control_plane_logout_fences_target_kind",
    ): "CHECK (target_kind::text = ANY (ARRAY['subject'::character varying, "
    "'sid'::character varying]::text[]))",
    (
        _FENCES,
        "ck_control_plane_logout_fences_target_value",
    ): "CHECK (octet_length(target_value::text) >= 1 AND octet_length(target_value::text) <= 512)",
    (
        _FENCES,
        "ck_control_plane_logout_fences_generation",
    ): "CHECK (generation >= 1 AND generation <= '9007199254740991'::bigint)",
    (
        _FENCES,
        "ck_control_plane_logout_fences_fenced_at",
    ): "CHECK (isfinite(fenced_at))",
    (
        _GENERATION,
        "ck_control_plane_logout_fence_generation_singleton",
    ): "CHECK (singleton IS TRUE)",
    (
        _GENERATION,
        "ck_control_plane_logout_fence_generation_order",
    ): "CHECK (deleted_generation_floor >= 0 AND deleted_generation_floor <= current_generation "
    "AND current_generation <= '9007199254740991'::bigint)",
}
_PRIMARY: Final = {
    (
        _FENCES,
        "pk_control_plane_logout_fences",
    ): "PRIMARY KEY (issuer, target_kind, target_value)",
    (
        _GENERATION,
        "pk_control_plane_logout_fence_generation",
    ): "PRIMARY KEY (singleton)",
}
_INDEXES: Final = {
    (
        _FENCES,
        "ix_control_plane_logout_fences_fenced_at",
    ): "CREATE INDEX ix_control_plane_logout_fences_fenced_at ON "
    "ci_coordinator.control_plane_logout_fences USING btree "
    "(fenced_at, issuer, target_kind, target_value)",
}
# Exactly the migration-seeded singleton; a missing or duplicated counter fails closed.
_GENERATION_ROW_COUNT: Final = text(
    "SELECT count(*) FROM (SELECT 1 FROM ci_coordinator.control_plane_logout_fence_generation "
    "LIMIT 2) AS bounded"
)

LOGOUT_FENCE_CATALOG: Final = EconomicsCatalogContract(
    relations=_RELATIONS,
    columns=tuple(
        (table, name, kind, False, None) for table in _RELATIONS for name, kind in _SHAPES[table]
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
    tables=tuple((table, "r", "p", False, False, True, True) for table in _RELATIONS),
    triggers=(),
    rewrite_rules=(),
    routine_source=None,
)


def control_plane_logout_fence_schema_matches_sync(connection: Connection) -> bool:
    return (
        ci_economics_schema_matches_contract_sync(connection, contract=LOGOUT_FENCE_CATALOG)
        and connection.scalar(_GENERATION_ROW_COUNT) == 1
    )


async def control_plane_logout_fence_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(control_plane_logout_fence_schema_matches_sync)
