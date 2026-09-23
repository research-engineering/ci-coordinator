from ci_coordinator.ci_economics.archive_analytics import summarize_archive
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
