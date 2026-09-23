"""Bounded SQL statements for one repository workbench snapshot."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import Select, and_, func, select
from sqlalchemy.sql.elements import UnaryExpression

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.operator_override_schema import operator_overrides
from ci_coordinator.persistence.schema import (
    active_config_epochs,
    audit_events,
    audit_ledger_head,
    config_epochs,
    issued_plan_envelopes,
    reconciliation_results,
    reconciliation_subjects,
)

type WorkbenchFamily = Literal["plans", "runs", "overrides", "config_epochs", "audit_events"]
WORKBENCH_FAMILIES: tuple[WorkbenchFamily, ...] = (
    "plans",
    "runs",
    "overrides",
    "config_epochs",
    "audit_events",
)


def workbench_snapshot_statement(scope: RepositoryScope, limit: int) -> Select[tuple[object, ...]]:
    positions = func.generate_series(1, limit + 1).table_valued("position").render_derived()
    families = (
        issued_plans_statement(scope, limit),
        reconciliation_runs_statement(scope, limit),
        overrides_statement(scope, limit),
        config_epochs_statement(scope, limit),
        audit_events_statement(scope, limit),
    )
    statement = select(
        positions.c.position,
        func.statement_timestamp().label("observed_at"),
        select(audit_ledger_head.c.revision)
        .where(audit_ledger_head.c.head_id == 1)
        .scalar_subquery()
        .label("ledger_revision"),
    ).select_from(positions)
    for name, family in zip(WORKBENCH_FAMILIES, families, strict=True):
        rows = family.subquery(name)
        statement = statement.add_columns(
            *(column.label(f"{name}_{column.name}") for column in rows.c)
        ).outerjoin(rows, rows.c._position == positions.c.position)
    return statement.order_by(positions.c.position)


def issued_plans_statement(scope: RepositoryScope, limit: int) -> Select[tuple[object, ...]]:
    return _bounded_ranked(
        select(issued_plan_envelopes).where(
            issued_plan_envelopes.c.installation_id == scope.installation_id,
            issued_plan_envelopes.c.repository_id == scope.repository_id,
        ),
        limit,
        ("issued_at", "record_id"),
    )


def reconciliation_runs_statement(
    scope: RepositoryScope,
    limit: int,
) -> Select[tuple[object, ...]]:
    result_at_revision = reconciliation_results.alias("result_at_revision")
    return _bounded_ranked(
        select(
            reconciliation_subjects,
            result_at_revision.c.revision.label("result_revision"),
            result_at_revision.c.result_canonical_json,
            result_at_revision.c.semantic_hash.label("result_semantic_hash"),
        )
        .outerjoin(
            result_at_revision,
            and_(
                result_at_revision.c.subject_id == reconciliation_subjects.c.subject_id,
                result_at_revision.c.revision == reconciliation_subjects.c.revision,
            ),
        )
        .where(
            reconciliation_subjects.c.installation_id == scope.installation_id,
            reconciliation_subjects.c.repository_id == scope.repository_id,
        ),
        limit,
        ("created_at", "subject_id"),
    )


def overrides_statement(scope: RepositoryScope, limit: int) -> Select[tuple[object, ...]]:
    return _bounded_ranked(
        select(operator_overrides).where(
            operator_overrides.c.installation_id == scope.installation_id,
            operator_overrides.c.repository_id == scope.repository_id,
        ),
        limit,
        ("applied_at", "override_id"),
    )


def config_epochs_statement(scope: RepositoryScope, limit: int) -> Select[tuple[object, ...]]:
    active = active_config_epochs.alias("active_epoch")
    return _bounded_ranked(
        select(
            config_epochs.c.epoch_id,
            config_epochs.c.source_format,
            config_epochs.c.source_hash,
            config_epochs.c.document_hash,
            config_epochs.c.epoch_hash,
            config_epochs.c.document_schema_id,
            config_epochs.c.document_profile_id,
            config_epochs.c.semantic_profile_id,
            config_epochs.c.compiled_schema_id,
            active.c.revision.label("active_revision"),
        )
        .outerjoin(
            active,
            and_(
                active.c.installation_id == config_epochs.c.installation_id,
                active.c.repository_id == config_epochs.c.repository_id,
                active.c.epoch_id == config_epochs.c.epoch_id,
            ),
        )
        .where(
            config_epochs.c.installation_id == scope.installation_id,
            config_epochs.c.repository_id == scope.repository_id,
        ),
        limit,
        ("active_revision", "epoch_id"),
    )


def audit_events_statement(scope: RepositoryScope, limit: int) -> Select[tuple[object, ...]]:
    return _bounded_ranked(
        select(audit_events).where(
            audit_events.c.installation_id == scope.installation_id,
            audit_events.c.repository_id == scope.repository_id,
        ),
        limit,
        ("sequence",),
    )


def _bounded_ranked(
    statement: Select[tuple[object, ...]], limit: int, ordering: tuple[str, ...]
) -> Select[tuple[object, ...]]:
    limited = statement.order_by(*_order(statement, ordering)).limit(limit + 1).subquery()
    bounded = select(*limited.c)
    order = _order(bounded, ordering)
    return bounded.add_columns(func.row_number().over(order_by=order).label("_position")).order_by(
        *order
    )


def _order(
    statement: Select[tuple[object, ...]], names: tuple[str, ...]
) -> tuple[UnaryExpression[object], ...]:
    return tuple(
        statement.selected_columns[name].desc().nulls_last()
        if name == "active_revision"
        else statement.selected_columns[name].desc()
        for name in names
    )
