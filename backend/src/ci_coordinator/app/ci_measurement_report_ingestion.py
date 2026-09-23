from __future__ import annotations

import re
from typing import Literal, Protocol

from ci_coordinator.ci_economics.ports import (
    CiEconomicsStoreUnavailable,
    MeasurementReportJobProvider,
    MeasurementReportStore,
    MeasurementReportWriteResult,
    ProviderSourceResolver,
)
from ci_coordinator.ci_economics.report_ingestion import (
    MAX_REPORT_JOB_PAGES,
    ProviderReportJobBinding,
)
from ci_coordinator.ci_economics.reports import JobMeasurementReport, MeasurementReportOrigin
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import TrustedActionsRun

type MeasurementReportIngestionResult = (
    MeasurementReportWriteResult | Literal["forbidden", "unavailable"]
)


class MeasurementReportIngestionUseCase(Protocol):
    async def record_report(
        self, report: JobMeasurementReport, identity: TrustedActionsRun, *, page_number: int
    ) -> MeasurementReportIngestionResult: ...


class MeasurementReportIngestionService:
    def __init__(
        self,
        *,
        allowed_scopes: frozenset[RepositoryScope],
        sources: ProviderSourceResolver,
        jobs: MeasurementReportJobProvider,
        store: MeasurementReportStore,
    ) -> None:
        if type(allowed_scopes) is not frozenset or any(
            type(scope) is not RepositoryScope for scope in allowed_scopes
        ):
            raise ValueError("report ingestion requires exact admitted repository scopes")
        self._scopes = allowed_scopes
        self._sources = sources
        self._jobs = jobs
        self._store = store

    async def record_report(
        self, report: JobMeasurementReport, identity: TrustedActionsRun, *, page_number: int
    ) -> MeasurementReportIngestionResult:
        if type(report) is not JobMeasurementReport or type(identity) is not TrustedActionsRun:
            raise TypeError("report ingestion requires exact report and authenticated identity")
        if type(page_number) is not int or not 1 <= page_number <= MAX_REPORT_JOB_PAGES:
            raise ValueError("report job page is outside the bounded membership lookup")
        attempt = report.attempt
        if (
            attempt.scope not in self._scopes
            or identity.repository_id != attempt.scope.repository_id
            or identity.run_id != attempt.workflow_run_id
            or identity.run_attempt != attempt.run_attempt
            or identity.check_run_id != str(report.check_run_id)
            or type(identity.claim_hash) is not str
            or re.fullmatch(r"[0-9a-f]{64}", identity.claim_hash) is None
        ):
            return "forbidden"
        source = await self._sources.resolve_attempt(
            attempt.scope, attempt.workflow_run_id, attempt.run_attempt
        )
        if type(source) is not ProviderRunCollectionSource or source.attempt != attempt:
            return "unavailable"
        binding = await self._jobs.bind_job(
            report, repository=identity.repository, page_number=page_number
        )
        if (
            type(binding) is not ProviderReportJobBinding
            or binding.attempt != attempt
            or binding.repository != identity.repository
            or binding.provider_job_id != report.provider_job_id
            or binding.check_run_id != report.check_run_id
        ):
            return "unavailable"
        try:
            return await self._store.record_measurement_report(
                source,
                report,
                MeasurementReportOrigin(identity.claim_hash, binding.evidence_digest),
            )
        except CiEconomicsStoreUnavailable:
            return "unavailable"
