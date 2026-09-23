from sqlalchemy import BigInteger, and_, case, cast, false, func, not_, or_, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select, Subquery

from ci_coordinator.ci_economics.archive_analytics_models import (
    MAX_ANALYTICS_ATTEMPTS,
    MAX_ANALYTICS_JOBS,
    AnalyticsQuery,
    JobAggregate,
    PurposeMapping,
)
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_attempts,
    ci_history_jobs,
)
from ci_coordinator.persistence.ci_history_state_store import history_scope_predicate


def analytics_attempts(query: AnalyticsQuery) -> Subquery:
    table = ci_history_attempts
    header = cast(func.convert_from(table.c.header_canonical, "UTF8"), JSONB)
    statement = select(
        table.c.installation_id,
        table.c.repository_id,
        table.c.generation,
        table.c.workflow_run_id,
        table.c.run_attempt,
        table.c.workflow_id,
        table.c.head_sha,
        table.c.run_created_at,
        table.c.job_count,
        case((table.c.has_conflict, "conflict"), else_=header["population"].astext).label(
            "population"
        ),
        cast(header["providerJobTotal"].astext, BigInteger).label("provider_job_total"),
        header["workflowBlobSha"].astext.label("workflow_blob_sha"),
        header["event"].astext.label("event"),
    ).where(
        history_scope_predicate(table, query.scope),
        table.c.generation == query.generation,
        table.c.run_created_at >= query.created_from,
        table.c.run_created_at < query.created_until,
    )
    if query.workflow_id is not None:
        statement = statement.where(table.c.workflow_id == query.workflow_id)
    return (
        statement.order_by(table.c.run_created_at, table.c.workflow_run_id, table.c.run_attempt)
        .limit(MAX_ANALYTICS_ATTEMPTS + 1)
        .subquery("analytics_attempts")
    )


def analytics_population(query: AnalyticsQuery) -> Select[tuple[int, int, int, int, int, int]]:
    attempts = analytics_attempts(query)
    return select(
        func.count().label("attempts"),
        func.count(func.distinct(attempts.c.workflow_run_id)).label("runs"),
        cast(func.coalesce(func.sum(attempts.c.job_count), 0), BigInteger).label("archived_jobs"),
        func.count().filter(attempts.c.workflow_blob_sha.is_(None)).label("unknown_versions"),
        func.count(func.distinct(tuple_(attempts.c.workflow_id, attempts.c.workflow_blob_sha)))
        .filter(attempts.c.workflow_blob_sha.is_not(None))
        .label("known_versions"),
        func.count(func.distinct(attempts.c.event)).label("events"),
    ).select_from(attempts)


def analytics_definition_changes(query: AnalyticsQuery) -> Select[tuple[int]]:
    attempts = analytics_attempts(query)
    changed = (
        select(attempts.c.workflow_id)
        .group_by(attempts.c.workflow_id)
        .having(
            or_(
                func.count(func.distinct(attempts.c.workflow_blob_sha)) > 1,
                func.count(func.distinct(attempts.c.event)) > 1,
            )
        )
        .subquery()
    )
    return select(func.count()).select_from(changed)


def analytics_daily(
    query: AnalyticsQuery, mapping: PurposeMapping | None
) -> Select[tuple[object, ...]]:
    attempts = analytics_attempts(query)
    totals = analytics_job_aggregates(query, mapping).subquery("job_totals")
    population = attempts.c.population
    admitted = population != "conflict"
    day = func.date_trunc("day", attempts.c.run_created_at, "UTC")
    selected_jobs = func.coalesce(totals.c.jobs, 0)

    def total(value: ColumnElement[object], name: str) -> ColumnElement[int]:
        return cast(func.coalesce(func.sum(value), 0), BigInteger).label(name)

    return (
        select(
            day.label("day"),
            func.count().label("attempts"),
            func.count(func.distinct(attempts.c.workflow_run_id)).label("runs"),
            func.count().filter(selected_jobs > 0).label("matching_attempts"),
            *(
                func.count().filter(population == value).label(f"{value}_attempts")
                for value in ("complete", "partial", "unavailable", "conflict")
            ),
            total(
                case(
                    (
                        admitted,
                        func.coalesce(attempts.c.provider_job_total - attempts.c.job_count, 0),
                    ),
                    else_=0,
                ),
                "known_missing_jobs",
            ),
            func.count()
            .filter(or_(population == "conflict", attempts.c.provider_job_total.is_(None)))
            .label("unknown_population_attempts"),
            total(case((admitted, 0), else_=selected_jobs), "conflict_excluded_jobs"),
            *(
                total(case((admitted, func.coalesce(totals.c[name], 0)), else_=0), name)
                for name in JobAggregate.model_fields
            ),
        )
        .select_from(
            attempts.outerjoin(
                totals,
                and_(
                    attempts.c.workflow_run_id == totals.c.workflow_run_id,
                    attempts.c.run_attempt == totals.c.run_attempt,
                ),
            )
        )
        .group_by(day)
        .order_by(day)
        .limit(367)
    )


def analytics_job_count(query: AnalyticsQuery) -> Select[tuple[int]]:
    attempts = analytics_attempts(query)
    jobs = ci_history_jobs
    bounded = (
        select(jobs.c.provider_job_id)
        .select_from(attempts.join(jobs, _job_join(attempts)))
        .limit(MAX_ANALYTICS_JOBS + 1)
        .subquery()
    )
    return select(func.count()).select_from(bounded)


def analytics_job_aggregates(
    query: AnalyticsQuery, mapping: PurposeMapping | None
) -> Select[tuple[object, ...]]:
    attempts = analytics_attempts(query)
    raw_jobs = ci_history_jobs
    jobs = (
        select(
            raw_jobs.c.workflow_run_id,
            raw_jobs.c.run_attempt,
            raw_jobs.c.name,
            raw_jobs.c.conclusion,
            raw_jobs.c.created_at,
            raw_jobs.c.started_at,
            raw_jobs.c.completed_at,
            attempts.c.workflow_id,
        )
        .select_from(attempts.join(raw_jobs, _job_join(attempts)))
        .limit(MAX_ANALYTICS_JOBS + 1)
        .subquery("bounded_analytics_jobs")
    )
    created, started, completed = jobs.c.created_at, jobs.c.started_at, jobs.c.completed_at
    inconsistent = or_(
        and_(created.is_not(None), started.is_not(None), created > started),
        and_(started.is_not(None), completed.is_not(None), started > completed),
        and_(created.is_not(None), completed.is_not(None), created > completed),
    )
    consistent = not_(inconsistent)
    duration = and_(consistent, started.is_not(None), completed.is_not(None))
    queue = and_(consistent, created.is_not(None), started.is_not(None))
    unknown, mixed, purpose_selected = _purpose_predicates(query, mapping, jobs)

    def count(predicate: ColumnElement[bool], name: str) -> ColumnElement[int]:
        return func.count().filter(predicate).label(name)

    def interval_sum(
        predicate: ColumnElement[bool],
        end: ColumnElement[object],
        start: ColumnElement[object],
        name: str,
    ) -> ColumnElement[int]:
        milliseconds = func.floor(func.extract("epoch", end - start) * 1000)
        return cast(
            func.coalesce(func.sum(case((predicate, milliseconds), else_=0)), 0), BigInteger
        ).label(name)

    statement = (
        select(
            jobs.c.workflow_run_id,
            jobs.c.run_attempt,
            func.count().label("jobs"),
            count(jobs.c.conclusion.in_(("failure", "timed_out", "startup_failure")), "failures"),
            count(jobs.c.conclusion == "cancelled", "cancellations"),
            count(duration, "duration_samples"),
            count(queue, "queue_samples"),
            interval_sum(duration, completed, started, "runner_ms"),
            interval_sum(queue, started, created, "queue_ms"),
            count(inconsistent, "inconsistent_timings"),
            count(
                and_(consistent, or_(started.is_(None), completed.is_(None))), "missing_duration"
            ),
            count(and_(consistent, or_(created.is_(None), started.is_(None))), "missing_queue"),
            count(mixed, "mixed_jobs"),
            count(unknown, "unknown_purpose_jobs"),
        )
        .select_from(jobs)
        .where(purpose_selected)
    )
    if query.job_name is not None:
        statement = statement.where(jobs.c.name == query.job_name)
    return (
        statement.group_by(jobs.c.workflow_run_id, jobs.c.run_attempt)
        .order_by(jobs.c.workflow_run_id, jobs.c.run_attempt)
        .limit(MAX_ANALYTICS_ATTEMPTS + 1)
    )


def _job_join(attempts: Subquery) -> ColumnElement[bool]:
    return and_(
        *(
            ci_history_jobs.c[name] == attempts.c[name]
            for name in (
                "installation_id",
                "repository_id",
                "generation",
                "workflow_run_id",
                "run_attempt",
            )
        )
    )


def _purpose_predicates(
    query: AnalyticsQuery, mapping: PurposeMapping | None, jobs: Subquery
) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[bool]]:
    entries = () if mapping is None else mapping.entries
    predicates = tuple(
        and_(
            jobs.c.workflow_id == entry.workflow_id,
            jobs.c.name == entry.job_name,
        )
        for entry in entries
    )
    unknown = not_(or_(false(), *predicates))
    mixed = or_(
        false(),
        *(
            predicate
            for entry, predicate in zip(entries, predicates, strict=True)
            if len(entry.purposes) > 1
        ),
    )
    selected = (
        unknown
        if query.purpose == "unknown"
        else mixed
        if query.purpose == "mixed"
        else or_(
            false(),
            *(
                predicate
                for entry, predicate in zip(entries, predicates, strict=True)
                if query.purpose in entry.purposes
            ),
        )
        if query.purpose is not None
        else not_(false())
    )
    return unknown, mixed, selected
