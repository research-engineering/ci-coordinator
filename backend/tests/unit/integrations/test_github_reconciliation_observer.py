from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import pytest

from ci_coordinator.ci_economics import ProviderAttemptSnapshot
from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.integrations.github import reconciliation_observer
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.ci_economics_provider import GitHubCiEconomicsProvider
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubRequest,
    GitHubResponse,
    GitHubTransportFailure,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.reconciliation_observer import (
    GitHubActionsReconciliationObserver,
    GitHubReconciliationObservationError,
    ObservationFailureReason,
    ReconciliationObserverLimits,
)
from ci_coordinator.integrations.github.reconciliation_observer_pagination import next_page_number
from ci_coordinator.kernel import hash_object
from ci_coordinator.reconciliation import (
    ProviderSignalAmbiguity,
    ReconciliationSubject,
)

REPOSITORY_ID = 202
WORKFLOW_RUN_ID = 303
RUN_ATTEMPT = 2
JOBS_PATH = "/repos/acme/service/actions/runs/303/attempts/2/jobs"
SUBJECT = ReconciliationSubject.create(
    installation_id=101,
    repository_id=REPOSITORY_ID,
    event_name="push",
    ref="refs/heads/main",
    base_sha="a" * 40,
    head_sha="b" * 40,
    workflow_run_id=WORKFLOW_RUN_ID,
    run_attempt=RUN_ATTEMPT,
)
SIGNAL = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)
OTHER_SIGNAL = ProviderSignal.derive(
    execution_profile_id="container",
    shard_id="ci_shard_fedcba9876543210fedcba9876543210",
)
SIGNALS = (SIGNAL,)
TWO_SIGNALS = tuple(sorted((SIGNAL, OTHER_SIGNAL), key=lambda item: item.signal_id))


@dataclass
class _Transport:
    handler: Callable[[GitHubRequest], GitHubTransportResult | BaseException]
    requests: list[GitHubRequest] = field(default_factory=list)

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        self.requests.append(request)
        result = self.handler(request)
        if isinstance(result, BaseException):
            raise result
        return result


@dataclass
class _Factory:
    transport: _Transport
    installations: list[int] = field(default_factory=list)

    def for_installation(self, installation_id: int) -> _Transport:
        self.installations.append(installation_id)
        return self.transport


def test_exact_subject_binding_produces_only_exact_contract_observations() -> None:
    jobs = [
        _job(1, SIGNAL.job_name, "completed", "success"),
        _job(2, "Configured omitted obligation", "queued", None),
        _job(3, "Unrelated", "completed", "failure"),
    ]
    transport = _Transport(_complete_handler(jobs))
    factory = _Factory(transport)

    observations = asyncio.run(_observer(factory).poll(SUBJECT, SIGNALS))

    assert not isinstance(observations, ProviderSignalAmbiguity)
    assert factory.installations == [SUBJECT.installation_id]
    assert [request.path for request in transport.requests] == [
        f"/repositories/{REPOSITORY_ID}",
        JOBS_PATH,
    ]
    assert [(item.name, item.value) for item in transport.requests[1].query] == [
        ("page", "1"),
        ("per_page", "100"),
    ]
    assert [observation.signal_id for observation in observations] == [SIGNAL.signal_id]
    assert observations[0].status == "completed"
    assert observations[0].conclusion == "success"
    assert all(observation.workflow_run_id == WORKFLOW_RUN_ID for observation in observations)
    assert all(observation.run_attempt == RUN_ATTEMPT for observation in observations)
    assert observations[0].provider_job_id == 1
    assert observations[0].observation_id == "github_job_" + hash_object(
        {
            "provider": "github",
            "subjectId": SUBJECT.subject_id,
            "repositoryId": REPOSITORY_ID,
            "workflowRunId": WORKFLOW_RUN_ID,
            "runAttempt": RUN_ATTEMPT,
            "providerJobId": 1,
            "status": "completed",
            "conclusion": "success",
        }
    )


def test_attempt_entrypoint_retains_exact_provider_binding_without_reconciliation() -> None:
    jobs = [_job(1, SIGNAL.job_name, "completed", "success")]
    transport = _Transport(_complete_handler(jobs))
    factory = _Factory(transport)
    attempt = AttemptIdentity(
        RepositoryScope(101, REPOSITORY_ID), WORKFLOW_RUN_ID, RUN_ATTEMPT, SUBJECT.head_sha
    )

    observed = asyncio.run(_observer(factory).load_attempt_identity(attempt))

    assert [job.job_id for job in observed] == [1]
    assert observed[0].head_sha == attempt.head_sha
    assert factory.installations == [101]
    assert [request.path for request in transport.requests] == [
        f"/repositories/{REPOSITORY_ID}",
        JOBS_PATH,
    ]


@pytest.mark.parametrize("length", [40, 41, 64])
def test_legacy_subject_reader_keeps_its_existing_sha_domain(length: int) -> None:
    subject = ReconciliationSubject.create(
        installation_id=SUBJECT.installation_id,
        repository_id=SUBJECT.repository_id,
        event_name=SUBJECT.event_name,
        ref=SUBJECT.ref,
        base_sha=SUBJECT.base_sha,
        head_sha="b" * length,
        workflow_run_id=SUBJECT.workflow_run_id,
        run_attempt=SUBJECT.run_attempt,
    )
    transport = _Transport(_complete_handler([]))
    factory = _Factory(transport)

    assert asyncio.run(_observer(factory).load_attempt(subject)) == ()
    assert factory.installations == [SUBJECT.installation_id]
    assert [request.path for request in transport.requests] == [
        f"/repositories/{REPOSITORY_ID}",
        JOBS_PATH,
    ]


@pytest.mark.parametrize("value", [SUBJECT, None, "303/2"])
def test_attempt_entrypoint_rejects_foreign_nominal_values_before_io(value: object) -> None:
    transport = _Transport(_complete_handler([]))
    factory = _Factory(transport)

    with pytest.raises(TypeError, match="exact attempt identity"):
        asyncio.run(_observer(factory).load_attempt_identity(cast(AttemptIdentity, value)))
    assert not factory.installations
    assert not transport.requests


@pytest.mark.parametrize("path", [JOBS_PATH, "/repositories/202/actions/runs/303/attempts/2/jobs"])
@pytest.mark.parametrize("entrypoint", ["subject", "attempt"])
def test_jobs_are_read_through_exact_bounded_pagination(path: str, entrypoint: str) -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "repositories.get_by_id":
            return _response(_repository_body())
        page = dict((item.name, item.value) for item in request.query)["page"]
        if page == "1":
            return _response(
                _jobs_body(
                    [_job(1, SIGNAL.job_name, "completed", "success")],
                    total_count=2,
                ),
                pagination=_next_page(2, path=path),
            )
        assert page == "2", f"unexpected jobs page: {page}"
        return _response(
            _jobs_body(
                [_job(2, OTHER_SIGNAL.job_name, "completed", "failure")],
                total_count=2,
            )
        )

    transport = _Transport(handler)

    observer = _observer(_Factory(transport))
    if entrypoint == "subject":
        observations = asyncio.run(observer.poll(SUBJECT, TWO_SIGNALS))
        assert not isinstance(observations, ProviderSignalAmbiguity)
        assert [observation.signal_id for observation in observations] == [
            signal.signal_id for signal in TWO_SIGNALS
        ]
        assert {observation.provider_job_id for observation in observations} == {1, 2}
    else:
        attempt = AttemptIdentity(
            RepositoryScope(101, REPOSITORY_ID), WORKFLOW_RUN_ID, RUN_ATTEMPT, SUBJECT.head_sha
        )
        jobs = asyncio.run(observer.load_attempt_identity(attempt))
        assert [job.job_id for job in jobs] == [1, 2]
        assert all(job.head_sha == attempt.head_sha for job in jobs)
    assert [request.path for request in transport.requests] == [
        f"/repositories/{REPOSITORY_ID}",
        JOBS_PATH,
        JOBS_PATH,
    ]
    assert [
        tuple((item.name, item.value) for item in request.query)
        for request in transport.requests[1:]
    ] == [
        (("page", "1"), ("per_page", "100")),
        (("page", "2"), ("per_page", "100")),
    ]


def test_attempt_alias_oracle_rejects_a_missing_repository_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def omit_identity(
        pagination: GitHubPaginationEvidence,
        *,
        expected_path: str,
        current_page: int,
        repository_id: int | None = None,
    ) -> int | None:
        assert repository_id == REPOSITORY_ID
        return next_page_number(
            pagination, expected_path=expected_path, current_page=current_page, repository_id=None
        )

    path = "/repositories/202/actions/runs/303/attempts/2/jobs"
    test_jobs_are_read_through_exact_bounded_pagination(path, "attempt")
    with monkeypatch.context() as patch:
        # noinspection PyUnresolvedReferences
        patch.setattr(reconciliation_observer, "next_page_number", omit_identity)
        test_jobs_are_read_through_exact_bounded_pagination(JOBS_PATH, "attempt")
        with pytest.raises(GitHubReconciliationObservationError) as rejected:
            test_jobs_are_read_through_exact_bounded_pagination(path, "attempt")
        assert rejected.value.reason is ObservationFailureReason.JOBS_TRUNCATED
    test_jobs_are_read_through_exact_bounded_pagination(path, "attempt")


def test_repository_id_mismatch_stops_before_job_lookup() -> None:
    transport = _Transport(lambda _: _response(_repository_body(repository_id=REPOSITORY_ID + 1)))

    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.REPOSITORY_MISMATCH
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        b'{"id":202,"id":202,"name":"service","full_name":"acme/service","owner":{"login":"acme"}}',
        b'{"id":202,"name":"service","full_name":"other/service","owner":{"login":"acme"}}',
        b"not-json",
    ],
    ids=["duplicate-key", "inconsistent-full-name", "invalid-json"],
)
def test_malformed_repository_identity_fails_closed(body: bytes) -> None:
    transport = _Transport(lambda _: _response(body))

    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.REPOSITORY_MALFORMED


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [
        ("unknown", None),
        ("completed", "unknown"),
        ("completed", None),
        ("queued", "success"),
    ],
)
def test_unsupported_or_inconsistent_job_state_fails_closed(
    status: str,
    conclusion: str | None,
) -> None:
    transport = _Transport(_complete_handler([_job(1, SIGNAL.job_name, status, conclusion)]))

    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.JOBS_MALFORMED


@pytest.mark.parametrize("conclusion", ["action_required", "stale", "startup_failure"])
def test_documented_unclassified_failure_conclusions_remain_terminal_failure(
    conclusion: str,
) -> None:
    transport = _Transport(_complete_handler([_job(1, SIGNAL.job_name, "completed", conclusion)]))

    observations = asyncio.run(_observer(_Factory(transport)).poll(SUBJECT, SIGNALS))

    assert not isinstance(observations, ProviderSignalAmbiguity)
    assert observations[0].status == "completed"
    assert observations[0].conclusion == "unknown"


def test_job_run_id_mismatch_fails_closed() -> None:
    mismatched = _job(1, SIGNAL.job_name, "completed", "success")
    mismatched["run_id"] = WORKFLOW_RUN_ID + 1
    transport = _Transport(_complete_handler([mismatched]))

    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.JOBS_MALFORMED


def test_two_jobs_matching_one_derived_name_are_terminal_ambiguity() -> None:
    transport = _Transport(
        _complete_handler(
            [
                _job(1, SIGNAL.job_name, "completed", "success"),
                _job(2, SIGNAL.job_name, "completed", "failure"),
            ]
        )
    )

    outcome = asyncio.run(_observer(_Factory(transport)).poll(SUBJECT, SIGNALS))

    assert outcome == ProviderSignalAmbiguity(SUBJECT.subject_id, SIGNAL)


def test_duplicate_provider_job_id_is_malformed() -> None:
    transport = _Transport(
        _complete_handler(
            [
                _job(1, SIGNAL.job_name, "completed", "success"),
                _job(1, "Unrelated", "completed", "failure"),
            ]
        )
    )

    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.JOBS_MALFORMED


def test_matching_job_names_across_pages_are_terminal_ambiguity() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "repositories.get_by_id":
            return _response(_repository_body())
        page = dict((item.name, item.value) for item in request.query)["page"]
        job_id = 1 if page == "1" else 2
        pagination = _next_page(2) if page == "1" else None
        return _response(
            _jobs_body(
                [_job(job_id, SIGNAL.job_name, "completed", "success")],
                total_count=2,
            ),
            pagination=pagination,
        )

    outcome = asyncio.run(_observer(_Factory(_Transport(handler))).poll(SUBJECT, SIGNALS))

    assert outcome == ProviderSignalAmbiguity(SUBJECT.subject_id, SIGNAL)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        (
            b'{"total_count":1,"jobs":[{"id":1,"id":1,"run_id":303,'
            b'"name":"Build","status":"completed","conclusion":"success"}]}'
        ),
        b'{"total_count":1,"jobs":{}}',
    ],
    ids=["invalid-json", "duplicate-key", "invalid-jobs-shape"],
)
def test_malformed_jobs_json_fails_closed(body: bytes) -> None:
    transport = _Transport(_complete_handler_body(body))

    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.JOBS_MALFORMED


def test_provider_terminal_truncation_never_means_complete() -> None:
    transport = _Transport(
        _complete_handler_body(
            _jobs_body([_job(1, SIGNAL.job_name, "completed", "success")], total_count=2)
        )
    )

    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.JOBS_TRUNCATED


def test_page_and_job_limits_fail_closed_before_unbounded_work() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "repositories.get_by_id":
            return _response(_repository_body())
        return _response(
            _jobs_body(
                [_job(1, SIGNAL.job_name, "completed", "success")],
                total_count=2,
            ),
            pagination=_next_page(2),
        )

    page_limited = _observer(
        _Factory(_Transport(handler)),
        limits=ReconciliationObserverLimits(max_pages=1),
    )
    job_limited = _observer(
        _Factory(
            _Transport(
                _complete_handler(
                    [
                        _job(1, SIGNAL.job_name, "completed", "success"),
                        _job(2, "Other", "completed", "success"),
                    ]
                )
            )
        ),
        limits=ReconciliationObserverLimits(max_jobs=1),
    )

    with pytest.raises(GitHubReconciliationObservationError) as page_error:
        asyncio.run(page_limited.poll(SUBJECT, SIGNALS))
    with pytest.raises(GitHubReconciliationObservationError) as job_error:
        asyncio.run(job_limited.poll(SUBJECT, SIGNALS))

    assert page_error.value.reason is ObservationFailureReason.JOBS_TRUNCATED
    assert job_error.value.reason is ObservationFailureReason.JOBS_TRUNCATED


def test_total_response_byte_budget_includes_repository_and_job_pages() -> None:
    repository_body = _repository_body()
    jobs_body = _jobs_body([_job(1, SIGNAL.job_name, "completed", "success")])
    observer = _observer(
        _Factory(_Transport(_complete_handler_body(jobs_body))),
        limits=ReconciliationObserverLimits(
            max_repository_response_bytes=len(repository_body),
            max_total_response_bytes=len(repository_body) + len(jobs_body) - 1,
        ),
    )

    with pytest.raises(GitHubReconciliationObservationError) as raised:
        asyncio.run(observer.poll(SUBJECT, SIGNALS))

    assert raised.value.reason is ObservationFailureReason.JOBS_TRUNCATED


def test_untrusted_next_page_identity_is_not_followed() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "repositories.get_by_id":
            return _response(_repository_body())
        return _response(
            _jobs_body(
                [_job(1, SIGNAL.job_name, "completed", "success")],
                total_count=2,
            ),
            pagination=_next_page(2, path="/repos/other/service/actions/runs/303/attempts/2/jobs"),
        )

    transport = _Transport(handler)
    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.JOBS_TRUNCATED
    assert len(transport.requests) == 2


def test_transport_failures_are_redacted_and_fail_closed() -> None:
    failure = GitHubTransportFailure(kind="unavailable", message="secret provider detail")
    transport = _Transport(lambda _: failure)

    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.PROVIDER_UNAVAILABLE
    assert str(error) == "GitHub reconciliation observation is unavailable"
    assert "secret" not in str(error)


def test_unexpected_transport_exception_is_not_misclassified_as_provider_unavailability() -> None:
    observer = _observer(_Factory(_Transport(lambda _: ValueError("adapter defect"))))

    with pytest.raises(ValueError, match="adapter defect"):
        asyncio.run(observer.poll(SUBJECT, SIGNALS))


def test_job_transport_failure_is_not_misreported_as_an_empty_observation() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "repositories.get_by_id":
            return _response(_repository_body())
        return GitHubTransportFailure(kind="unavailable", message="provider unavailable")

    transport = _Transport(handler)

    error = _poll_error(_Factory(transport))

    assert error.reason is ObservationFailureReason.PROVIDER_UNAVAILABLE
    assert len(transport.requests) == 2


def test_caller_cancellation_is_never_translated_into_provider_unavailability() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult | BaseException:
        if request.operation == "repositories.get_by_id":
            return _response(_repository_body())
        return asyncio.CancelledError()

    observer = _observer(_Factory(_Transport(handler)))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(observer.poll(SUBJECT, SIGNALS))


def test_ambiguous_contract_stops_before_provider_io() -> None:
    transport = _Transport(_complete_handler([]))
    observer = _observer(_Factory(transport))

    with pytest.raises(GitHubReconciliationObservationError) as raised:
        asyncio.run(observer.poll(SUBJECT, (SIGNAL, SIGNAL)))

    assert raised.value.reason is ObservationFailureReason.AMBIGUOUS_CONTRACT
    assert transport.requests == []


def test_provider_state_transition_changes_immutable_observation_identity() -> None:
    queued = _Transport(_complete_handler([_job(1, SIGNAL.job_name, "queued", None)]))
    completed = _Transport(_complete_handler([_job(1, SIGNAL.job_name, "completed", "success")]))

    first_outcome = asyncio.run(_observer(_Factory(queued)).poll(SUBJECT, SIGNALS))
    second_outcome = asyncio.run(_observer(_Factory(completed)).poll(SUBJECT, SIGNALS))
    assert not isinstance(first_outcome, ProviderSignalAmbiguity)
    assert not isinstance(second_outcome, ProviderSignalAmbiguity)
    first = first_outcome[0]
    second = second_outcome[0]

    assert first.observation_id != second.observation_id
    assert (first.status, first.conclusion) == ("in_progress", None)
    assert (second.status, second.conclusion) == ("completed", "success")


def test_unrelated_complete_jobs_are_not_fabricated_as_contract_observations() -> None:
    transport = _Transport(_complete_handler([_job(1, "Unrelated", "completed", "success")]))

    observations = asyncio.run(_observer(_Factory(transport)).poll(SUBJECT, SIGNALS))

    assert not isinstance(observations, ProviderSignalAmbiguity)
    assert observations == ()


def test_hosted_runner_wire_evidence_reaches_stable_economics_snapshot() -> None:
    job = {
        **_job(1, SIGNAL.job_name, "completed", "success"),
        "runner_group_id": 0,
        "runner_group_name": "GitHub Actions",
    }
    transport = _Transport(_complete_handler([job]))
    factory = _Factory(transport)
    attempt = AttemptIdentity(
        RepositoryScope(SUBJECT.installation_id, SUBJECT.repository_id),
        SUBJECT.workflow_run_id,
        SUBJECT.run_attempt,
        SUBJECT.head_sha,
    )
    source = ProviderRunCollectionSource(
        attempt, datetime(2026, 9, 4, 12, tzinfo=UTC), GITHUB_API_VERSION, "e" * 64
    )

    outcome = asyncio.run(GitHubCiEconomicsProvider(_observer(factory)).load_stable(source))

    assert isinstance(outcome, ProviderAttemptSnapshot)
    assert outcome.subject_id == source.source_id and outcome.attempt == attempt
    assert len(outcome.jobs) == 1
    fact = outcome.jobs[0]
    assert fact.provider_job_id == 1 and fact.conclusion == "success"
    assert fact.runner.canonical_mapping() == {
        "runnerId": 11,
        "runnerName": "runner-a",
        "runnerGroupId": None,
        "runnerGroupName": None,
    }
    assert fact.timing.started_at == datetime(2026, 9, 4, 12, tzinfo=UTC)
    assert fact.timing.completed_at == datetime(2026, 9, 4, 12, 2, tzinfo=UTC)
    assert factory.installations == [SUBJECT.installation_id, SUBJECT.installation_id]
    assert [request.path for request in transport.requests] == [
        "/repositories/202",
        JOBS_PATH,
        "/repositories/202",
        JOBS_PATH,
    ]


def _observer(
    factory: _Factory,
    *,
    limits: ReconciliationObserverLimits | None = None,
) -> GitHubActionsReconciliationObserver:
    if limits is None:
        return GitHubActionsReconciliationObserver(factory)
    return GitHubActionsReconciliationObserver(factory, limits=limits)


def _poll_error(factory: _Factory) -> GitHubReconciliationObservationError:
    with pytest.raises(GitHubReconciliationObservationError) as raised:
        asyncio.run(_observer(factory).poll(SUBJECT, SIGNALS))
    return raised.value


def _complete_handler(
    jobs: list[dict[str, object]],
) -> Callable[[GitHubRequest], GitHubTransportResult]:
    return _complete_handler_body(_jobs_body(jobs))


def _complete_handler_body(body: bytes) -> Callable[[GitHubRequest], GitHubTransportResult]:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        return (
            _response(_repository_body())
            if request.operation == "repositories.get_by_id"
            else _response(body)
        )

    return handler


def _repository_body(repository_id: int = REPOSITORY_ID) -> bytes:
    return _json_bytes(
        {
            "id": repository_id,
            "name": "service",
            "full_name": "acme/service",
            "owner": {"login": "acme"},
        }
    )


def _jobs_body(
    jobs: list[dict[str, object]],
    *,
    total_count: int | None = None,
) -> bytes:
    return _json_bytes(
        {"total_count": len(jobs) if total_count is None else total_count, "jobs": jobs}
    )


def _job(
    job_id: int,
    name: str,
    status: str,
    conclusion: str | None,
) -> dict[str, object]:
    return {
        "id": job_id,
        "run_id": WORKFLOW_RUN_ID,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": SUBJECT.head_sha,
        "started_at": "2026-09-04T12:00:00Z" if status == "completed" else None,
        "completed_at": "2026-09-04T12:02:00Z" if status == "completed" else None,
        "labels": ["self-hosted", "linux"],
        "runner_id": 11,
        "runner_name": "runner-a",
        "runner_group_id": 12,
        "runner_group_name": "linux",
    }


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _response(
    body: bytes,
    *,
    pagination: GitHubPaginationEvidence | None = None,
) -> GitHubResponse:
    return GitHubResponse(
        status=200,
        api_version=GITHUB_API_VERSION,
        headers=(),
        body=body,
        pagination=pagination or GitHubPaginationEvidence.not_paginated(),
    )


def _next_page(
    page: int,
    *,
    path: str = JOBS_PATH,
) -> GitHubPaginationEvidence:
    return GitHubPaginationEvidence(
        complete=False,
        pages_observed=1,
        item_count=None,
        next_page=f"https://api.github.com{path}?page={page}&per_page=100",
        termination="next_page",
    )
