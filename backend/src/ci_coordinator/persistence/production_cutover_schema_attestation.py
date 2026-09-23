"""Exact successor-catalog admission; predecessor capability meanings are unchanged."""

from collections.abc import Mapping
from typing import Final

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.production_cutover_catalog import (
    CatalogRows,
    column_rows,
    constraint_rows,
    index_rows,
    relation_rows,
    routine_rows,
    trigger_rows,
    unexpected_relation_objects,
)
from ci_coordinator.persistence.production_cutover_routines import (
    PRODUCTION_EVIDENCE_INSERT_BODY,
    PRODUCTION_STAGE_INSERT_BODY,
    PRODUCTION_STATE_TRANSITION_BODY,
)
from ci_coordinator.persistence.production_cutover_schema_contract import (
    EXPIRY_COLUMNS,
    EXPIRY_CONSTRAINTS,
    EXPIRY_INDEX,
    ORIGIN_COLUMNS,
    ORIGIN_CONSTRAINTS,
    ORIGIN_INDEX,
    PRODUCTION_COLUMNS,
    PRODUCTION_CONSTRAINTS,
)
from ci_coordinator.persistence.runtime_state_schema_attestation import (
    _runtime_ingress_predecessor_projection_matches,
)
from ci_coordinator.persistence.schema import APPLICATION_SCHEMA
from ci_coordinator.persistence.shadow_reconciliation_state_schema_attestation import (
    _shadow_predecessor_projection_matches,
)

_RELATIONS: Final = (
    "production_evidence_bundles",
    "production_scope_states",
    "production_staged_grants",
)
_ROUTINES: Final = (
    ("guard_production_evidence_insert_v1", PRODUCTION_EVIDENCE_INSERT_BODY),
    ("guard_production_stage_insert_v1", PRODUCTION_STAGE_INSERT_BODY),
    ("guard_production_state_transition_v1", PRODUCTION_STATE_TRANSITION_BODY),
)


def _expected_constraints(definitions: Mapping[tuple[str, str], tuple[str, str]]) -> CatalogRows:
    return tuple(
        (*key, *value, False, False, True, True) for key, value in sorted(definitions.items())
    )


def _additions_match(
    connection: Connection,
    relation: str,
    columns: CatalogRows,
    constraints: Mapping[tuple[str, str], tuple[str, str]],
    index: tuple[object, ...],
) -> bool:
    column_keys = {(row[0], row[1]) for row in columns}
    return (
        tuple(
            row for row in column_rows(connection, (relation,)) if (row[0], row[1]) in column_keys
        )
        == columns
        and tuple(
            row
            for row in constraint_rows(connection, (relation,))
            if (row[0], row[1]) in constraints
        )
        == _expected_constraints(constraints)
        and tuple(row for row in index_rows(connection, (relation,)) if row[1] == index[1])
        == (index,)
        and unexpected_relation_objects(connection, (relation,)) == ((0, 0, 0, 0, 0),)
    )


async def runtime_ingress_issuance_v2_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(runtime_ingress_issuance_v2_schema_matches_contract_sync)


def runtime_ingress_issuance_v2_schema_matches_contract_sync(connection: Connection) -> bool:
    return _runtime_ingress_predecessor_projection_matches(connection, successor=True) and (
        _additions_match(
            connection, "issued_plan_envelopes", EXPIRY_COLUMNS, EXPIRY_CONSTRAINTS, EXPIRY_INDEX
        )
    )


async def shadow_reconciliation_v2_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(shadow_reconciliation_v2_schema_matches_contract_sync)


def shadow_reconciliation_v2_schema_matches_contract_sync(connection: Connection) -> bool:
    return _shadow_predecessor_projection_matches(connection, successor=True) and (
        _additions_match(
            connection, "reconciliation_subjects", ORIGIN_COLUMNS, ORIGIN_CONSTRAINTS, ORIGIN_INDEX
        )
    )


async def production_cutover_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(production_cutover_schema_matches_contract_sync)


def production_cutover_schema_matches_contract_sync(connection: Connection) -> bool:
    return not production_cutover_schema_mismatch_details_sync(connection)


def production_cutover_schema_mismatch_details_sync(
    connection: Connection,
) -> tuple[tuple[str, str], ...]:
    triggers = (
        ("production_evidence_bundles", "guard_production_evidence_insert_v1", 7),
        ("production_scope_states", "guard_production_state_transition_v1", 23),
        ("production_staged_grants", "guard_production_stage_insert_v1", 7),
    )
    expected_triggers = tuple(
        (
            relation,
            name,
            name,
            APPLICATION_SCHEMA,
            False,
            "O",
            mode,
            True,
            True,
            0,
            0,
            "",
            True,
            True,
            False,
            False,
        )
        for relation, name, mode in triggers
    )
    expected_routines = tuple(
        (
            name,
            "",
            "trigger",
            "plpgsql",
            False,
            "v",
            False,
            False,
            True,
            "{search_path=pg_catalog}",
            body,
        )
        for name, body in _ROUTINES
    )
    comparisons = (
        ("columns", column_rows(connection, _RELATIONS), PRODUCTION_COLUMNS),
        (
            "constraints",
            constraint_rows(connection, _RELATIONS),
            _expected_constraints(PRODUCTION_CONSTRAINTS),
        ),
        ("indexes", index_rows(connection, _RELATIONS), ()),
        (
            "tables",
            relation_rows(connection, _RELATIONS),
            tuple((name, "r", "p", False, False, True, True) for name in _RELATIONS),
        ),
        ("triggers", trigger_rows(connection, _RELATIONS), expected_triggers),
        (
            "routines",
            routine_rows(connection, tuple(name for name, _body in _ROUTINES)),
            expected_routines,
        ),
        (
            "unexpected-objects",
            unexpected_relation_objects(connection, _RELATIONS),
            ((0, 0, 0, 0, 0),),
        ),
    )
    return tuple(
        (component, f"expected={expected!r}"[:2000] + f"; actual={actual!r}"[:2000])
        for component, actual, expected in comparisons
        if actual != expected
    )
