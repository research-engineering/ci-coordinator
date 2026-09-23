"""Exact, bounded GitHub Actions observations for reconciliation subjects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NoReturn, cast

from ci_coordinator.ci_economics.model import AttemptIdentity, WorkflowConclusion
from ci_coordinator.execution_orchestration import ProviderOccurrence, ProviderSignal
from ci_coordinator.integrations.github._routes import (
    GitHubPage,
    GitHubRepository,
    repository_id_path,
    workflow_run_attempt_jobs_path,
)
from ci_coordinator.integrations.github.actions_client import ActionsClient
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubIncomplete,
    GitHubSuccess,
)
from ci_coordinator.integrations.github.reconciliation_observer_decoding import (
    ProviderJob,
    decode_job_page,
    decode_repository,
)
from ci_coordinator.integrations.github.reconciliation_observer_pagination import (
    JOBS_PAGE_SIZE,
    next_page_number,
    pagination_count_matches,
    terminal_pagination,
)
from ci_coordinator.integrations.github.request_admission import (
    get_request_matches as _request_matches,
)
from ci_coordinator.integrations.github.transport import InstallationTransportFactory
from ci_coordinator.kernel import hash_object
from ci_coordinator.reconciliation.observation import SignalConclusion, SignalObservation
from ci_coordinator.reconciliation.poll_outcome import (
    ProviderSignalAmbiguity,
    ReconciliationPollOutcome,
)
from ci_coordinator.reconciliation.ports import ReconciliationPollUnavailable
from ci_coordinator.reconciliation.subject import ReconciliationSubject


class ObservationFailureReason(StrEnum):
    AMBIGUOUS_CONTRACT = "ambiguous_contract"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_BINDING_MISMATCH = "provider_binding_mismatch"
    REPOSITORY_MALFORMED = "repository_malformed"
    REPOSITORY_MISMATCH = "repository_mismatch"
    JOBS_MALFORMED = "jobs_malformed"
    JOBS_TRUNCATED = "jobs_truncated"


class GitHubReconciliationObservationError(ReconciliationPollUnavailable):
    """A redacted fail-closed outcome that carries only a stable reason code."""

    def __init__(self, reason: ObservationFailureReason) -> None:
        self.reason = reason
        super().__init__("GitHub reconciliation observation is unavailable")


@dataclass(frozen=True, slots=True)
class ReconciliationObserverLimits:
    max_pages: int = 20
    max_jobs: int = 2_000
    max_repository_response_bytes: int = 1_048_576
    max_total_response_bytes: int = 8_388_608

    def __post_init__(self) -> None:
        values = (
            self.max_pages,
            self.max_jobs,
            self.max_repository_response_bytes,
            self.max_total_response_bytes,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("reconciliation observer limits must be positive integers")
        if self.max_repository_response_bytes > self.max_total_response_bytes:
            raise ValueError("repository response limit must not exceed the total response limit")


DEFAULT_RECONCILIATION_OBSERVER_LIMITS: Final = ReconciliationObserverLimits()


class GitHubActionsReconciliationObserver:
    """Poll only the provider run attempt named by an immutable subject."""

    def __init__(
        self,
        transport_factory: InstallationTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
        limits: ReconciliationObserverLimits = DEFAULT_RECONCILIATION_OBSERVER_LIMITS,
    ) -> None:
        if type(api_version) is not str or not api_version:
            raise ValueError("GitHub API version provenance must be non-empty")
        if type(limits) is not ReconciliationObserverLimits:
            raise TypeError("reconciliation observer requires exact limits")
        self._transport_factory = transport_factory
        self._api_version = api_version
        self._limits = limits

    async def poll(
        self,
        subject: ReconciliationSubject,
        provider_signals: tuple[ProviderSignal, ...],
    ) -> ReconciliationPollOutcome:
        _signal_index(provider_signals)
        jobs = await self.load_attempt(subject)
        return self.project(subject, provider_signals, jobs)

    async def load_attempt(
        self,
        subject: ReconciliationSubject,
    ) -> tuple[ProviderJob, ...]:
        """Load one complete bounded provider view for an exact run attempt."""
        if type(subject) is not ReconciliationSubject:
            raise TypeError("reconciliation observer requires an exact subject")
        transport = self._transport_factory.for_installation(subject.installation_id)
        client = ActionsClient(transport, api_version=self._api_version)
        repository, consumed_bytes = await self._resolve_repository(client, subject.repository_id)
        return await self._load_jobs(client, subject, repository, consumed_bytes)

    async def load_attempt_identity(self, attempt: AttemptIdentity) -> tuple[ProviderJob, ...]:
        if type(attempt) is not AttemptIdentity:
            raise TypeError("GitHub attempt reader requires an exact attempt identity")
        transport = self._transport_factory.for_installation(attempt.scope.installation_id)
        client = ActionsClient(transport, api_version=self._api_version)
        repository, consumed_bytes = await self._resolve_repository(
            client, attempt.scope.repository_id
        )
        return await self._load_jobs(client, attempt, repository, consumed_bytes)

    def project(
        self,
        subject: ReconciliationSubject,
        provider_signals: tuple[ProviderSignal, ...],
        jobs: tuple[ProviderJob, ...],
    ) -> ReconciliationPollOutcome:
        """Project already-bound provider jobs into reconciliation observations."""
        if type(subject) is not ReconciliationSubject:
            raise TypeError("reconciliation observer requires an exact subject")
        if type(jobs) is not tuple or any(type(job) is not ProviderJob for job in jobs):
            raise TypeError("reconciliation projection requires exact provider jobs")
        if any(job.head_sha != subject.head_sha for job in jobs):
            _fail(ObservationFailureReason.PROVIDER_BINDING_MISMATCH)
        signal_by_name = _signal_index(provider_signals)

        observations: list[SignalObservation] = []
        jobs_by_name: dict[str, list[ProviderJob]] = {}
        for job in jobs:
            if job.name in signal_by_name:
                jobs_by_name.setdefault(job.name, []).append(job)
        for signal in provider_signals:
            matches = jobs_by_name.get(signal.job_name, [])
            if len(matches) > 1:
                return ProviderSignalAmbiguity(subject.subject_id, signal)
            if matches:
                occurrence = ProviderOccurrence(
                    signal=signal,
                    workflow_run_id=subject.workflow_run_id,
                    run_attempt=subject.run_attempt,
                    job_id=matches[0].job_id,
                )
                observations.append(_observation(subject, occurrence, matches[0]))
        return tuple(sorted(observations, key=lambda observation: observation.signal_id))

    async def _resolve_repository(
        self,
        client: ActionsClient,
        repository_id: int,
    ) -> tuple[GitHubRepository, int]:
        outcome = await client.get_repository_by_id(repository_id)
        expected_path = repository_id_path(repository_id)
        if not isinstance(outcome, GitHubSuccess):
            _fail(ObservationFailureReason.PROVIDER_UNAVAILABLE)
        if not _request_matches(
            outcome.request,
            operation="repositories.get_by_id",
            path=expected_path,
            query=(),
            api_version=self._api_version,
        ):
            _fail(ObservationFailureReason.PROVIDER_BINDING_MISMATCH)
        if not terminal_pagination(outcome.response.pagination):
            _fail(ObservationFailureReason.REPOSITORY_MALFORMED)

        body = outcome.response.body
        if (
            type(body) is not bytes
            or len(body) > self._limits.max_repository_response_bytes
            or len(body) > self._limits.max_total_response_bytes
        ):
            _fail(ObservationFailureReason.REPOSITORY_MALFORMED)
        repository = decode_repository(body)
        if repository is None:
            _fail(ObservationFailureReason.REPOSITORY_MALFORMED)
        if repository[0] != repository_id:
            _fail(ObservationFailureReason.REPOSITORY_MISMATCH)
        return repository[1], len(body)

    async def _load_jobs(
        self,
        client: ActionsClient,
        attempt: ReconciliationSubject | AttemptIdentity,
        repository: GitHubRepository,
        consumed_bytes: int,
    ) -> tuple[ProviderJob, ...]:
        expected_path = workflow_run_attempt_jobs_path(
            repository,
            attempt.workflow_run_id,
            attempt.run_attempt,
        )
        page_number = 1
        pages_observed = 0
        expected_total: int | None = None
        jobs: list[ProviderJob] = []
        seen_job_ids: set[int] = set()

        while True:
            page = GitHubPage(page_number, JOBS_PAGE_SIZE)
            outcome = await client.list_workflow_run_attempt_jobs(
                repository,
                attempt.workflow_run_id,
                attempt.run_attempt,
                page=page,
            )
            pages_observed += 1
            if not isinstance(outcome, (GitHubSuccess, GitHubIncomplete)):
                _fail(ObservationFailureReason.PROVIDER_UNAVAILABLE)
            if not _request_matches(
                outcome.request,
                operation="actions.list_workflow_run_attempt_jobs",
                path=expected_path,
                query=page.query(),
                api_version=self._api_version,
            ):
                _fail(ObservationFailureReason.PROVIDER_BINDING_MISMATCH)

            body = outcome.response.body
            if type(body) is not bytes or len(body) > self._limits.max_total_response_bytes:
                _fail(ObservationFailureReason.JOBS_TRUNCATED)
            if consumed_bytes > self._limits.max_total_response_bytes - len(body):
                _fail(ObservationFailureReason.JOBS_TRUNCATED)
            consumed_bytes += len(body)

            decoded = decode_job_page(
                body,
                expected_run_id=attempt.workflow_run_id,
                expected_head_sha=attempt.head_sha,
            )
            if decoded is None:
                _fail(ObservationFailureReason.JOBS_MALFORMED)
            if decoded.total_count > self._limits.max_jobs:
                _fail(ObservationFailureReason.JOBS_TRUNCATED)
            if expected_total is None:
                expected_total = decoded.total_count
            elif decoded.total_count != expected_total:
                _fail(ObservationFailureReason.JOBS_MALFORMED)

            for job in decoded.jobs:
                if job.job_id in seen_job_ids:
                    _fail(ObservationFailureReason.JOBS_MALFORMED)
                seen_job_ids.add(job.job_id)
                jobs.append(job)
            if len(jobs) > expected_total:
                _fail(ObservationFailureReason.JOBS_MALFORMED)
            if not pagination_count_matches(outcome.response.pagination, expected_total):
                _fail(ObservationFailureReason.JOBS_MALFORMED)

            if isinstance(outcome, GitHubSuccess):
                if not terminal_pagination(outcome.response.pagination):
                    _fail(ObservationFailureReason.JOBS_MALFORMED)
                if len(jobs) != expected_total:
                    _fail(ObservationFailureReason.JOBS_TRUNCATED)
                return tuple(jobs)

            if pages_observed >= self._limits.max_pages or len(jobs) >= expected_total:
                _fail(ObservationFailureReason.JOBS_TRUNCATED)
            next_page = next_page_number(
                outcome.response.pagination,
                expected_path=expected_path,
                current_page=page_number,
                repository_id=attempt.repository_id
                if isinstance(attempt, ReconciliationSubject)
                else attempt.scope.repository_id,
            )
            if next_page is None:
                _fail(ObservationFailureReason.JOBS_TRUNCATED)
            page_number = next_page


def _signal_index(
    provider_signals: tuple[ProviderSignal, ...],
) -> dict[str, ProviderSignal]:
    if type(provider_signals) is not tuple or any(
        type(signal) is not ProviderSignal for signal in provider_signals
    ):
        raise TypeError("provider signals must be exact ProviderSignal values")
    names = tuple(signal.job_name for signal in provider_signals)
    ids = tuple(signal.signal_id for signal in provider_signals)
    if len(names) != len(set(names)) or len(ids) != len(set(ids)):
        _fail(ObservationFailureReason.AMBIGUOUS_CONTRACT)
    return {signal.job_name: signal for signal in provider_signals}


def _observation(
    subject: ReconciliationSubject,
    occurrence: ProviderOccurrence,
    job: ProviderJob,
) -> SignalObservation:
    if (
        not occurrence.belongs_to(
            workflow_run_id=subject.workflow_run_id,
            run_attempt=subject.run_attempt,
        )
        or occurrence.job_id != job.job_id
    ):
        raise ValueError("provider occurrence belongs to another reconciliation subject")
    identity = {
        "provider": "github",
        "subjectId": subject.subject_id,
        "repositoryId": subject.repository_id,
        "workflowRunId": subject.workflow_run_id,
        "runAttempt": subject.run_attempt,
        "providerJobId": job.job_id,
        "status": job.status,
        "conclusion": job.conclusion,
    }
    return SignalObservation(
        observation_id="github_job_" + hash_object(identity),
        subject_id=subject.subject_id,
        signal_id=occurrence.signal.signal_id,
        workflow_run_id=occurrence.workflow_run_id,
        run_attempt=occurrence.run_attempt,
        provider_job_id=occurrence.job_id,
        status=job.status,
        conclusion=_signal_conclusion(job.conclusion),
    )


def _signal_conclusion(value: WorkflowConclusion | None) -> SignalConclusion | None:
    if value in {"action_required", "startup_failure", "stale"}:
        return "unknown"
    return cast(SignalConclusion | None, value)


def _fail(reason: ObservationFailureReason) -> NoReturn:
    raise GitHubReconciliationObservationError(reason) from None
