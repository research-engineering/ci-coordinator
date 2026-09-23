from datetime import timedelta

import pytest

from ci_coordinator.ci_economics.archive_analytics import summarize_archive
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsQuery,
    AnalyticsSnapshot,
    JobAggregate,
    PurposeEntry,
    PurposeMapping,
)
from ci_economics.archive_analytics_factories import START, analytics_query, analytics_snapshot


def test_retained_daily_units_and_unknown_provenance_remain_explicit() -> None:
    result = summarize_archive(analytics_snapshot())
    assert result.runs == result.attempts == 30
    assert result.unit == "milliseconds" and result.provider_coverage == "unknown"
    assert result.buckets[0].observed_runner_ms == 30000
    assert result.buckets[0].observed_queue_ms == 3000
    assert not result.cohort.compatible and result.cohort.contributors == "unclassified"
    assert result.forecast.monetary_estimate is None
    assert result.forecast.monetary_unavailable_reason == "no_explicit_versioned_tariff"


@pytest.mark.parametrize("population", ["partial", "unavailable", "conflict", "unknown"])
def test_incomplete_day_never_becomes_a_zero_forecast(population: str) -> None:
    snapshot = analytics_snapshot()
    bucket = snapshot.buckets[0]
    changes: dict[str, object] = {"complete_attempts": 0, "coverage": "partial"}
    if population == "unknown":
        changes.update(coverage="unknown", runs=0, attempts=0, matching_attempts=0)
    else:
        changes[f"{population}_attempts"] = 1
        changes["unknown_population_attempts"] = 1
    if population != "partial":
        changes.update(selected=JobAggregate(), observed_runner_ms=None, observed_queue_ms=None)
    if population == "conflict":
        changes["conflict_excluded_jobs"] = 3
    buckets = (
        type(bucket).model_validate(bucket.model_copy(update=changes)),
        *snapshot.buckets[1:],
    )
    updated = AnalyticsSnapshot.model_validate(
        snapshot.model_copy(
            update={
                "buckets": buckets,
                "attempts": sum(b.attempts for b in buckets),
                "runs": sum(b.runs for b in buckets),
            }
        )
    )
    result = summarize_archive(updated)
    assert result.forecast.status == "unavailable"
    assert result.forecast.reason == "incomplete_daily_coverage"
    assert result.forecast.predicted_runner_ms is None
    if population != "partial":
        assert result.buckets[0].observed_runner_ms is None


def test_constant_series_has_real_chronological_calibration_and_evaluation() -> None:
    result = summarize_archive(analytics_snapshot()).forecast
    assert result.status == "available"
    assert result.predicted_runner_ms == result.lower_runner_ms == result.upper_runner_ms == 30000
    assert result.backtest_mae_ms == 0 and result.empirical_coverage_bps == 10000
    assert result.backtest_basis == "current_archive_reconstructed_chronology"
    assert [fold.phase for fold in result.folds[:4]] == ["calibration"] * 4
    assert all(fold.phase == "evaluation" for fold in result.folds[4:])
    for index, fold in enumerate(result.folds):
        assert fold.training_until == START + timedelta(days=14 + index)
        assert fold.test_until == fold.training_until + timedelta(days=1)
        assert fold.test_until <= result.cutoff


def test_mutating_future_holdouts_cannot_rewrite_earlier_predictions_or_intervals() -> None:
    before = summarize_archive(analytics_snapshot()).forecast
    after = summarize_archive(analytics_snapshot((10000,) * 29 + (90000,))).forecast
    assert before.folds[:-1] == after.folds[:-1]
    assert before.folds[-1].predicted_runner_ms == after.folds[-1].predicted_runner_ms
    assert before.folds[-1].upper_runner_ms == after.folds[-1].upper_runner_ms
    assert before.folds[-1].actual_runner_ms != after.folds[-1].actual_runner_ms


def test_horizon_sized_folds_are_disjoint_and_short_histories_are_unavailable() -> None:
    sparse = summarize_archive(analytics_snapshot(query=analytics_query(horizon_days=30))).forecast
    assert sparse.reason == "insufficient_backtest" and sparse.predicted_runner_ms is None
    longer = summarize_archive(
        analytics_snapshot((10000,) * 254, query=analytics_query(254, horizon_days=30))
    ).forecast
    assert longer.status == "available" and longer.predicted_runner_ms == 900000
    assert len(longer.folds) == 8
    assert all(
        left.test_until == right.training_until
        for left, right in zip(longer.folds, longer.folds[1:], strict=False)
    )


def test_uncalibrated_growth_suppresses_estimate_but_preserves_evaluation() -> None:
    result = summarize_archive(
        analytics_snapshot((10000,) * 18 + (20000, 40000, 80000, 160000))
    ).forecast
    assert result.status == "unavailable" and result.reason == "poor_calibration"
    assert result.empirical_coverage_bps == 0 and result.backtest_mae_ms is not None
    assert result.predicted_runner_ms is result.lower_runner_ms is result.upper_runner_ms is None


@pytest.mark.parametrize(
    "change",
    [
        {"created_until": START},
        {"created_until": START + timedelta(days=367)},
        {"created_from": START + timedelta(seconds=1)},
        {"generation": True},
        {"horizon_days": 31},
        {"job_name": "\x00"},
        {"purpose": "guessed-linter"},
    ],
)
def test_query_boundaries_are_owner_validated(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        AnalyticsQuery.model_validate(analytics_query().model_copy(update=change))


def test_unordered_or_missing_buckets_are_rejected_before_model_fitting() -> None:
    snapshot = analytics_snapshot()
    for buckets in (snapshot.buckets[::-1], snapshot.buckets[:-1]):
        with pytest.raises(ValueError, match="each requested UTC day"):
            summarize_archive(snapshot.model_copy(update={"buckets": buckets}))


def test_mapping_has_exact_scope_version_provenance_and_no_overlapping_key_rows() -> None:
    entry = PurposeEntry(workflow_id=9001, job_name="lint and build", purposes=("lint", "build"))
    mapping = PurposeMapping(
        installation_id=101,
        repository_id=202,
        generation=1,
        version="v1",
        provenance="repository policy revision abc",
        entries=(entry,),
    )
    assert len(mapping.digest) == 64
    with pytest.raises(ValueError, match="repeats a workflow/job"):
        PurposeMapping.model_validate(mapping.model_copy(update={"entries": (entry, entry)}))
    with pytest.raises(ValueError, match="crosses repository"):
        AnalyticsSnapshot.model_validate(
            analytics_snapshot().model_copy(
                update={
                    "mapping": mapping.model_copy(update={"repository_id": 999}),
                }
            )
        )
    assert (
        AnalyticsQuery.model_validate_json(analytics_query().model_dump_json()) == analytics_query()
    )


def test_sparse_duration_samples_and_observed_definition_change_are_not_forecast_support() -> None:
    sparse = analytics_snapshot(query=analytics_query(minimum_daily_samples=4))
    assert summarize_archive(sparse).forecast.reason == "incomplete_daily_coverage"
    source = analytics_snapshot()
    changed = source.model_copy(
        update={
            "cohort": source.cohort.model_copy(update={"observed_definition_changes": True}),
        }
    )
    assert summarize_archive(changed).forecast.reason == "incompatible_cohort"
