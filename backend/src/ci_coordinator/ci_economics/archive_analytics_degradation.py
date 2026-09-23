from ci_coordinator.ci_economics.archive_analytics_forecast import usable_duration_day
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsQuery,
    CohortEvidence,
    DailyBucket,
    DegradationEvent,
    DegradationResult,
    PurposeMapping,
)
from ci_coordinator.kernel import hash_object

BASELINE_DAYS = 7


def assess_degradation(
    query: AnalyticsQuery,
    buckets: tuple[DailyBucket, ...],
    cohort: CohortEvidence,
    mapping: PurposeMapping | None,
) -> DegradationResult:
    if query.workflow_id is None or query.job_name is None:
        return DegradationResult(status="unavailable", reason="incompatible_cohort")
    baseline = buckets[:BASELINE_DAYS]
    if len(buckets) <= BASELINE_DAYS or not all(
        usable_duration_day(bucket, query.minimum_daily_samples) for bucket in baseline
    ):
        return DegradationResult(status="unavailable", reason="insufficient_samples")
    samples = sum(bucket.selected.duration_samples for bucket in baseline)
    baseline_total = sum(bucket.selected.runner_ms for bucket in baseline)
    mean = baseline_total // samples
    active = False
    high_streak = low_streak = 0
    events: list[DegradationEvent] = []
    last_usable = True
    for bucket in buckets[BASELINE_DAYS:]:
        last_usable = usable_duration_day(bucket, query.minimum_daily_samples)
        if not last_usable:
            high_streak = low_streak = 0
            continue
        daily_samples = bucket.selected.duration_samples
        daily_total = bucket.selected.runner_ms
        difference = daily_total * samples - baseline_total * daily_samples
        absolute_threshold = query.degradation_absolute_ms * daily_samples * samples
        relative_threshold = query.degradation_relative_bps * baseline_total * daily_samples
        high = difference >= absolute_threshold and difference * 10000 >= relative_threshold
        low = difference * 2 < absolute_threshold and difference * 20000 < relative_threshold
        high_streak = high_streak + 1 if high else 0
        low_streak = low_streak + 1 if low else 0
        changed = False
        if not active and high_streak >= query.persistence_days:
            active, changed = True, True
        elif active and low_streak >= query.persistence_days:
            active, changed = False, True
        if changed:
            state = "observed_slowdown" if active else "recovered"
            events.append(
                DegradationEvent.model_validate(
                    {
                        "key": hash_object(
                            {
                                "model": "fixed-baseline-hysteresis/v1",
                                "query": query.model_dump(
                                    mode="json", exclude={"created_until", "horizon_days"}
                                ),
                                "mapping": None if mapping is None else mapping.digest,
                                "day": bucket.day.isoformat(),
                                "state": state,
                            }
                        ),
                        "day": bucket.day,
                        "state": state,
                        "mean_duration_ms": daily_total // daily_samples,
                    }
                )
            )
    status = (
        "unavailable"
        if not last_usable
        else "observed_slowdown"
        if active
        else "pending"
        if high_streak
        else "recovered"
        if events
        else "clear"
    )
    return DegradationResult.model_validate(
        {
            "status": status,
            "reason": "observed_duration_only" if last_usable else "insufficient_samples",
            "baseline_until": buckets[BASELINE_DAYS].day,
            "baseline_mean_ms": mean,
            "baseline_samples": samples,
            "events": tuple(events),
        }
    )
