from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import BigInteger, Column, Table, and_, func, literal, select, true, tuple_
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.archive_detail import MAX_HISTORY_DETAIL_BYTES
from ci_coordinator.ci_economics.archive_encoding import MAX_HISTORY_STATISTICS_BYTES
from ci_coordinator.ci_economics.history_read import (
    HistoryReadCursor,
    HistoryReadKey,
    HistoryReadPage,
    HistoryReadQuery,
    HistoryReadRejected,
)
from ci_coordinator.ci_economics.model import MAX_JOBS_PER_ATTEMPT
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_attempts as attempts
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_details as details
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_jobs as jobs
from ci_coordinator.persistence._schema_ci_history_control import ci_history_datasets as datasets
from ci_coordinator.persistence._schema_ci_history_control import ci_history_gaps as gaps
from ci_coordinator.persistence.ci_history_codec import decode_archive_statistics
from ci_coordinator.persistence.ci_history_control_codec import decode_history_dataset
from ci_coordinator.persistence.ci_history_read_codec import (
    read_history_detail_payload,
    read_history_gap,
    read_history_job,
    read_history_summary,
)
from ci_coordinator.persistence.ci_history_state_store import history_scope_predicate


async def read_history(
    connection: AsyncConnection, query: HistoryReadQuery, cursor: HistoryReadCursor | None
) -> HistoryReadPage | HistoryReadRejected:
    query = HistoryReadQuery.model_validate(query)
    if cursor is not None:
        cursor = HistoryReadCursor.model_validate(cursor)
    table = jobs if query.kind == "jobs" else gaps if query.kind == "gaps" else attempts
    predicates = [
        history_scope_predicate(table, query.scope),
        table.c.generation == query.generation,
    ]
    if query.workflow_run_id is not None:
        predicates.extend(
            [
                table.c.workflow_run_id == query.workflow_run_id,
                table.c.run_attempt == query.run_attempt,
            ]
        )
    if query.kind == "records":
        if query.created_from is not None:
            predicates.append(table.c.run_created_at >= datetime.fromisoformat(query.created_from))
        if query.created_through is not None:
            predicates.append(
                table.c.run_created_at <= datetime.fromisoformat(query.created_through)
            )
        if query.workflow_id is not None:
            predicates.append(table.c.workflow_id == query.workflow_id)
    if query.job_name is not None:
        if query.kind == "records":
            predicates.append(
                select(jobs.c.provider_job_id)
                .where(
                    history_scope_predicate(jobs, query.scope),
                    jobs.c.generation == query.generation,
                    jobs.c.workflow_run_id == attempts.c.workflow_run_id,
                    jobs.c.run_attempt == attempts.c.run_attempt,
                    jobs.c.name == query.job_name,
                )
                .exists()
            )
        else:
            predicates.append(table.c.name == query.job_name)
    order = _order(table)
    if cursor is not None:
        key = cursor.key
        if table is jobs:
            if key.job is None or key.gap is not None:
                return HistoryReadRejected("invalid_cursor")
            predicates.append(jobs.c.provider_job_id > key.job)
        elif table is gaps:
            if key.gap is None or key.job is not None:
                return HistoryReadRejected("invalid_cursor")
            predicates.append(gaps.c.gap_id > key.gap)
        else:
            if key.time is None or key.run is None or key.attempt is None or query.kind == "detail":
                return HistoryReadRejected("invalid_cursor")
            predicates.append(
                tuple_(*order) > tuple_(literal(key.time), literal(key.run), literal(key.attempt))
            )
    items = select(table).where(*predicates).order_by(*order).limit(query.limit + 1).subquery()
    detail_job_aggregate = None
    if query.kind == "detail":
        if query.workflow_run_id is None or query.run_attempt is None:
            raise ValueError("detail read requires an exact attempt")
        bounded_jobs = (
            select(jobs)
            .where(
                history_scope_predicate(jobs, query.scope),
                jobs.c.generation == query.generation,
                jobs.c.workflow_run_id == query.workflow_run_id,
                jobs.c.run_attempt == query.run_attempt,
            )
            .order_by(jobs.c.provider_job_id)
            .limit(MAX_JOBS_PER_ATTEMPT + 1)
            .cte("bounded_detail_jobs")
        )
        job_bytes = (
            select(func.coalesce(func.sum(func.octet_length(bounded_jobs.c.job_canonical)), 0))
            .correlate(None)
            .scalar_subquery()
        )
        detail_job_aggregate = (
            select(
                *(
                    func.array_agg(
                        aggregate_order_by(
                            bounded_jobs.c[column.name], bounded_jobs.c.provider_job_id
                        )
                    ).label(column.name)
                    for column in jobs.c
                )
            )
            .select_from(bounded_jobs)
            .where(job_bytes <= MAX_HISTORY_STATISTICS_BYTES)
            .subquery("detail_job_aggregate")
        )
    parent = (
        select(attempts)
        .where(
            history_scope_predicate(attempts, query.scope),
            attempts.c.generation == query.generation,
            attempts.c.workflow_run_id == query.workflow_run_id,
            attempts.c.run_attempt == query.run_attempt,
        )
        .subquery()
    )
    joined = datasets.outerjoin(items, true())
    columns = [
        *(c.label(f"dataset_{c.name}") for c in datasets.c),
        *(c.label(f"item_{c.name}") for c in items.c),
        func.statement_timestamp().label("now"),
    ]
    if query.kind == "jobs":
        joined = joined.outerjoin(parent, true())
        columns.extend(c.label(f"parent_{c.name}") for c in parent.c)
    if query.kind == "detail":
        if detail_job_aggregate is None:
            raise ValueError("detail read requires its bounded job projection")
        joined = joined.outerjoin(
            details,
            and_(
                history_scope_predicate(details, query.scope),
                details.c.generation == query.generation,
                details.c.workflow_run_id == query.workflow_run_id,
                details.c.run_attempt == query.run_attempt,
                func.octet_length(details.c.detail_canonical) <= MAX_HISTORY_DETAIL_BYTES,
            ),
        ).outerjoin(detail_job_aggregate, true())
        columns.extend(c.label(f"detail_{c.name}") for c in details.c)
        columns.extend(c.label(f"detail_jobs_{c.name}") for c in detail_job_aggregate.c)
    if query.kind == "gaps":
        source = func.convert_from(items.c.gap_canonical, "UTF8").cast(JSONB)
        joined = joined.outerjoin(
            attempts,
            and_(
                history_scope_predicate(attempts, query.scope),
                attempts.c.generation == query.generation,
                attempts.c.workflow_run_id == source["workflowRunId"].astext.cast(BigInteger),
                attempts.c.run_attempt == source["runAttempt"].astext.cast(BigInteger),
            ),
        )
        columns.extend(c.label(f"gap_attempt_{c.name}") for c in attempts.c)
    rows = (
        (
            await connection.execute(
                select(*columns)
                .select_from(joined)
                .where(history_scope_predicate(datasets, query.scope))
                .order_by(*(items.c[c.name] for c in order))
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return HistoryReadRejected("not_found")
    first = rows[0]
    dataset = decode_history_dataset({c.name: first[f"dataset_{c.name}"] for c in datasets.c})
    now = first["now"]
    if dataset.configured_at > now:
        raise ValueError("archive configuration postdates observation")
    if dataset.state not in {"active", "paused"}:
        return HistoryReadRejected("dataset_fenced")
    if dataset.generation != query.generation:
        return HistoryReadRejected("stale_cursor")
    if cursor is not None and (
        cursor.configuration_revision != dataset.configuration_revision
        or cursor.data_revision != dataset.data_revision
        or not cursor.observed_at <= now < cursor.observed_at + timedelta(minutes=20)
    ):
        return HistoryReadRejected("stale_cursor")
    selected = [
        {c.name: row[f"item_{c.name}"] for c in table.c}
        for row in rows
        if row["item_installation_id"] is not None
    ]
    if query.workflow_run_id is not None and query.kind != "jobs" and not selected:
        return HistoryReadRejected("not_found")
    visible = selected[: query.limit]
    values: dict[str, object] = {
        "query": query,
        "configurationRevision": dataset.configuration_revision,
        "dataRevision": dataset.data_revision,
        "observedAt": now,
    }
    if query.kind == "jobs":
        if first["parent_installation_id"] is None:
            return HistoryReadRejected("not_found")
        values["records"] = (
            read_history_summary({c.name: first[f"parent_{c.name}"] for c in attempts.c}, now=now),
        )
        values["jobs"] = tuple(read_history_job(row) for row in visible)
    elif query.kind == "gaps":
        retained = {
            row["item_gap_id"]: read_history_summary(
                {c.name: row[f"gap_attempt_{c.name}"] for c in attempts.c}, now=now
            )
            for row in rows
            if row["gap_attempt_installation_id"] is not None
        }
        values["gaps"] = tuple(
            read_history_gap(
                row, query.scope, query.generation, retained=retained.get(row["gap_id"])
            )
            for row in visible
        )
    else:
        records = tuple(read_history_summary(row, now=now) for row in visible)
        values["records"] = records
        if query.kind == "detail":
            values["detail"] = records[0].detail
            parent_row = {c.name: first[f"item_{c.name}"] for c in attempts.c}
            detail_row = (
                {c.name: first[f"detail_{c.name}"] for c in details.c}
                if first["detail_installation_id"] is not None
                else None
            )
            try:
                statistics = decode_archive_statistics(parent_row, _detail_job_rows(first))
            except (TypeError, ValueError):
                values["detail_payload"] = None
            else:
                values["detail_payload"] = read_history_detail_payload(
                    parent_row, detail_row, statistics, now=now
                )
    if len(selected) > query.limit:
        last = visible[-1]
        coordinates = (
            {"job": last["provider_job_id"]}
            if table is jobs
            else {"gap": last["gap_id"]}
            if table is gaps
            else {
                "time": last["run_created_at"],
                "run": last["workflow_run_id"],
                "attempt": last["run_attempt"],
            }
        )
        values["nextKey"] = HistoryReadKey.model_validate(coordinates)
    return HistoryReadPage.model_validate(values)


def _order(table: Table) -> tuple[Column[Any], ...]:
    if table is jobs:
        return (jobs.c.provider_job_id,)
    if table is gaps:
        return (gaps.c.gap_id,)
    return (attempts.c.run_created_at, attempts.c.workflow_run_id, attempts.c.run_attempt)


def _detail_job_rows(row: RowMapping) -> tuple[dict[str, object], ...]:
    values = {
        column.name: _bounded_array(row.get(f"detail_jobs_{column.name}"), column.name)
        for column in jobs.c
    }
    count = len(values["provider_job_id"])
    if count > MAX_JOBS_PER_ATTEMPT:
        raise ValueError("stored detail jobs exceed their bounded read")
    if any(len(value) != count for value in values.values()):
        raise ValueError("stored detail job columns have inconsistent cardinality")
    return tuple({name: value[index] for name, value in values.items()} for index in range(count))


def _bounded_array(value: object, name: str) -> tuple[object, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"stored detail {name} aggregate is not an array")
    if len(value) > MAX_JOBS_PER_ATTEMPT + 1:
        raise ValueError(f"stored detail {name} aggregate exceeds its bounded read")
    return tuple(value)
