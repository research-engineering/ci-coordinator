from __future__ import annotations

from datetime import date

from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.ci_economics.report_ingestion import (
    MAX_REPORT_JOB_PAGES,
    REPORT_JOB_PAGE_SIZE,
    ProviderReportJobBinding,
)
from ci_coordinator.ci_economics.reports import JobMeasurementReport
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    non_negative_safe_integer,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github._routes import GitHubPage, workflow_run_attempt_jobs_path
from ci_coordinator.integrations.github.actions_client import ActionsClient
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.ci_economics_admission import (
    bound_economics_response,
    load_economics_repository,
)
from ci_coordinator.integrations.github.transport import InstallationTransportFactory
from ci_coordinator.kernel import hash_object, sha256_hex


class GitHubMeasurementReportJobs:
    def __init__(
        self,
        transport_factory: InstallationTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
    ) -> None:
        if (
            type(api_version) is not str
            or len(api_version) != 10
            or date.fromisoformat(api_version).isoformat() != api_version
        ):
            raise ValueError("report binding API version must be an ISO calendar date")
        self._transport_factory = transport_factory
        self._api_version = api_version

    async def bind_job(
        self, report: JobMeasurementReport, *, repository: str, page_number: int
    ) -> ProviderReportJobBinding | ProviderAttemptDeferred:
        if type(report) is not JobMeasurementReport or type(repository) is not str:
            raise TypeError("report binding requires an exact report and repository name")
        if type(page_number) is not int or not 1 <= page_number <= MAX_REPORT_JOB_PAGES:
            raise ValueError("report job page exceeds its bound")
        attempt = report.attempt
        client = ActionsClient(
            self._transport_factory.for_installation(attempt.scope.installation_id),
            api_version=self._api_version,
        )
        resolved = await load_economics_repository(
            client, attempt.scope, api_version=self._api_version
        )
        if isinstance(resolved, ProviderAttemptDeferred):
            return resolved
        if f"{resolved.owner}/{resolved.name}" != repository:
            return ProviderAttemptDeferred("provider_binding_mismatch")
        page = GitHubPage(page_number, REPORT_JOB_PAGE_SIZE)
        path = workflow_run_attempt_jobs_path(
            resolved, attempt.workflow_run_id, attempt.run_attempt
        )
        body = bound_economics_response(
            await client.list_workflow_run_attempt_jobs(
                resolved, attempt.workflow_run_id, attempt.run_attempt, page=page
            ),
            operation="actions.list_workflow_run_attempt_jobs",
            path=path,
            api_version=self._api_version,
            query=page.query(),
            paginated=True,
        )
        if isinstance(body, ProviderAttemptDeferred):
            return body
        check_run_url = f"https://api.github.com{resolved.path}/check-runs/{report.check_run_id}"
        job = _page_member(body, report.provider_job_id, page_number, check_run_url)
        if isinstance(job, ProviderAttemptDeferred):
            return job
        if (
            positive_safe_integer(job.get("run_id")) != attempt.workflow_run_id
            or job.get("head_sha") != attempt.head_sha
            or job.get("check_run_url") != check_run_url
        ):
            return ProviderAttemptDeferred("provider_binding_mismatch")
        evidence_digest = hash_object(
            {
                "schemaVersion": "ci-economics-report-job-binding/v1",
                "attempt": attempt.canonical_mapping(),
                "repository": repository,
                "providerJobId": report.provider_job_id,
                "checkRunId": report.check_run_id,
                "apiVersion": self._api_version,
                "path": path,
                "page": page_number,
                "pageSize": REPORT_JOB_PAGE_SIZE,
                "responseSha256": sha256_hex(body),
            }
        )
        return ProviderReportJobBinding(
            attempt, repository, report.provider_job_id, report.check_run_id, evidence_digest
        )


def _page_member(
    body: bytes, provider_job_id: int, page_number: int, check_run_url: str
) -> dict[str, object] | ProviderAttemptDeferred:
    value = json_object_or_none(body)
    if value is None:
        return ProviderAttemptDeferred("provider_malformed")
    total = non_negative_safe_integer(value.get("total_count"))
    jobs = value.get("jobs")
    if (
        total is None
        or type(jobs) is not list
        or len(jobs) > REPORT_JOB_PAGE_SIZE
        or (page_number - 1) * REPORT_JOB_PAGE_SIZE + len(jobs) > total
    ):
        return ProviderAttemptDeferred("provider_malformed")
    seen: set[int] = set()
    check_run_seen = False
    selected: dict[str, object] | None = None
    for raw in jobs:
        job = object_or_none(raw)
        job_id = positive_safe_integer(job.get("id")) if job is not None else None
        if job is None or job_id is None or job_id in seen:
            return ProviderAttemptDeferred("provider_malformed")
        seen.add(job_id)
        if job.get("check_run_url") == check_run_url:
            if check_run_seen:
                return ProviderAttemptDeferred("provider_binding_mismatch")
            check_run_seen = True
        if job_id == provider_job_id:
            selected = job
    return selected if selected is not None else ProviderAttemptDeferred("provider_incomplete")
