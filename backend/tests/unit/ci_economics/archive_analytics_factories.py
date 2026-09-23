from datetime import UTC, datetime, timedelta

from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsQuery,
    AnalyticsSnapshot,
    CohortEvidence,
    DailyBucket,
    JobAggregate,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def analytics_query(days: int = 30, **changes: object) -> AnalyticsQuery:
    return AnalyticsQuery.model_validate(
        {
            "installation_id": 101,
            "repository_id": 202,
            "generation": 1,
            "created_from": START,
            "created_until": START + timedelta(days=days),
            "workflow_id": 9001,
            "job_name": "lint",
            "horizon_days": 1,
            **changes,
        }
    )


def analytics_snapshot(
    durations: tuple[int, ...] = (10000,) * 30,
    *,
    query: AnalyticsQuery | None = None,
) -> AnalyticsSnapshot:
    query = analytics_query(len(durations)) if query is None else query
    buckets = tuple(
        DailyBucket(
            day=query.created_from + timedelta(days=index),
            coverage="complete_retained",
            runs=1,
            attempts=1,
            matching_attempts=1,
            complete_attempts=1,
            partial_attempts=0,
            unavailable_attempts=0,
            conflict_attempts=0,
            known_missing_jobs=0,
            unknown_population_attempts=0,
            conflict_excluded_jobs=0,
            selected=JobAggregate(
                jobs=3,
                duration_samples=3,
                queue_samples=3,
                runner_ms=duration * 3,
                queue_ms=3000,
                unknown_purpose_jobs=3,
            ),
            observed_runner_ms=duration * 3,
            observed_queue_ms=3000,
        )
        for index, duration in enumerate(durations)
    )
    return AnalyticsSnapshot(
        query=query,
        observed_at=query.created_until + timedelta(days=1),
        data_revision=1,
        configuration_revision=1,
        dataset_state="active",
        mapping=None,
        runs=len(durations),
        attempts=len(durations),
        buckets=buckets,
        cohort=CohortEvidence(
            known_workflow_versions=0,
            unknown_workflow_attempts=len(durations),
            event_count=1,
            definition_stable=False,
            observed_definition_changes=False,
            compatible=False,
        ),
    )
