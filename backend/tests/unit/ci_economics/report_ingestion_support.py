from __future__ import annotations

from dataclasses import dataclass, field

from ci_coordinator.app.ci_measurement_report_ingestion import MeasurementReportIngestionService
from ci_coordinator.ci_economics.discovery import ProviderRunDiscoveryPage, RunDiscoveryWindow
from ci_coordinator.ci_economics.ports import MeasurementReportWriteResult, ProviderAttemptDeferred
from ci_coordinator.ci_economics.report_ingestion import (
    ProviderReportJobBinding,
    measurement_report_audience,
)
from ci_coordinator.ci_economics.reports import (
    JobMeasurementReport,
    MeasurementReportOrigin,
    StoredMeasurementReport,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import TrustedActionsRun

from .factories import ATTEMPT, NOW
from .report_factories import measurement_report

REPORT = measurement_report()
SOURCE = ProviderRunCollectionSource(ATTEMPT, NOW, "2026-03-10", "e" * 64)
BINDING = ProviderReportJobBinding(ATTEMPT, "acme/service", 404, 405, "f" * 64)
IDENTITY = TrustedActionsRun(
    issuer="https://token.actions.githubusercontent.com",
    audience=measurement_report_audience("ci-coordinator"),
    repository="acme/service",
    repository_id=202,
    ref="refs/pull/42/merge",
    run_id=303,
    run_attempt=2,
    event_name="pull_request",
    workflow_ref="acme/service/.github/workflows/ci.yml@refs/pull/42/merge",
    workflow_sha="a" * 40,
    job_workflow_ref=None,
    job_workflow_sha=None,
    check_run_id="405",
    verified_at=NOW,
    execution_sha="c" * 40,
    claim_hash="a" * 64,
)


@dataclass
class IngestionBoundary:
    source: ProviderRunCollectionSource | ProviderAttemptDeferred | BaseException = SOURCE
    binding: ProviderReportJobBinding | ProviderAttemptDeferred | BaseException = BINDING
    result: MeasurementReportWriteResult | BaseException = "recorded"
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def resolve_attempt(
        self, scope: RepositoryScope, workflow_run_id: int, run_attempt: int
    ) -> ProviderRunCollectionSource | ProviderAttemptDeferred:
        self.calls.append(("resolve", (scope, workflow_run_id, run_attempt)))
        if isinstance(self.source, BaseException):
            raise self.source
        return self.source

    async def discover_page(
        self, scope: RepositoryScope, window: RunDiscoveryWindow, *, page_number: int
    ) -> ProviderRunDiscoveryPage | ProviderAttemptDeferred:
        raise AssertionError("report ingestion must not discover sources")

    async def bind_job(
        self, report: JobMeasurementReport, *, repository: str, page_number: int
    ) -> ProviderReportJobBinding | ProviderAttemptDeferred:
        self.calls.append(("bind", (report, repository, page_number)))
        if isinstance(self.binding, BaseException):
            raise self.binding
        return self.binding

    async def record_measurement_report(
        self,
        source: ProviderRunCollectionSource,
        report: JobMeasurementReport,
        origin: MeasurementReportOrigin,
    ) -> MeasurementReportWriteResult:
        self.calls.append(("write", (source, report, origin)))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def load_measurement_report(
        self, scope: RepositoryScope, report_id: str
    ) -> StoredMeasurementReport | None:
        raise AssertionError("report ingestion must not read retained reports")

    def service(
        self, *, allowed_scopes: frozenset[RepositoryScope] = frozenset({ATTEMPT.scope})
    ) -> MeasurementReportIngestionService:
        return MeasurementReportIngestionService(
            allowed_scopes=allowed_scopes, sources=self, jobs=self, store=self
        )
