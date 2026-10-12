"""Independent PostgreSQL catalog contract for history population-bound provenance."""

from typing import Final

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_schema_contract import EconomicsCatalogContract

_BOUNDS: Final = "ci_history_population_bounds"
_SAFE: Final = "'9007199254740991'::bigint"
_SHAPE: Final = (
    ("installation_id", "bigint", False),
    ("repository_id", "bigint", False),
    ("generation", "bigint", False),
    ("created_from", "timestamp with time zone", False),
    ("source", "character varying(32)", False),
    ("repository_created_at", "timestamp with time zone", True),
    ("configuration_revision", "bigint", False),
    ("recorded_at", "timestamp with time zone", False),
)
_DEFINITIONS: Final = {
    (_BOUNDS, "ck_ci_history_population_bounds_identity"): (
        "CHECK ("
        + " AND ".join(
            f"{name} >= 1 AND {name} <= {_SAFE}"
            for name in (
                "installation_id",
                "repository_id",
                "generation",
                "configuration_revision",
            )
        )
        + " AND isfinite(created_from) AND isfinite(recorded_at) AND "
        "created_from = date_trunc('second'::text, created_from) AND "
        "created_from <= recorded_at)"
    ),
    (_BOUNDS, "ck_ci_history_population_bounds_source"): (
        "CHECK (source::text = 'explicit'::text AND repository_created_at IS NULL OR "
        "source::text = 'repository_created_at'::text AND repository_created_at IS NOT NULL)"
    ),
    (_BOUNDS, "ck_ci_history_population_bounds_catalog_floor"): (
        "CHECK (repository_created_at IS NULL OR isfinite(repository_created_at) AND "
        "repository_created_at <= recorded_at AND "
        "created_from = (date_trunc('day'::text, (repository_created_at AT TIME ZONE "
        "'UTC'::text)) AT TIME ZONE 'UTC'::text))"
    ),
    (_BOUNDS, "pk_ci_history_population_bounds"): (
        "PRIMARY KEY (installation_id, repository_id, generation, created_from)"
    ),
    (_BOUNDS, "fk_ci_history_population_bounds_dataset"): (
        "FOREIGN KEY (installation_id, repository_id) REFERENCES "
        "ci_coordinator.ci_history_datasets(installation_id, repository_id) ON DELETE RESTRICT"
    ),
}

HISTORY_POPULATION_BOUND_CATALOG: Final = EconomicsCatalogContract(
    relations=(_BOUNDS,),
    columns=tuple((_BOUNDS, name, kind, nullable, None) for name, kind, nullable in _SHAPE),
    constraints=frozenset(
        (table, name, {"ck": "c", "pk": "p", "fk": "f"}[name[:2]], False, False, True, True)
        for table, name in _DEFINITIONS
    ),
    constraint_definitions=_DEFINITIONS,
    indexes=frozenset(),
    index_definitions={},
    tables=((_BOUNDS, "r", "p", False, False, True, True),),
    triggers=(),
    rewrite_rules=(),
    routine_source=None,
)


def history_population_bound_schema_matches_sync(connection: Connection) -> bool:
    return ci_economics_schema_matches_contract_sync(
        connection, contract=HISTORY_POPULATION_BOUND_CATALOG
    )


async def history_population_bound_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(history_population_bound_schema_matches_sync)
