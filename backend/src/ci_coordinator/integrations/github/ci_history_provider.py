from datetime import date, datetime

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.archive_detail import (
    MAX_HISTORY_DETAIL_BYTES,
    ArchivedAttemptDetail,
    ArchivedJobDetail,
    encode_archive_detail,
    validate_history_detail,
)
from ci_coordinator.ci_economics.archive_encoding import (
    MAX_HISTORY_HEADER_BYTES,
    MAX_HISTORY_STATISTICS_BYTES,
    encode_archive_header,
    encode_archive_job,
)
from ci_coordinator.ci_economics.archive_statistics import (
    ArchivedAttemptStatistics,
    ArchivedJobStatistics,
)
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_ports import (
    HistoryAttemptNotFound,
    HistoryAttemptObservation,
    HistoryAttemptResult,
)
from ci_coordinator.ci_economics.model import MAX_JOBS_PER_ATTEMPT
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.integrations.github._routes import (
    GitHubPage,
    GitHubRepository,
    workflow_run_attempt_jobs_path,
    workflow_run_attempt_path,
)
from ci_coordinator.integrations.github.actions_client import ActionsClient
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.ci_economics_admission import (
    bound_economics_response,
    load_economics_repository,
)
from ci_coordinator.integrations.github.ci_history_decoding import (
    HistoryAttemptHeader,
    decode_history_header,
    decode_history_job_page,
)
from ci_coordinator.integrations.github.ci_history_detail_decoding import (
    decode_history_detail_page,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubIncomplete,
    GitHubOutcome,
    GitHubQueryParameter,
    GitHubSuccess,
    GitHubUnavailable,
)
from ci_coordinator.integrations.github.reconciliation_observer_pagination import (
    JOBS_PAGE_SIZE,
    next_page_number,
    pagination_count_matches,
    terminal_pagination,
)
from ci_coordinator.integrations.github.transport import InstallationTransportFactory


class GitHubHistoryProvider:
    def __init__(
        self,
        transport_factory: InstallationTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
    ) -> None:
        if (
            type(api_version) is not str
            or date.fromisoformat(api_version).isoformat() != api_version
        ):
            raise ValueError("history API version must be an ISO calendar date")
        self._transport_factory = transport_factory
        self._api_version = api_version

    async def load_history_attempt(
        self, cursor: HistoryAttemptCursor, *, run_created_at: datetime
    ) -> HistoryAttemptResult:
        if type(cursor) is not HistoryAttemptCursor:
            raise TypeError("history provider requires an exact attempt cursor")
        run_created_at = utc_time(run_created_at)
        client = ActionsClient(
            self._transport_factory.for_installation(cursor.scope.installation_id),
            api_version=self._api_version,
        )
        repository = await load_economics_repository(
            client, cursor.scope, api_version=self._api_version
        )
        if isinstance(repository, ProviderAttemptDeferred):
            return repository
        first = await self._header(client, repository, cursor, run_created_at=run_created_at)
        if not isinstance(first, HistoryAttemptHeader):
            return first
        try:
            encode_archive_header(first.statistics)
        except ValueError:
            return ProviderAttemptDeferred("provider_malformed")
        population = await self._jobs(client, repository, cursor, first.statistics)
        if isinstance(population, ProviderAttemptDeferred):
            return population
        second = await self._header(client, repository, cursor, run_created_at=run_created_at)
        if isinstance(second, ProviderAttemptDeferred):
            return second
        if isinstance(second, HistoryAttemptNotFound) or second != first:
            return ProviderAttemptDeferred("provider_unstable")
        total, jobs, detail_jobs = population
        statistics = ArchivedAttemptStatistics.model_validate(
            {
                **first.statistics.model_dump(),
                "population": "unavailable"
                if total is None
                else "complete"
                if total == len(jobs)
                else "partial",
                "providerJobTotal": total,
                "jobs": jobs,
            }
        )
        if total == len(jobs) and detail_jobs is not None:
            try:
                detail = validate_history_detail(
                    statistics,
                    ArchivedAttemptDetail(
                        schemaVersion="ci-economics-archive-detail/v1",
                        attempt=statistics.attempt,
                        jobs=tuple(sorted(detail_jobs, key=lambda job: job.provider_job_id)),
                    ),
                )
                encode_archive_detail(detail)
            except (TypeError, ValueError):
                pass
            else:
                return HistoryAttemptObservation(statistics, detail)
        return statistics

    async def _header(
        self,
        client: ActionsClient,
        repository: GitHubRepository,
        cursor: HistoryAttemptCursor,
        *,
        run_created_at: datetime,
    ) -> HistoryAttemptHeader | HistoryAttemptNotFound | ProviderAttemptDeferred:
        outcome = await client.get_workflow_run_attempt(
            repository, cursor.workflow_run_id, cursor.next_attempt
        )
        path = workflow_run_attempt_path(repository, cursor.workflow_run_id, cursor.next_attempt)
        if _bound_not_found(
            outcome,
            operation="actions.get_workflow_run_attempt",
            path=path,
            api_version=self._api_version,
        ):
            return HistoryAttemptNotFound(cursor)
        body = bound_economics_response(
            outcome,
            operation="actions.get_workflow_run_attempt",
            path=path,
            api_version=self._api_version,
        )
        return (
            body
            if isinstance(body, ProviderAttemptDeferred)
            else decode_history_header(body, cursor, run_created_at=run_created_at)
        )

    async def _jobs(
        self,
        client: ActionsClient,
        repository: GitHubRepository,
        cursor: HistoryAttemptCursor,
        header: ArchivedAttemptStatistics,
    ) -> (
        tuple[int | None, tuple[ArchivedJobStatistics, ...], tuple[ArchivedJobDetail, ...] | None]
        | ProviderAttemptDeferred
    ):
        seen_ids: set[int] = set()
        jobs: dict[int, ArchivedJobStatistics] = {}
        detail_jobs: list[ArchivedJobDetail] = []
        detail_available = True
        detail_candidate_seen = False
        try:
            envelope = encode_archive_detail(
                ArchivedAttemptDetail(
                    schemaVersion="ci-economics-archive-detail/v1", attempt=header.attempt, jobs=()
                )
            )
        except (TypeError, ValueError):
            detail_available = False
            detail_remaining_bytes = 0
        else:
            detail_remaining_bytes = MAX_HISTORY_DETAIL_BYTES - len(envelope) + 1
        retained_bytes = MAX_HISTORY_HEADER_BYTES
        total: int | None = None
        path = workflow_run_attempt_jobs_path(
            repository, cursor.workflow_run_id, cursor.next_attempt
        )
        for number in range(1, MAX_JOBS_PER_ATTEMPT // JOBS_PAGE_SIZE + 1):
            page = GitHubPage(number, JOBS_PAGE_SIZE)
            outcome = await client.list_workflow_run_attempt_jobs(
                repository, cursor.workflow_run_id, cursor.next_attempt, page=page
            )
            if _bound_not_found(
                outcome,
                operation="actions.list_workflow_run_attempt_jobs",
                path=path,
                api_version=self._api_version,
                query=page.query(),
            ):
                return total, tuple(jobs[key] for key in sorted(jobs)), None
            body = bound_economics_response(
                outcome,
                operation="actions.list_workflow_run_attempt_jobs",
                path=path,
                api_version=self._api_version,
                query=page.query(),
                paginated=True,
            )
            if isinstance(body, ProviderAttemptDeferred):
                return body
            if not isinstance(outcome, (GitHubSuccess, GitHubIncomplete)):
                return ProviderAttemptDeferred("provider_unavailable")
            decoded = decode_history_job_page(body, cursor, head_sha=header.attempt.head_sha)
            if isinstance(decoded, ProviderAttemptDeferred):
                return decoded
            if detail_available:
                decoded_detail = decode_history_detail_page(
                    body,
                    cursor,
                    head_sha=header.attempt.head_sha,
                    remaining_bytes=detail_remaining_bytes,
                )
                if decoded_detail is None:
                    detail_available = False
                    detail_jobs.clear()
                else:
                    detail_remaining_bytes -= decoded_detail.consumed_bytes
                    detail_candidate_seen = detail_candidate_seen or bool(decoded.terminal_jobs)
                    detail_jobs.extend(decoded_detail.jobs)
                del decoded_detail
            if total is not None and total != decoded.total:
                return ProviderAttemptDeferred("provider_unstable")
            total = decoded.total
            page_ids = set(decoded.observed_ids)
            if len(page_ids) != len(decoded.observed_ids) or page_ids.intersection(seen_ids):
                return ProviderAttemptDeferred("provider_unstable")
            pagination = outcome.response.pagination
            observed_count = len(seen_ids) + len(page_ids)
            if not pagination_count_matches(pagination, total) or observed_count > total:
                return ProviderAttemptDeferred("provider_malformed")
            terminal = isinstance(outcome, GitHubSuccess) and terminal_pagination(pagination)
            if terminal:
                if observed_count != total:
                    return ProviderAttemptDeferred("provider_incomplete")
            elif (
                not isinstance(outcome, GitHubIncomplete)
                or next_page_number(
                    pagination,
                    expected_path=path,
                    current_page=number,
                    repository_id=cursor.scope.repository_id,
                )
                is None
            ):
                return ProviderAttemptDeferred("provider_incomplete")
            elif len(page_ids) != JOBS_PAGE_SIZE or observed_count >= total:
                return ProviderAttemptDeferred("provider_malformed")
            seen_ids.update(page_ids)
            for job in decoded.terminal_jobs:
                try:
                    size = len(encode_archive_job(job))
                except ValueError:
                    return ProviderAttemptDeferred("provider_malformed")
                if retained_bytes + size > MAX_HISTORY_STATISTICS_BYTES:
                    return total, tuple(jobs[key] for key in sorted(jobs)), None
                retained_bytes += size
                jobs[job.provider_job_id] = job
            if terminal:
                return (
                    total,
                    tuple(jobs[key] for key in sorted(jobs)),
                    tuple(detail_jobs) if detail_available and detail_candidate_seen else None,
                )
        return (
            total,
            tuple(jobs[key] for key in sorted(jobs)),
            tuple(detail_jobs) if detail_available and detail_candidate_seen else None,
        )


def _bound_not_found(
    outcome: GitHubOutcome,
    *,
    operation: str,
    path: str,
    api_version: str,
    query: tuple[GitHubQueryParameter, ...] = (),
) -> bool:
    if not isinstance(outcome, GitHubUnavailable) or outcome.failure.kind != "not_found":
        return False
    request, response = outcome.failure.request, outcome.failure.response
    return (
        request.method == "GET"
        and request.operation == operation
        and request.path == path
        and request.query == query
        and request.body is None
        and request.api_version == api_version
        and response is not None
        and response.api_version == api_version
        and response.status == 404
        and type(response.body) is bytes
        and len(response.body) <= 1_048_576
        and terminal_pagination(response.pagination)
    )
