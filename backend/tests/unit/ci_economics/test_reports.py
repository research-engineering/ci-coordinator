from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from ci_coordinator.ci_economics.reports import (
    CounterUnavailableReason,
    ReportCounter,
    ReportedWorkload,
    ReportMeasurement,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .factories import ATTEMPT, NOW
from .report_factories import measurement_report


@pytest.mark.parametrize("value", [0, 1, MAX_SAFE_JSON_INTEGER])
def test_observed_counters_preserve_exact_safe_integer_values(value: int) -> None:
    measurement = ReportMeasurement("cpu_user", value)

    assert measurement.canonical_mapping() == {
        "counter": "cpu_user",
        "unit": "microsecond",
        "scope": "waited_children",
        "value": value,
        "unavailableReason": None,
    }


@pytest.mark.parametrize("counter", ["cpu_user", "cpu_system", "elapsed"])
@pytest.mark.parametrize(
    "reason", ["unsupported_platform", "counter_error", "out_of_range", "incomplete_scope"]
)
def test_unavailable_is_not_a_zero_counter(
    counter: ReportCounter, reason: CounterUnavailableReason
) -> None:
    measurement = ReportMeasurement(counter, None, reason)

    assert measurement.value_us is None
    assert measurement.unavailable_reason == reason
    assert measurement.scope == ("reporter_interval" if counter == "elapsed" else "waited_children")


@pytest.mark.parametrize("value", [True, False, -1, MAX_SAFE_JSON_INTEGER + 1, 1.0, "1"])
def test_counter_rejects_coercion_negative_and_unsafe_values(value: object) -> None:
    with pytest.raises(ValueError, match="safe integer"):
        ReportMeasurement("cpu_user", cast(int, value))


@pytest.mark.parametrize("counter", ["", "billing", "cpu_total", [], None])
def test_counter_rejects_an_unknown_definition(counter: object) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        ReportMeasurement(cast(ReportCounter, counter), 1)


@pytest.mark.parametrize("reason", [None, "unknown", [], 1])
def test_missing_counter_requires_a_closed_reason(reason: object) -> None:
    with pytest.raises(ValueError, match="exact reason"):
        ReportMeasurement("cpu_user", None, cast(CounterUnavailableReason, reason))


def test_known_value_cannot_also_be_unavailable() -> None:
    with pytest.raises(ValueError, match="no reason"):
        ReportMeasurement("cpu_user", 0, "counter_error")


@pytest.mark.parametrize("measurements", [(), (ReportMeasurement("cpu_user", 1),) * 3])
def test_report_requires_every_counter_once(measurements: tuple[ReportMeasurement, ...]) -> None:
    with pytest.raises(ValueError, match="each method counter exactly once"):
        measurement_report(measurements=measurements)


def test_report_order_and_timezone_do_not_change_canonical_evidence() -> None:
    report = measurement_report()
    reordered = replace(
        report,
        measurements=tuple(reversed(report.measurements)),
        reported_at=NOW.astimezone(timezone(timedelta(hours=-3))),
    )

    assert reordered == report
    assert reordered.reported_at.tzinfo is UTC
    assert reordered.report_digest == report.report_digest
    assert report.canonical_mapping()["method"] == "waited_children/v1"


def test_sample_identity_is_not_renewed_by_changed_observation_or_producer() -> None:
    report = measurement_report()
    changed = replace(report, reported_at=NOW + timedelta(seconds=1), producer_digest="f" * 64)

    assert changed.report_id == report.report_id
    assert changed.report_digest != report.report_digest


@pytest.mark.parametrize("dimension", ["attempt", "job", "sample"])
def test_report_identity_binds_every_sample_coordinate(dimension: str) -> None:
    report = measurement_report()
    if dimension == "attempt":
        changed = replace(report, attempt=replace(ATTEMPT, run_attempt=3))
    elif dimension == "job":
        changed = replace(report, provider_job_id=406)
    else:
        changed = replace(report, sample_key="backend-lint")
    assert changed.report_id != report.report_id


@pytest.mark.parametrize("value", [0, -1, True, MAX_SAFE_JSON_INTEGER + 1, "1"])
@pytest.mark.parametrize("field", ["provider_job_id", "check_run_id"])
def test_job_ids_are_exact_positive_integers(field: str, value: object) -> None:
    report = measurement_report()
    with pytest.raises(ValueError, match="job identities"):
        if field == "provider_job_id":
            replace(report, provider_job_id=cast(int, value))
        else:
            replace(report, check_run_id=cast(int, value))


@pytest.mark.parametrize("value", ["", "a" * 129, "a/b", "a b", "a\n", 1])
def test_sample_key_is_bounded_non_command_text(value: object) -> None:
    with pytest.raises(ValueError, match="ASCII identifier"):
        replace(measurement_report(), sample_key=cast(str, value))


@pytest.mark.parametrize("value", [None, NOW.replace(tzinfo=None), "2026-09-04T10:00:00Z"])
def test_report_time_requires_an_aware_instant(value: object) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(measurement_report(), reported_at=cast(datetime, value))


@pytest.mark.parametrize(
    "value", [True, "0", MAX_SAFE_JSON_INTEGER + 1, -MAX_SAFE_JSON_INTEGER - 1]
)
def test_exit_code_is_not_coerced_or_truncated(value: object) -> None:
    with pytest.raises(ValueError, match="exit code"):
        replace(measurement_report(), command_exit_code=cast(int, value))


@pytest.mark.parametrize("value", [-15, 0, 1, 255])
def test_failed_and_signal_terminated_commands_are_retained(value: int) -> None:
    assert replace(measurement_report(), command_exit_code=value).command_exit_code == value


@pytest.mark.parametrize(
    "field", ["protected_inputs_digest", "runner_class_digest", "cache_class_digest"]
)
def test_workload_dimensions_reject_non_digest_values(field: str) -> None:
    workload = measurement_report().workload
    with pytest.raises(ValueError, match="SHA-256"):
        if field == "protected_inputs_digest":
            replace(workload, protected_inputs_digest="x")
        elif field == "runner_class_digest":
            replace(workload, runner_class_digest="x")
        else:
            replace(workload, cache_class_digest="x")


def test_report_rejects_non_owner_workload_and_invalid_producer() -> None:
    with pytest.raises(TypeError, match="workload declaration"):
        replace(measurement_report(), workload=cast(ReportedWorkload, None))
    with pytest.raises(ValueError, match="producer"):
        replace(measurement_report(), producer_digest="x")
