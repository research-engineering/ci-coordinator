from __future__ import annotations

import re
from typing import Protocol

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadNotFound,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.ci_economics.budget import EvaluatedReportBudget, ReportBudget
from ci_coordinator.ci_economics.catalog import (
    MeasurementReportPage,
    require_catalog_digest,
    require_catalog_page,
)
from ci_coordinator.ci_economics.comparison import RetainedReportPair
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable, MeasurementReportQuery
from ci_coordinator.ci_economics.reports import StoredMeasurementReport
from ci_coordinator.config_control import RepositoryScope

type MeasurementReportReadResult = (
    StoredMeasurementReport
    | CiEconomicsReadForbidden
    | CiEconomicsReadNotFound
    | CiEconomicsReadUnavailable
)
type MeasurementReportPageResult = (
    MeasurementReportPage
    | CiEconomicsReadForbidden
    | CiEconomicsReadNotFound
    | CiEconomicsReadUnavailable
)
type ReportComparisonReadResult = (
    RetainedReportPair
    | CiEconomicsReadForbidden
    | CiEconomicsReadNotFound
    | CiEconomicsReadUnavailable
)
type ReportBudgetReadResult = (
    EvaluatedReportBudget
    | CiEconomicsReadForbidden
    | CiEconomicsReadNotFound
    | CiEconomicsReadUnavailable
)
_REPORT_ID = re.compile(r"[0-9a-f]{64}")


class MeasurementReportReadUseCase(Protocol):
    async def list_reports(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        source_id: str,
        after_cursor: str | None,
        limit: int,
    ) -> MeasurementReportPageResult: ...

    async def evaluate_budget(
        self, *, actor: str, scope: RepositoryScope, report_id: str, budget: ReportBudget
    ) -> ReportBudgetReadResult: ...

    async def load_report(
        self, *, actor: str, scope: RepositoryScope, report_id: str
    ) -> MeasurementReportReadResult: ...

    async def compare_reports(
        self, *, actor: str, scope: RepositoryScope, baseline_id: str, treatment_id: str
    ) -> ReportComparisonReadResult: ...


class MeasurementReportReadService:
    def __init__(self, *, authorizer: CiEconomicsAuthorizer, query: MeasurementReportQuery) -> None:
        self._authorizer = authorizer
        self._query = query

    async def list_reports(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        source_id: str,
        after_cursor: str | None,
        limit: int,
    ) -> MeasurementReportPageResult:
        require_catalog_page(scope, limit)
        require_catalog_digest(source_id)
        if after_cursor is not None:
            require_catalog_digest(after_cursor)
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return CiEconomicsReadForbidden()
        try:
            page = await self._query.list_measurement_reports(
                scope, source_id, after_cursor=after_cursor, limit=limit
            )
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if page is None:
            return CiEconomicsReadNotFound()
        if (
            type(page) is not MeasurementReportPage
            or page.source.attempt.scope != scope
            or page.source.source_id != source_id
            or len(page.items) > limit
            or (
                after_cursor is not None
                and any(item.report_id <= after_cursor for item in page.items)
            )
        ):
            return CiEconomicsReadUnavailable()
        return page

    async def evaluate_budget(
        self, *, actor: str, scope: RepositoryScope, report_id: str, budget: ReportBudget
    ) -> ReportBudgetReadResult:
        if type(budget) is not ReportBudget:
            raise TypeError("budget read requires an exact threshold")
        result = await self.load_report(actor=actor, scope=scope, report_id=report_id)
        return (
            EvaluatedReportBudget(result, budget)
            if isinstance(result, StoredMeasurementReport)
            else result
        )

    async def load_report(
        self, *, actor: str, scope: RepositoryScope, report_id: str
    ) -> MeasurementReportReadResult:
        _require_identity(scope, report_id)
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return CiEconomicsReadForbidden()
        return await self._load(scope, report_id)

    async def compare_reports(
        self, *, actor: str, scope: RepositoryScope, baseline_id: str, treatment_id: str
    ) -> ReportComparisonReadResult:
        _require_identity(scope, baseline_id)
        _require_identity(scope, treatment_id)
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return CiEconomicsReadForbidden()
        baseline = await self._load(scope, baseline_id)
        if not isinstance(baseline, StoredMeasurementReport):
            return baseline
        treatment = (
            baseline if treatment_id == baseline_id else await self._load(scope, treatment_id)
        )
        if not isinstance(treatment, StoredMeasurementReport):
            return treatment
        return RetainedReportPair(baseline, treatment)

    async def _load(
        self, scope: RepositoryScope, report_id: str
    ) -> StoredMeasurementReport | CiEconomicsReadNotFound | CiEconomicsReadUnavailable:
        try:
            report = await self._query.load_measurement_report(scope, report_id)
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if report is None:
            return CiEconomicsReadNotFound()
        if (
            type(report) is not StoredMeasurementReport
            or report.report.attempt.scope != scope
            or report.report.report_id != report_id
        ):
            return CiEconomicsReadUnavailable()
        return report


def _require_identity(scope: RepositoryScope, report_id: str) -> None:
    if type(scope) is not RepositoryScope:
        raise TypeError("report read requires an exact repository scope")
    if type(report_id) is not str or _REPORT_ID.fullmatch(report_id) is None:
        raise ValueError("report read requires an exact SHA-256 report identity")
