"""Exact PostgreSQL catalog attestation for retained CI economics evidence."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.catalog_observation import (
    column_rows,
    relation_rows,
    trigger_rows,
)
from ci_coordinator.persistence.ci_economics_schema_contract import (
    V1_CATALOG,
    EconomicsCatalogContract,
)
from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

_MAX_DIAGNOSTIC_VALUE_CHARS = 2_000
_MAX_DIAGNOSTIC_DIFFERENCES = 8


async def ci_economics_schema_matches_contract(
    connection: AsyncConnection, *, contract: EconomicsCatalogContract = V1_CATALOG
) -> bool:
    return await connection.run_sync(ci_economics_schema_matches_contract_sync, contract=contract)


def ci_economics_schema_matches_contract_sync(
    connection: Connection, *, contract: EconomicsCatalogContract = V1_CATALOG
) -> bool:
    return not ci_economics_schema_mismatch_details_sync(connection, contract=contract)


def ci_economics_schema_mismatches_sync(
    connection: Connection, *, contract: EconomicsCatalogContract = V1_CATALOG
) -> tuple[str, ...]:
    return tuple(
        component
        for component, _detail in ci_economics_schema_mismatch_details_sync(
            connection, contract=contract
        )
    )


def ci_economics_schema_mismatch_details_sync(
    connection: Connection,
    *,
    contract: EconomicsCatalogContract = V1_CATALOG,
) -> tuple[tuple[str, str], ...]:
    parameters = {"schema": APPLICATION_SCHEMA, "relations": list(contract.relations)}
    columns = column_rows(connection, contract.relations)
    constraint_rows = tuple(
        connection.execute(
            text(
                "SELECT relation.relname, constraint_metadata.conname, "
                "constraint_metadata.contype, "
                "pg_get_constraintdef(constraint_metadata.oid, true), "
                "constraint_metadata.condeferrable, "
                "constraint_metadata.condeferred, constraint_metadata.convalidated, "
                "constraint_metadata.conenforced "
                "FROM pg_constraint AS constraint_metadata "
                "JOIN pg_class AS relation "
                "ON relation.oid = constraint_metadata.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema "
                "AND relation.relname = ANY(:relations) "
                "AND constraint_metadata.contype <> 'n'"
            ),
            parameters,
        )
    )
    constraints = frozenset(
        (row[0], row[1], row[2], row[4], row[5], row[6], row[7]) for row in constraint_rows
    )
    constraint_definitions = {(row[0], row[1]): row[3] for row in constraint_rows}
    index_rows = tuple(
        connection.execute(
            text(
                "SELECT table_metadata.relname, index_relation.relname, "
                "index_metadata.indisunique, index_metadata.indisprimary, "
                "index_metadata.indisvalid, index_metadata.indisready, "
                "index_metadata.indislive, index_metadata.indpred IS NOT NULL, "
                "pg_get_indexdef(index_relation.oid, 0, false) "
                "FROM pg_index AS index_metadata "
                "JOIN pg_class AS index_relation "
                "ON index_relation.oid = index_metadata.indexrelid "
                "JOIN pg_class AS table_metadata "
                "ON table_metadata.oid = index_metadata.indrelid "
                "JOIN pg_namespace AS namespace "
                "ON namespace.oid = index_relation.relnamespace "
                "LEFT JOIN pg_constraint AS constraint_metadata "
                "ON constraint_metadata.conindid = index_metadata.indexrelid "
                "WHERE namespace.nspname = :schema "
                "AND table_metadata.relname = ANY(:relations) "
                "AND constraint_metadata.oid IS NULL"
            ),
            parameters,
        )
    )
    indexes = frozenset(tuple(row[:8]) for row in index_rows)
    index_definitions = {(row[0], row[1]): row[8] for row in index_rows}
    tables = relation_rows(connection, contract.relations)
    triggers = trigger_rows(connection, contract.relations)
    rewrite_rules = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT relation.relname, rule_metadata.rulename, rule_metadata.ev_type, "
                "rule_metadata.is_instead, rule_metadata.ev_enabled, "
                "pg_get_ruledef(rule_metadata.oid, false) "
                "FROM pg_rewrite AS rule_metadata "
                "JOIN pg_class AS relation ON relation.oid = rule_metadata.ev_class "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema "
                "AND relation.relname = ANY(:relations) ORDER BY 1, 2"
            ),
            parameters,
        )
    )
    routine = (
        connection.execute(
            text(
                "SELECT routine.proname, pg_get_function_identity_arguments(routine.oid), "
                "pg_get_function_result(routine.oid), language.lanname, routine.prosecdef, "
                "routine.provolatile, routine.proisstrict, routine.proleakproof, "
                "routine.proowner = namespace.nspowner, "
                "regexp_replace(btrim(routine.prosrc), '\\s+', ' ', 'g') "
                "FROM pg_proc AS routine "
                "JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
                "JOIN pg_language AS language ON language.oid = routine.prolang "
                "WHERE namespace.nspname = :schema "
                "AND routine.proname = 'guard_ci_economics_mutation'"
            ),
            {"schema": APPLICATION_SCHEMA},
        ).one_or_none()
        if contract.routine_source is not None
        else None
    )
    policies = connection.scalar(
        text(
            "SELECT count(*) FROM pg_policy AS policy_metadata "
            "WHERE policy_metadata.polrelid = ANY("
            "SELECT relation.oid FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema "
            "AND relation.relname = ANY(:relations))"
        ),
        parameters,
    )
    expected_routine = (
        "guard_ci_economics_mutation",
        "",
        "trigger",
        "plpgsql",
        False,
        "v",
        False,
        False,
        True,
        contract.routine_source,
    )
    mismatches: list[tuple[str, str]] = []
    for component, actual, expected in (
        ("columns", columns, contract.columns),
        ("constraints", constraints, contract.constraints),
        ("constraint-definitions", constraint_definitions, dict(contract.constraint_definitions)),
        ("indexes", indexes, contract.indexes),
        ("index-definitions", index_definitions, dict(contract.index_definitions)),
        ("tables", tables, contract.tables),
        ("triggers", triggers, contract.triggers),
        ("rewrite-rules", rewrite_rules, contract.rewrite_rules),
        (
            "routine",
            tuple(routine) if routine is not None else None,
            expected_routine if contract.routine_source is not None else None,
        ),
        ("policies", policies, 0),
    ):
        if actual != expected:
            mismatches.append((component, _difference_summary(actual, expected)))
    return tuple(mismatches)


def _difference_summary(actual: object, expected: object) -> str:
    differences: list[str] = []
    if isinstance(actual, dict) and isinstance(expected, dict):
        actual_keys = set(actual)
        expected_keys = set(expected)
        differences.extend(
            f"missing-key={_bounded_repr(key)}"
            for key in sorted(expected_keys - actual_keys, key=repr)
        )
        differences.extend(
            f"unexpected-key={_bounded_repr(key)}"
            for key in sorted(actual_keys - expected_keys, key=repr)
        )
        differences.extend(
            f"key={_bounded_repr(key)}; expected={_bounded_repr(expected[key])}; "
            f"actual={_bounded_repr(actual[key])}"
            for key in sorted(expected_keys & actual_keys, key=repr)
            if actual[key] != expected[key]
        )
    elif isinstance(actual, frozenset) and isinstance(expected, frozenset):
        differences.extend(
            f"missing={_bounded_repr(item)}" for item in sorted(expected - actual, key=repr)
        )
        differences.extend(
            f"unexpected={_bounded_repr(item)}" for item in sorted(actual - expected, key=repr)
        )
    elif isinstance(actual, tuple) and isinstance(expected, tuple):
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected, strict=False)):
            if actual_item != expected_item:
                differences.append(
                    f"index={index}; expected={_bounded_repr(expected_item)}; "
                    f"actual={_bounded_repr(actual_item)}"
                )
        if len(actual) != len(expected):
            differences.append(f"expected-length={len(expected)}; actual-length={len(actual)}")
    if not differences:
        differences.append(f"expected={_bounded_repr(expected)}; actual={_bounded_repr(actual)}")
    visible = differences[:_MAX_DIAGNOSTIC_DIFFERENCES]
    omitted_count = len(differences) - len(visible)
    if omitted_count:
        visible.append(f"omitted-difference-count={omitted_count}")
    return " || ".join(visible)


def _bounded_repr(value: object) -> str:
    rendered = repr(value)
    if len(rendered) <= _MAX_DIAGNOSTIC_VALUE_CHARS:
        return rendered
    return f"{rendered[:_MAX_DIAGNOSTIC_VALUE_CHARS]}...<truncated>"
