from datetime import timedelta

from ci_coordinator.ci_economics.archive_analytics_models import (
    FORECAST_CALIBRATION_FOLDS,
    FORECAST_MINIMUM_COVERAGE_BPS,
    FORECAST_MINIMUM_EVALUATION_FOLDS,
    FORECAST_TRAINING_DAYS,
    AnalyticsQuery,
    CohortEvidence,
    DailyBucket,
    ForecastFold,
    ForecastResult,
)


def usable_duration_day(bucket: DailyBucket, minimum_samples: int) -> bool:
    return (
        bucket.coverage == "complete_retained"
        and bucket.selected.duration_samples >= minimum_samples
        and bucket.selected.duration_samples == bucket.selected.jobs
        and bucket.conflict_excluded_jobs == 0
    )


def forecast_occupancy(
    query: AnalyticsQuery, buckets: tuple[DailyBucket, ...], cohort: CohortEvidence
) -> ForecastResult:
    if not buckets or not all(
        usable_duration_day(bucket, query.minimum_daily_samples) for bucket in buckets
    ):
        return _unavailable(query, "incomplete_daily_coverage")
    if cohort.observed_definition_changes:
        return _unavailable(query, "incompatible_cohort")
    horizon = query.horizon_days
    if len(buckets) < FORECAST_TRAINING_DAYS + horizon * (
        FORECAST_CALIBRATION_FOLDS + FORECAST_MINIMUM_EVALUATION_FOLDS
    ):
        return _unavailable(query, "insufficient_backtest")
    values = [bucket.selected.runner_ms for bucket in buckets]
    prefix = [0]
    for value in values:
        prefix.append(prefix[-1] + value)
    errors: list[int] = []
    evaluated_errors: list[int] = []
    covered = 0
    folds: list[ForecastFold] = []
    for origin in range(FORECAST_TRAINING_DAYS, len(values) - horizon + 1, horizon):
        prediction = prefix[origin] * horizon // origin
        actual = prefix[origin + horizon] - prefix[origin]
        evaluation = len(errors) >= FORECAST_CALIBRATION_FOLDS
        radius = max(errors) if evaluation else None
        lower = max(0, prediction - radius) if radius is not None else None
        upper = prediction + radius if radius is not None else None
        error = abs(prediction - actual)
        if lower is not None and upper is not None:
            covered += lower <= actual <= upper
            evaluated_errors.append(error)
        folds.append(
            ForecastFold(
                training_until=query.created_from + timedelta(days=origin),
                test_until=query.created_from + timedelta(days=origin + horizon),
                predicted_runner_ms=prediction,
                actual_runner_ms=actual,
                lower_runner_ms=lower,
                upper_runner_ms=upper,
                phase="evaluation" if evaluation else "calibration",
            )
        )
        errors.append(error)
    coverage = covered * 10000 // len(evaluated_errors)
    mae = sum(evaluated_errors) // len(evaluated_errors)
    prediction = prefix[-1] * horizon // len(values)
    radius = max(errors)
    available = coverage >= FORECAST_MINIMUM_COVERAGE_BPS
    return ForecastResult(
        status="available" if available else "unavailable",
        reason="conditional_on_unchanged_collection_and_workload"
        if available
        else ("poor_calibration"),
        cutoff=query.created_until,
        horizon_days=horizon,
        predicted_runner_ms=prediction if available else None,
        lower_runner_ms=max(0, prediction - radius) if available else None,
        upper_runner_ms=prediction + radius if available else None,
        backtest_mae_ms=mae,
        empirical_coverage_bps=coverage,
        usable_days=len(values),
        duration_samples=sum(bucket.selected.duration_samples for bucket in buckets),
        folds=tuple(folds),
    )


def _unavailable(
    query: AnalyticsQuery,
    reason: str,
) -> ForecastResult:
    return ForecastResult.model_validate(
        {
            "status": "unavailable",
            "reason": reason,
            "cutoff": query.created_until,
            "horizon_days": query.horizon_days,
        }
    )
