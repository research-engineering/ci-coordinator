from __future__ import annotations

from datetime import timedelta

from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.reports import (
    JobMeasurementReport,
    MeasurementReportOrigin,
    ReportedWorkload,
    ReportMeasurement,
    StoredMeasurementReport,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource

from .factories import ATTEMPT, NOW


def measurement_report(
    *,
    attempt: AttemptIdentity = ATTEMPT,
    measurements: tuple[ReportMeasurement, ...] | None = None,
) -> JobMeasurementReport:
    return JobMeasurementReport(
        attempt=attempt,
        provider_job_id=404,
        check_run_id=405,
        sample_key="backend-tests",
        producer_digest="a" * 64,
        workload=ReportedWorkload("b" * 64, "c" * 64, "d" * 64),
        reported_at=NOW,
        command_exit_code=0,
        measurements=(
            ReportMeasurement("cpu_user", 1_000),
            ReportMeasurement("cpu_system", 200),
            ReportMeasurement("elapsed", 800),
        )
        if measurements is None
        else measurements,
    )


def stored_report(report: JobMeasurementReport | None = None) -> StoredMeasurementReport:
    value = measurement_report() if report is None else report
    return StoredMeasurementReport(
        value,
        ProviderRunCollectionSource(value.attempt, NOW, "2026-03-10", "f" * 64),
        MeasurementReportOrigin("a" * 64, "b" * 64),
        NOW + timedelta(seconds=1),
        NOW + timedelta(days=90),
    )
