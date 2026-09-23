from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from ci_coordinator.ci_economics.comparison import (
    ReportCounterDifference,
    ReportPairComparison,
    ReportPairMismatch,
)
from ci_coordinator.ci_economics.reports import (
    JobMeasurementReport,
    ReportMeasurement,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .factories import ATTEMPT
from .report_factories import measurement_report


@pytest.mark.parametrize(
    ("baseline", "treatment", "difference", "ratio"),
    [
        (100, 60, 40, (40, 100)),
        (60, 100, -40, (-40, 60)),
        (100, 100, 0, (0, 100)),
        (0, 0, 0, None),
        (0, 100, -100, None),
        (
            MAX_SAFE_JSON_INTEGER,
            0,
            MAX_SAFE_JSON_INTEGER,
            (MAX_SAFE_JSON_INTEGER, MAX_SAFE_JSON_INTEGER),
        ),
    ],
)
def test_difference_preserves_signed_integer_arithmetic_and_exact_ratio(
    baseline: int,
    treatment: int,
    difference: int,
    ratio: tuple[int, int] | None,
) -> None:
    result = ReportCounterDifference(
        ReportMeasurement("cpu_user", baseline),
        ReportMeasurement("cpu_user", treatment),
    )

    assert result.reduction_us == difference
    assert result.relative_reduction == ratio


@pytest.mark.parametrize("missing_side", ["baseline", "treatment", "both"])
def test_unavailable_counter_never_becomes_a_numeric_difference(missing_side: str) -> None:
    known = ReportMeasurement("elapsed", 100)
    unknown = ReportMeasurement("elapsed", None, "counter_error")
    result = ReportCounterDifference(
        unknown if missing_side in {"baseline", "both"} else known,
        unknown if missing_side in {"treatment", "both"} else known,
    )

    assert result.reduction_us is None
    assert result.relative_reduction is None


def test_difference_cannot_mix_cpu_and_elapsed_or_distinct_cpu_counters() -> None:
    user = ReportMeasurement("cpu_user", 100)
    for other in ("cpu_system", "elapsed"):
        with pytest.raises(ValueError, match="mix measurement definitions"):
            ReportCounterDifference(user, ReportMeasurement(other, 100))


def test_matching_reports_remain_descriptive_not_a_coverage_or_causality_proof() -> None:
    result = ReportPairComparison(measurement_report(), _treatment())

    assert result.mismatches == ()
    assert len(result.differences) == 3
    assert result.coverage_status == "not_verified"
    assert result.causal_status == "not_established"
    assert all(item.reduction_us == 0 for item in result.differences)


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ("scope", "repository_scope"),
        ("attempt", "same_attempt"),
        ("head", "source_sha"),
        ("sample", "sample_key"),
        ("producer", "producer"),
        ("inputs", "protected_inputs"),
        ("runner", "runner_class"),
        ("cache", "cache_class"),
        ("outcome", "command_outcome"),
    ],
)
def test_each_comparison_operand_independently_prevents_a_misleading_difference(
    changed: str, reason: ReportPairMismatch
) -> None:
    treatment = _treatment()
    changes = {
        "scope": replace(
            treatment, attempt=replace(treatment.attempt, scope=RepositoryScope(101, 999))
        ),
        "attempt": replace(treatment, attempt=ATTEMPT),
        "head": replace(treatment, attempt=replace(treatment.attempt, head_sha="f" * 40)),
        "sample": replace(treatment, sample_key="other"),
        "producer": replace(treatment, producer_digest="f" * 64),
        "inputs": replace(
            treatment, workload=replace(treatment.workload, protected_inputs_digest="f" * 64)
        ),
        "runner": replace(
            treatment, workload=replace(treatment.workload, runner_class_digest="f" * 64)
        ),
        "cache": replace(
            treatment, workload=replace(treatment.workload, cache_class_digest="f" * 64)
        ),
        "outcome": replace(treatment, command_exit_code=1),
    }

    result = ReportPairComparison(measurement_report(), changes[changed])

    assert result.mismatches == (reason,)
    assert result.differences == ()
    assert result.treatment == changes[changed]


def test_all_applicable_mismatches_are_reported_in_stable_order() -> None:
    treatment = replace(_treatment(), sample_key="other", command_exit_code=-15)

    result = ReportPairComparison(measurement_report(), treatment)

    assert result.mismatches == ("sample_key", "command_outcome")


def test_pair_digest_binds_direction_and_exact_evidence_not_only_sample_identity() -> None:
    baseline, treatment = measurement_report(), _treatment()
    initial = ReportPairComparison(baseline, treatment)
    changed = replace(treatment, producer_digest="f" * 64)

    assert initial.pair_digest != ReportPairComparison(treatment, baseline).pair_digest
    assert changed.report_id == treatment.report_id
    assert initial.pair_digest != ReportPairComparison(baseline, changed).pair_digest


def test_equal_failed_outcomes_remain_visible_without_becoming_verified_savings() -> None:
    result = ReportPairComparison(
        replace(measurement_report(), command_exit_code=1),
        replace(_treatment(), command_exit_code=1),
    )

    assert result.mismatches == ()
    assert result.baseline.command_exit_code == result.treatment.command_exit_code == 1
    assert result.coverage_status == "not_verified"


def test_comparison_requires_nominal_report_and_measurement_values() -> None:
    with pytest.raises(TypeError, match="measurement reports"):
        ReportPairComparison(cast(JobMeasurementReport, None), _treatment())
    with pytest.raises(TypeError, match="report measurements"):
        ReportCounterDifference(cast(ReportMeasurement, None), ReportMeasurement("elapsed", 1))


def _treatment() -> JobMeasurementReport:
    return measurement_report(attempt=replace(ATTEMPT, workflow_run_id=304))
