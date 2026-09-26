import pytest

from ci_coordinator.ci_economics.archive_analytics import summarize_archive
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsSnapshot,
    DailyBucket,
    DegradationResult,
    JobAggregate,
)
from ci_economics.archive_analytics_factories import analytics_query, analytics_snapshot


def test_unknown_workflow_bytes_still_allow_descriptive_sustained_slowdown() -> None:
    report = summarize_archive(analytics_snapshot((10000,) * 7 + (14000,) * 3))
    assert not report.cohort.compatible
    assert report.degradation.status == "observed_slowdown"
    assert report.degradation.contributor == "unclassified"
    assert report.degradation.baseline_samples == 21
    assert report.degradation.baseline_mean_ms == 10000
    assert report.degradation.events[0].day == report.buckets[-1].day
    assert report.degradation.events[0].mean_duration_ms == 14000


def test_gradual_growth_and_abrupt_growth_are_observations_not_causal_classifications() -> None:
    result = summarize_archive(
        analytics_snapshot((10000,) * 7 + (10500, 11000, 12000, 13000, 14000))
    ).degradation
    assert result.status == "observed_slowdown" and result.contributor == "unclassified"


def test_entry_boundary_requires_both_effect_and_persistence() -> None:
    pending = summarize_archive(analytics_snapshot((10000,) * 7 + (12000,) * 2)).degradation
    entered = summarize_archive(analytics_snapshot((10000,) * 7 + (12000,) * 3)).degradation
    control = summarize_archive(analytics_snapshot((10000,) * 7 + (11999,) * 3)).degradation
    assert pending.status == "pending" and pending.events == ()
    assert entered.status == "observed_slowdown"
    assert control.status == "clear" and control.events == ()
    absolute = summarize_archive(analytics_snapshot((100,) * 7 + (130,) * 3)).degradation
    assert absolute.status == "clear"


def test_hysteresis_holds_middle_band_and_emits_one_recovery() -> None:
    held = summarize_archive(
        analytics_snapshot((10000,) * 7 + (14000,) * 3 + (11500,) * 3)
    ).degradation
    recovered = summarize_archive(
        analytics_snapshot((10000,) * 7 + (14000,) * 3 + (11500,) * 3 + (10000,) * 3)
    ).degradation
    assert held.status == "observed_slowdown" and len(held.events) == 1
    assert recovered.status == "recovered"
    assert held.events[0].key == recovered.events[0].key
    assert [event.state for event in recovered.events] == ["observed_slowdown", "recovered"]
    assert (
        summarize_archive(
            analytics_snapshot((10000,) * 7 + (14000,) * 3 + (11500,) * 3 + (10000,) * 3)
        ).degradation.events
        == recovered.events
    )


def test_unqualified_cohort_is_not_silently_presented_as_compatible() -> None:
    report = summarize_archive(
        analytics_snapshot(
            (10000,) * 7 + (14000,) * 3,
            query=analytics_query(10, workflow_id=None, job_name=None),
        )
    )
    assert report.degradation.status == "unavailable"
    assert report.degradation.reason == "incompatible_cohort"


def test_insufficient_daily_samples_cannot_prove_recovery() -> None:
    snapshot = analytics_snapshot((10000,) * 7 + (14000,) * 3 + (10000,) * 3)
    last = snapshot.buckets[-1]
    partial = last.model_copy(
        update={
            "coverage": "partial",
            "complete_attempts": 0,
            "partial_attempts": 1,
            "known_missing_jobs": 1,
        }
    )
    result = summarize_archive(
        snapshot.model_copy(
            update={
                "buckets": (*snapshot.buckets[:-1], partial),
            }
        )
    ).degradation
    assert result.status == "unavailable" and len(result.events) == 1
    assert result.events[0].state == "observed_slowdown"


@pytest.mark.parametrize("gap_index", range(7))
@pytest.mark.parametrize(
    "kind",
    ("unknown", "partial", "insufficient_samples", "missing_duration", "excluded_conflict"),
)
def test_first_seven_usable_days_skip_each_baseline_gap(gap_index: int, kind: str) -> None:
    durations = tuple(1_000_000 if index == gap_index else 10000 for index in range(8))
    source = analytics_snapshot(durations + (14000,) * 3)
    buckets = tuple(
        _unusable_bucket(bucket, kind) if index == gap_index else bucket
        for index, bucket in enumerate(source.buckets)
    )
    result = summarize_archive(_replace_buckets(source, buckets)).degradation
    control = summarize_archive(analytics_snapshot((10000,) * 8 + (14000,) * 3)).degradation

    assert result.status == "observed_slowdown"
    assert result.reason == "observed_duration_only"
    assert result.baseline_until == source.buckets[8].day
    assert result.baseline_mean_ms == 10000
    assert result.baseline_samples == 21
    assert len(result.events) == 1
    assert result.events[0].day == source.buckets[10].day
    assert result.events[0].mean_duration_ms == 14000
    assert result.events == control.events
    assert result.contributor == "unclassified"


@pytest.mark.parametrize("usable_days", (6, 7, 8))
@pytest.mark.parametrize("unknown_tail", (False, True))
@pytest.mark.parametrize("spaced", (False, True), ids=("contiguous", "spaced"))
def test_baseline_requires_a_remaining_calendar_day(
    usable_days: int, unknown_tail: bool, spaced: bool
) -> None:
    days = (2 * usable_days - 1 if spaced else usable_days) + int(unknown_tail)
    source = analytics_snapshot((10000,) * days)
    buckets = tuple(
        _unusable_bucket(bucket, "unknown")
        if (spaced and index % 2) or (unknown_tail and index == days - 1)
        else bucket
        for index, bucket in enumerate(source.buckets)
    )
    result = summarize_archive(_replace_buckets(source, buckets)).degradation

    assert result.events == ()
    if usable_days < 7 or (usable_days == 7 and not unknown_tail):
        assert result == DegradationResult(status="unavailable", reason="insufficient_samples")
    else:
        assert result.baseline_until == source.buckets[13 if spaced else 7].day
        assert result.baseline_mean_ms == 10000
        assert result.baseline_samples == 21
        assert result.status == ("unavailable" if unknown_tail else "clear")
        assert result.reason == (
            "insufficient_samples" if unknown_tail else "observed_duration_only"
        )


@pytest.mark.parametrize(
    "after,status,states,indices",
    (
        pytest.param((10000,), "clear", (), (), id="steady"),
        pytest.param((14000,) * 2, "pending", (), (), id="entry-pending"),
        pytest.param((14000,) * 3, "observed_slowdown", ("observed_slowdown",), (9,), id="entered"),
        pytest.param(
            (14000,) * 3 + (11500,) * 3,
            "observed_slowdown",
            ("observed_slowdown",),
            (9,),
            id="middle-band-holds",
        ),
        pytest.param(
            (14000,) * 3 + (10000,) * 3,
            "recovered",
            ("observed_slowdown", "recovered"),
            (9, 12),
            id="recovered",
        ),
    ),
)
def test_no_gap_baseline_preserves_declared_results(
    after: tuple[int, ...], status: str, states: tuple[str, ...], indices: tuple[int, ...]
) -> None:
    source = analytics_snapshot((10000,) * 7 + after)
    result = summarize_archive(source).degradation

    assert result.status == status
    assert result.reason == "observed_duration_only"
    assert result.baseline_until == source.buckets[7].day
    assert result.baseline_mean_ms == 10000
    assert result.baseline_samples == 21
    assert tuple(event.state for event in result.events) == states
    assert tuple(event.day for event in result.events) == tuple(
        source.buckets[index].day for index in indices
    )


@pytest.mark.parametrize(
    "absolute,duration,residual,expected",
    (
        pytest.param(1000, 2499, 0, "clear", id="below-absolute"),
        pytest.param(1000, 2500, 0, "observed_slowdown", id="absolute-equality"),
        pytest.param(1000, 2500, 1, "clear", id="absolute-no-flooring"),
        pytest.param(100, 1799, 0, "clear", id="below-relative"),
        pytest.param(100, 1800, 0, "observed_slowdown", id="relative-equality"),
        pytest.param(100, 1800, 1, "clear", id="relative-no-flooring"),
    ),
)
def test_gapped_baseline_preserves_job_weighting_and_exact_thresholds(
    absolute: int, duration: int, residual: int, expected: str
) -> None:
    source = analytics_snapshot(
        (1000,) * 7 + (3000,) + (duration,) * 3,
        query=analytics_query(11, degradation_absolute_ms=absolute),
    )
    buckets = list(source.buckets)
    buckets[3] = _unusable_bucket(buckets[3], "unknown")
    heavy = JobAggregate(
        jobs=6,
        duration_samples=6,
        queue_samples=6,
        runner_ms=18000 + residual,
        queue_ms=6000,
        unknown_purpose_jobs=6,
    )
    buckets[7] = buckets[7].model_copy(
        update={
            "selected": heavy,
            "observed_runner_ms": heavy.runner_ms,
            "observed_queue_ms": heavy.queue_ms,
        }
    )
    result = summarize_archive(_replace_buckets(source, tuple(buckets))).degradation

    assert result.baseline_samples == 24
    assert result.baseline_mean_ms == 1500
    assert result.baseline_until == source.buckets[8].day
    assert result.status == expected
    if expected == "clear":
        assert result.events == ()
    else:
        assert len(result.events) == 1
        assert result.events[0].day == source.buckets[10].day
        assert result.events[0].mean_duration_ms == duration


@pytest.mark.parametrize("phase", ("entry", "recovery"))
@pytest.mark.parametrize("supported_after_gap", (2, 3))
def test_post_baseline_gap_resets_consecutive_entry_and_recovery_days(
    phase: str, supported_after_gap: int
) -> None:
    prefix = (10000,) * 7 + (() if phase == "entry" else (14000,) * 3)
    value = 14000 if phase == "entry" else 10000
    gap_index = len(prefix) + 2
    source = analytics_snapshot(prefix + (value,) * (3 + supported_after_gap))
    buckets = tuple(
        _unusable_bucket(bucket, "unknown") if index == gap_index else bucket
        for index, bucket in enumerate(source.buckets)
    )
    result = summarize_archive(_replace_buckets(source, buckets)).degradation

    assert result.baseline_until == source.buckets[7].day
    states: tuple[str, ...]
    if phase == "entry":
        assert result.status == ("pending" if supported_after_gap == 2 else "observed_slowdown")
        states = () if supported_after_gap == 2 else ("observed_slowdown",)
    else:
        assert result.status == ("observed_slowdown" if supported_after_gap == 2 else "recovered")
        states = (
            ("observed_slowdown",)
            if supported_after_gap == 2
            else (
                "observed_slowdown",
                "recovered",
            )
        )
        assert result.events[0].day == source.buckets[9].day
    assert tuple(event.state for event in result.events) == states
    if supported_after_gap == 3:
        assert result.events[-1].day == source.buckets[-1].day


@pytest.mark.parametrize("tail_days", (1, 3))
@pytest.mark.parametrize("recovered", (False, True))
def test_unknown_tail_preserves_historical_events_from_a_gapped_baseline(
    tail_days: int, recovered: bool
) -> None:
    durations = (10000,) * 8 + (14000,) * 3 + ((10000,) * 3 if recovered else ())
    before = analytics_snapshot(durations)
    before = _replace_buckets(
        before,
        tuple(
            _unusable_bucket(bucket, "unknown") if index == 3 else bucket
            for index, bucket in enumerate(before.buckets)
        ),
    )
    prior = summarize_archive(before).degradation
    assert prior.status == ("recovered" if recovered else "observed_slowdown")
    assert len(prior.events) == (2 if recovered else 1)
    extended = analytics_snapshot(durations + (10000,) * tail_days)
    extended = _replace_buckets(
        extended,
        tuple(
            _unusable_bucket(bucket, "unknown") if index == 3 or index >= len(durations) else bucket
            for index, bucket in enumerate(extended.buckets)
        ),
    )
    result = summarize_archive(extended).degradation

    assert result.status == "unavailable"
    assert result.reason == "insufficient_samples"
    assert result.baseline_until == prior.baseline_until
    assert result.baseline_mean_ms == prior.baseline_mean_ms
    assert result.baseline_samples == prior.baseline_samples
    assert result.events == prior.events


@pytest.mark.parametrize("slowdown", (False, True))
def test_changed_cohort_preserves_descriptive_output_and_the_forecast_gate(
    slowdown: bool,
) -> None:
    source = analytics_snapshot((10000,) * 7 + ((14000,) * 23 if slowdown else (10000,) * 23))
    control = summarize_archive(source)
    cohort = source.cohort.model_copy(
        update={
            "known_workflow_versions": 2,
            "unknown_workflow_attempts": 0,
            "event_count": 2,
            "definition_stable": False,
            "observed_definition_changes": True,
            "compatible": False,
        }
    )
    result = summarize_archive(source.model_copy(update={"cohort": cohort}))

    assert result.cohort == cohort
    assert result.degradation == control.degradation
    assert result.degradation.status == ("observed_slowdown" if slowdown else "clear")
    assert result.degradation.contributor == "unclassified"
    assert result.forecast.status == "unavailable"
    assert result.forecast.reason == "incompatible_cohort"
    assert result.forecast.predicted_runner_ms is None
    if not slowdown:
        assert control.forecast.status == "available"


def _replace_buckets(
    snapshot: AnalyticsSnapshot, buckets: tuple[DailyBucket, ...]
) -> AnalyticsSnapshot:
    attempts = sum(bucket.attempts for bucket in buckets)
    return AnalyticsSnapshot.model_validate(
        snapshot.model_copy(
            update={
                "buckets": buckets,
                "runs": sum(bucket.runs for bucket in buckets),
                "attempts": attempts,
                "cohort": snapshot.cohort.model_copy(
                    update={"unknown_workflow_attempts": attempts}
                ),
            }
        )
    )


def _unusable_bucket(bucket: DailyBucket, kind: str) -> DailyBucket:
    changes: dict[str, object]
    if kind == "unknown":
        changes = {
            "coverage": "unknown",
            "runs": 0,
            "attempts": 0,
            "matching_attempts": 0,
            "complete_attempts": 0,
            "selected": JobAggregate(),
            "observed_runner_ms": None,
            "observed_queue_ms": None,
        }
    elif kind == "partial":
        changes = {"coverage": "partial", "complete_attempts": 0, "partial_attempts": 1}
    elif kind == "insufficient_samples":
        selected = JobAggregate(
            jobs=2,
            duration_samples=2,
            queue_samples=2,
            runner_ms=bucket.selected.runner_ms * 2 // 3,
            queue_ms=2000,
            unknown_purpose_jobs=2,
        )
        changes = {
            "selected": selected,
            "observed_runner_ms": selected.runner_ms,
            "observed_queue_ms": selected.queue_ms,
        }
    elif kind == "missing_duration":
        changes = {
            "selected": bucket.selected.model_copy(
                update={
                    "jobs": 4,
                    "queue_samples": 4,
                    "queue_ms": 4000,
                    "missing_duration": 1,
                    "unknown_purpose_jobs": 4,
                }
            ),
            "observed_queue_ms": 4000,
        }
    elif kind == "excluded_conflict":
        changes = {"conflict_excluded_jobs": 1}
    else:
        raise AssertionError("unsupported test bucket quality")
    return DailyBucket.model_validate(bucket.model_copy(update=changes))
