from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.ci_economics.reports import (
    JobMeasurementReport,
    ReportMeasurement,
    StoredMeasurementReport,
)
from ci_coordinator.kernel import hash_object

type ReportPairMismatch = Literal[
    "repository_scope",
    "same_attempt",
    "source_sha",
    "sample_key",
    "producer",
    "protected_inputs",
    "runner_class",
    "cache_class",
    "command_outcome",
]


@dataclass(frozen=True, slots=True)
class RetainedReportPair:
    baseline: StoredMeasurementReport
    treatment: StoredMeasurementReport

    def __post_init__(self) -> None:
        if (
            type(self.baseline) is not StoredMeasurementReport
            or type(self.treatment) is not StoredMeasurementReport
        ):
            raise TypeError("retained comparison requires exact stored reports")

    @property
    def comparison(self) -> ReportPairComparison:
        return ReportPairComparison(self.baseline.report, self.treatment.report)


@dataclass(frozen=True, slots=True)
class ReportCounterDifference:
    baseline: ReportMeasurement
    treatment: ReportMeasurement

    def __post_init__(self) -> None:
        if (
            type(self.baseline) is not ReportMeasurement
            or type(self.treatment) is not ReportMeasurement
        ):
            raise TypeError("counter difference requires exact report measurements")
        if self.baseline.counter != self.treatment.counter:
            raise ValueError("counter difference cannot mix measurement definitions")

    @property
    def reduction_us(self) -> int | None:
        if self.baseline.value_us is None or self.treatment.value_us is None:
            return None
        return self.baseline.value_us - self.treatment.value_us

    @property
    def relative_reduction(self) -> tuple[int, int] | None:
        reduction = self.reduction_us
        if reduction is None or self.baseline.value_us == 0 or self.baseline.value_us is None:
            return None
        return reduction, self.baseline.value_us


@dataclass(frozen=True, slots=True)
class ReportPairComparison:
    """Matched assertions yield descriptive differences, never verified CI savings."""

    baseline: JobMeasurementReport
    treatment: JobMeasurementReport

    def __post_init__(self) -> None:
        if (
            type(self.baseline) is not JobMeasurementReport
            or type(self.treatment) is not JobMeasurementReport
        ):
            raise TypeError("report comparison requires exact measurement reports")

    @property
    def pair_digest(self) -> str:
        return hash_object(
            {
                "schemaVersion": "ci-economics-report-comparison/v1",
                "baselineReportDigest": self.baseline.report_digest,
                "treatmentReportDigest": self.treatment.report_digest,
            }
        )

    @property
    def coverage_status(self) -> Literal["not_verified"]:
        return "not_verified"

    @property
    def causal_status(self) -> Literal["not_established"]:
        return "not_established"

    @property
    def mismatches(self) -> tuple[ReportPairMismatch, ...]:
        baseline, treatment = self.baseline, self.treatment
        dimensions: tuple[tuple[ReportPairMismatch, bool], ...] = (
            ("repository_scope", baseline.attempt.scope != treatment.attempt.scope),
            ("same_attempt", baseline.attempt == treatment.attempt),
            ("source_sha", baseline.attempt.head_sha != treatment.attempt.head_sha),
            ("sample_key", baseline.sample_key != treatment.sample_key),
            ("producer", baseline.producer_digest != treatment.producer_digest),
            (
                "protected_inputs",
                baseline.workload.protected_inputs_digest
                != treatment.workload.protected_inputs_digest,
            ),
            (
                "runner_class",
                baseline.workload.runner_class_digest != treatment.workload.runner_class_digest,
            ),
            (
                "cache_class",
                baseline.workload.cache_class_digest != treatment.workload.cache_class_digest,
            ),
            ("command_outcome", baseline.command_exit_code != treatment.command_exit_code),
        )
        return tuple(reason for reason, mismatched in dimensions if mismatched)

    @property
    def differences(self) -> tuple[ReportCounterDifference, ...]:
        if self.mismatches:
            return ()
        return tuple(
            ReportCounterDifference(baseline, treatment)
            for baseline, treatment in zip(
                self.baseline.measurements, self.treatment.measurements, strict=True
            )
        )
