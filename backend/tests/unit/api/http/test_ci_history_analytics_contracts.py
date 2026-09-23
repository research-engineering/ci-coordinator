import pytest
from ci_economics.archive_analytics_factories import analytics_query, analytics_snapshot

from ci_coordinator.api.http.ci_history_analytics_contracts import (
    HistoryAnalyticsResponse,
    analytics_openapi_parameters,
    parse_analytics_query,
)
from ci_coordinator.ci_economics.archive_analytics import summarize_archive
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsReport,
    AnalyticsUnavailable,
    ForecastFold,
    ForecastResult,
    PurposeEntry,
    PurposeMapping,
)


@pytest.mark.parametrize(
    "changes",
    [
        {"predicted_runner_ms": None},
        {"lower_runner_ms": None},
        {"upper_runner_ms": None},
        {"lower_runner_ms": 30001},
        {"upper_runner_ms": 29999},
        {"folds": ()},
        {"usable_days": 0},
        {"duration_samples": 0},
        {"backtest_mae_ms": None},
        {"empirical_coverage_bps": None},
        {"empirical_coverage_bps": 7999},
        {"reason": "poor_calibration"},
    ],
)
def test_available_forecast_requires_its_entire_result_algebra(changes: dict[str, object]) -> None:
    forecast = summarize_archive(analytics_snapshot()).forecast
    with pytest.raises(ValueError):
        ForecastResult.model_validate(forecast.model_copy(update=changes))


@pytest.mark.parametrize("field", ["predicted_runner_ms", "lower_runner_ms", "upper_runner_ms"])
def test_unavailable_forecast_cannot_smuggle_an_actionable_number(field: str) -> None:
    forecast = ForecastResult(
        status="unavailable",
        reason="insufficient_backtest",
        cutoff=analytics_snapshot().query.created_until,
        horizon_days=1,
    )
    with pytest.raises(ValueError, match="actionable estimate"):
        ForecastResult.model_validate(forecast.model_copy(update={field: 1}))


def test_fold_interval_and_chronology_are_cross_field_contracts() -> None:
    forecast = summarize_archive(analytics_snapshot()).forecast
    fold = forecast.folds[-1]
    with pytest.raises(ValueError, match="prediction interval"):
        ForecastFold.model_validate(fold.model_copy(update={"lower_runner_ms": 30001}))
    with pytest.raises(ValueError, match="training prefix"):
        ForecastFold.model_validate(fold.model_copy(update={"training_until": fold.test_until}))
    with pytest.raises(ValueError, match="ordered horizon-sized"):
        ForecastResult.model_validate(forecast.model_copy(update={"folds": forecast.folds[::-1]}))


@pytest.mark.parametrize(
    "outcome,report_present,unavailable_present",
    [
        ("available", False, False),
        ("available", True, True),
        ("unavailable", False, False),
        ("unavailable", True, True),
        ("unavailable", True, False),
    ],
)
def test_http_outcome_variants_are_exclusive(
    outcome: str,
    report_present: bool,
    unavailable_present: bool,
) -> None:
    with pytest.raises(ValueError):
        HistoryAnalyticsResponse.model_validate(
            {
                "outcome": outcome,
                "report": summarize_archive(analytics_snapshot()) if report_present else None,
                "unavailable": AnalyticsUnavailable(reason="query_budget_exceeded")
                if unavailable_present
                else None,
            }
        )


def test_report_binds_its_mapping_digest_cutoff_and_horizon() -> None:
    report = summarize_archive(analytics_snapshot())
    assert AnalyticsReport.model_validate_json(report.model_dump_json()) == report
    for changes in (
        {"mapping_digest": "a" * 64},
        {"query": report.query.model_copy(update={"horizon_days": 2})},
    ):
        with pytest.raises(ValueError):
            AnalyticsReport.model_validate(report.model_copy(update=changes))


def test_get_aliases_and_json_mapping_arrays_cross_only_the_declared_boundary() -> None:
    query = parse_analytics_query(
        "101",
        "202",
        {
            "generation": "1",
            "createdFrom": "2026-01-01T00:00:00Z",
            "createdUntil": "2026-01-31T00:00:00Z",
            "workflowId": "9001",
            "jobName": "lint",
            "horizonDays": "1",
            "minimumDailySamples": "3",
            "degradationRelativeBps": "2000",
            "degradationAbsoluteMs": "1000",
            "persistenceDays": "3",
        },
    )
    assert query == analytics_query()
    mapping = PurposeMapping(
        installation_id=101,
        repository_id=202,
        generation=1,
        version="v1",
        provenance="repository policy",
        entries=(PurposeEntry(workflow_id=9001, job_name="lint", purposes=("lint",)),),
    )
    assert PurposeMapping.model_validate_json(mapping.model_dump_json()) == mapping
    with pytest.raises(ValueError):
        PurposeMapping.model_validate(mapping.model_dump(mode="json"))


def test_generated_query_parameters_have_resolvable_inline_scalar_contracts() -> None:
    import json

    parameters = {parameter["name"]: parameter for parameter in analytics_openapi_parameters()}
    assert "$ref" not in json.dumps(parameters)
    assert "$defs" not in json.dumps(parameters)
    assert parameters["generation"]["required"] is True
    assert parameters["createdFrom"]["required"] is True
    horizon = parameters["horizonDays"]["schema"]
    samples = parameters["minimumDailySamples"]["schema"]
    assert isinstance(horizon, dict) and horizon["maximum"] == 30
    assert isinstance(samples, dict) and samples["minimum"] == 1
    assert "installationId" not in parameters and "repositoryId" not in parameters
