from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from ci_coordinator.api.http.ci_economics_measurement_contracts import Digest
from ci_coordinator.api.http.ci_economics_source_contracts import (
    EconomicsProviderSourceResponse,
    provider_source_response,
)
from ci_coordinator.api.http.model_contracts import ProjectedResponseModel, ResponseModel
from ci_coordinator.ci_economics.budget import EvaluatedReportBudget, ReportBudgetOutcome
from ci_coordinator.ci_economics.comparison import (
    ReportCounterDifference,
    ReportPairMismatch,
    RetainedReportPair,
)
from ci_coordinator.ci_economics.report_payload import JobMeasurementPayload, ReportCounterPayload
from ci_coordinator.ci_economics.reports import ReportCounter, StoredMeasurementReport
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type SignedCounter = Annotated[int, Field(ge=-MAX_SAFE_JSON_INTEGER, le=MAX_SAFE_JSON_INTEGER)]
type PositiveCounter = Annotated[int, Field(ge=1, le=MAX_SAFE_JSON_INTEGER)]


class MeasurementReportOriginResponse(ProjectedResponseModel):
    producer_claim_hash: Digest
    provider_binding_digest: Digest


class RetainedMeasurementReportResponse(ResponseModel):
    schema_version: Literal["ci-economics-retained-report/v1"]
    ok: Literal[True]
    report_id: Digest
    report_digest: Digest
    source: EconomicsProviderSourceResponse
    payload: JobMeasurementPayload
    origin: MeasurementReportOriginResponse
    received_at: datetime
    retain_until: datetime


class RelativeReductionResponse(ResponseModel):
    numerator: SignedCounter
    denominator: PositiveCounter


class CounterDifferenceResponse(ResponseModel):
    counter: ReportCounter
    unit: Literal["microsecond"]
    scope: Literal["waited_children", "reporter_interval"]
    reduction: SignedCounter | None
    relative_reduction: RelativeReductionResponse | None


class MeasurementReportComparisonResponse(ResponseModel):
    schema_version: Literal["ci-economics-report-comparison/v1"]
    ok: Literal[True]
    baseline: RetainedMeasurementReportResponse
    treatment: RetainedMeasurementReportResponse
    pair_digest: Digest
    mismatches: tuple[ReportPairMismatch, ...]
    differences: tuple[CounterDifferenceResponse, ...]
    coverage_status: Literal["not_verified"]
    causal_status: Literal["not_established"]


class MeasurementReportBudgetResponse(ResponseModel):
    schema_version: Literal["ci-economics-report-budget/v1"] = "ci-economics-report-budget/v1"
    ok: Literal[True] = True
    report: RetainedMeasurementReportResponse
    measurement: ReportCounterPayload
    maximum_us: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    threshold_authority: Literal["caller_supplied"] = "caller_supplied"
    evaluation_window: Literal["exact_report"] = "exact_report"
    outcome: ReportBudgetOutcome


def report_budget_response(evaluation: EvaluatedReportBudget) -> MeasurementReportBudgetResponse:
    return MeasurementReportBudgetResponse(
        report=retained_report_response(evaluation.record),
        measurement=ReportCounterPayload.model_validate(evaluation.measurement.canonical_mapping()),
        maximum_us=evaluation.budget.maximum_us,
        outcome=evaluation.outcome,
    )


def retained_report_response(record: StoredMeasurementReport) -> RetainedMeasurementReportResponse:
    return RetainedMeasurementReportResponse(
        schema_version="ci-economics-retained-report/v1",
        ok=True,
        report_id=record.report.report_id,
        report_digest=record.report.report_digest,
        source=provider_source_response(record.source),
        payload=JobMeasurementPayload.model_validate(record.report.canonical_mapping()),
        origin=MeasurementReportOriginResponse.model_validate(record.origin),
        received_at=record.received_at,
        retain_until=record.retain_until,
    )


def report_comparison_response(pair: RetainedReportPair) -> MeasurementReportComparisonResponse:
    comparison = pair.comparison
    return MeasurementReportComparisonResponse(
        schema_version="ci-economics-report-comparison/v1",
        ok=True,
        baseline=retained_report_response(pair.baseline),
        treatment=retained_report_response(pair.treatment),
        pair_digest=comparison.pair_digest,
        mismatches=comparison.mismatches,
        differences=tuple(
            _difference_response(difference) for difference in comparison.differences
        ),
        coverage_status=comparison.coverage_status,
        causal_status=comparison.causal_status,
    )


def _difference_response(difference: ReportCounterDifference) -> CounterDifferenceResponse:
    relative = difference.relative_reduction
    return CounterDifferenceResponse(
        counter=difference.baseline.counter,
        unit="microsecond",
        scope=difference.baseline.scope,
        reduction=difference.reduction_us,
        relative_reduction=None
        if relative is None
        else RelativeReductionResponse(numerator=relative[0], denominator=relative[1]),
    )
