from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

import pytest

from ci_coordinator.integrations.github import (
    ActionsClient,
    AppIdentityClient,
    DiffClient,
    GitHubHeader,
    GitHubIncomplete,
    GitHubPaginationEvidence,
    GitHubRateLimitEvidence,
    GitHubRepository,
    GitHubRequest,
    GitHubResponse,
    GitHubSuccess,
    GitHubTransportFailure,
    GitHubUnavailable,
)
from ci_coordinator.integrations.github.contracts import GitHubTransportResult
from ci_coordinator.integrations.github.installation_request_admission import (
    installation_request_is_admitted,
)

API_VERSION = "2022-11-28"
_RUN_REQUEST = GitHubRequest(
    "actions.get_workflow_run", "GET", "/repos/acme/service/actions/runs/303", API_VERSION
)


@dataclass
class FakeTransport:
    result: GitHubTransportResult | BaseException
    requests: list[GitHubRequest] = field(default_factory=list)

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        self.requests.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def response(
    status: int,
    *,
    api_version: str | None = API_VERSION,
    pagination: GitHubPaginationEvidence | None = None,
    rate_limit: GitHubRateLimitEvidence | None = None,
) -> GitHubResponse:
    return GitHubResponse(
        status=status,
        api_version=api_version,
        headers=(),
        body=b'{"provider":"github"}',
        pagination=pagination or GitHubPaginationEvidence.not_paginated(),
        rate_limit=rate_limit,
    )


def test_non_2xx_is_typed_unavailable_not_success() -> None:
    transport = FakeTransport(response(500))

    result = asyncio.run(AppIdentityClient(transport, api_version=API_VERSION).get_installation(77))

    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "non_success"
    assert result.failure.status == 500


def test_get_workflow_run_constructs_one_exact_admitted_request() -> None:
    transport = FakeTransport(response(200))
    result = asyncio.run(
        ActionsClient(transport, api_version=API_VERSION).get_workflow_run(
            GitHubRepository("acme", "service"), 303
        )
    )
    expected = GitHubRequest(
        "actions.get_workflow_run",
        "GET",
        "/repos/acme/service/actions/runs/303",
        API_VERSION,
        headers=(GitHubHeader("X-GitHub-Api-Version", API_VERSION),),
    )
    assert isinstance(result, GitHubSuccess)
    assert transport.requests == [expected]
    assert installation_request_is_admitted(expected)


@pytest.mark.parametrize(
    "candidate",
    [
        replace(_RUN_REQUEST, operation="actions.list_workflow_runs"),
        replace(_RUN_REQUEST, method="POST"),
        replace(_RUN_REQUEST, body=b"{}"),
        replace(_RUN_REQUEST, path="/repos/acme/service/actions/runs/303/attempts/1"),
        replace(_RUN_REQUEST, path="/repos/acme/service/actions/runs/303/jobs"),
        replace(_RUN_REQUEST, path="/repos/acme/service/actions/runs/0303"),
        replace(_RUN_REQUEST, path="/repos/acme/service/actions/runs/0"),
        replace(_RUN_REQUEST, path="/repos/acme/service/actions/runs/9007199254740992"),
    ],
)
def test_get_workflow_run_admission_is_closed(candidate: GitHubRequest) -> None:
    assert not installation_request_is_admitted(candidate)


def test_get_workflow_run_rejects_query_and_invalid_numeric_identity() -> None:
    from ci_coordinator.integrations.github import GitHubQueryParameter

    request = GitHubRequest(
        "actions.get_workflow_run",
        "GET",
        "/repos/acme/service/actions/runs/303",
        API_VERSION,
        query=(GitHubQueryParameter("page", "1"),),
    )
    assert not installation_request_is_admitted(request)
    transport = FakeTransport(response(200))
    with pytest.raises(ValueError):
        asyncio.run(
            ActionsClient(transport, api_version=API_VERSION).get_workflow_run(
                GitHubRepository("acme", "service"), True
            )
        )
    assert transport.requests == []


def test_incomplete_pagination_is_not_successful_empty_diff() -> None:
    transport = FakeTransport(
        response(
            200,
            pagination=GitHubPaginationEvidence(
                complete=False,
                pages_observed=10,
                item_count=1000,
                next_page="https://api.github.example/next",
                termination="page_limit",
            ),
        )
    )

    result = asyncio.run(
        DiffClient(transport, api_version=API_VERSION).list_pull_request_files(
            repository=repository(),
            pull_request_number=42,
        )
    )

    assert isinstance(result, GitHubIncomplete)
    assert result.response.pagination.complete is False
    assert result.request.operation == "diff.list_pull_request_files"


def test_pull_request_metadata_uses_the_exact_non_paginated_endpoint() -> None:
    transport = FakeTransport(response(200))

    result = asyncio.run(
        DiffClient(transport, api_version=API_VERSION).get_pull_request(
            repository=repository(),
            pull_request_number=42,
        )
    )

    assert isinstance(result, GitHubSuccess)
    assert result.request.operation == "diff.get_pull_request"
    assert result.request.method == "GET"
    assert result.request.path == "/repos/example-org/ci-coordinator/pulls/42"
    assert result.request.query == ()


@pytest.mark.parametrize("pull_request_number", [True, 0, -1])
def test_pull_request_endpoints_reject_non_positive_exact_integers(
    pull_request_number: int,
) -> None:
    client = DiffClient(FakeTransport(response(200)), api_version=API_VERSION)

    with pytest.raises(ValueError, match="positive integer"):
        asyncio.run(client.get_pull_request(repository(), pull_request_number))
    with pytest.raises(ValueError, match="positive integer"):
        asyncio.run(client.list_pull_request_files(repository(), pull_request_number))


@pytest.mark.parametrize(
    ("response_version", "expected_kind"),
    [(None, "missing_api_version_provenance"), ("2022-11-27", "api_version_provenance_mismatch")],
)
def test_api_version_provenance_is_required_and_bound_to_request(
    response_version: str | None,
    expected_kind: str,
) -> None:
    transport = FakeTransport(response(200, api_version=response_version))

    result = asyncio.run(AppIdentityClient(transport, api_version=API_VERSION).get_installation(77))

    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == expected_kind
    assert transport.requests[0].headers == (GitHubHeader("X-GitHub-Api-Version", API_VERSION),)


def test_missing_configured_api_version_stops_before_transport() -> None:
    transport = FakeTransport(response(200))

    result = asyncio.run(AppIdentityClient(transport, api_version=None).get_installation(77))

    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "missing_api_version_provenance"
    assert transport.requests == []


@pytest.mark.parametrize(
    ("transport_result", "expected_kind"),
    [
        (TimeoutError("deadline"), "timeout"),
        (GitHubTransportFailure(kind="cancelled", message="provider cancelled"), "cancelled"),
    ],
)
def test_timeout_and_provider_cancellation_remain_typed(
    transport_result: GitHubTransportResult | BaseException,
    expected_kind: str,
) -> None:
    result = asyncio.run(
        AppIdentityClient(
            FakeTransport(transport_result), api_version=API_VERSION
        ).get_installation(77)
    )

    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == expected_kind


def test_caller_task_cancellation_is_not_suppressed() -> None:
    client = AppIdentityClient(FakeTransport(asyncio.CancelledError()), api_version=API_VERSION)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client.get_installation(77))


@pytest.mark.parametrize(("status", "expected_kind"), [(403, "forbidden"), (404, "not_found")])
def test_forbidden_without_rate_limit_evidence_and_not_found_are_distinct_protocol_outcomes(
    status: int,
    expected_kind: str,
) -> None:
    result = asyncio.run(
        AppIdentityClient(
            FakeTransport(response(status)), api_version=API_VERSION
        ).get_installation(77)
    )

    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == expected_kind


@pytest.mark.parametrize(
    "rate_limit",
    [
        GitHubRateLimitEvidence(
            limit=5000,
            remaining=0,
            reset_at="2026-07-13T12:00:00Z",
            retry_after=None,
        ),
        GitHubRateLimitEvidence(limit=5000, remaining=1, reset_at=None, retry_after="60"),
    ],
)
def test_github_403_rate_limit_evidence_has_priority_over_forbidden(
    rate_limit: GitHubRateLimitEvidence,
) -> None:
    result = asyncio.run(
        AppIdentityClient(
            FakeTransport(response(403, rate_limit=rate_limit)),
            api_version=API_VERSION,
        ).get_installation(77)
    )

    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "rate_limited"
    assert result.failure.rate_limit == rate_limit


def test_rate_limit_preserves_retry_metadata() -> None:
    rate_limit = GitHubRateLimitEvidence(
        limit=5000,
        remaining=0,
        reset_at="2026-07-13T12:00:00Z",
        retry_after="60",
    )
    result = asyncio.run(
        ActionsClient(
            FakeTransport(response(429, rate_limit=rate_limit)), api_version=API_VERSION
        ).list_workflow_runs(repository(), "ci.yml")
    )

    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "rate_limited"
    assert result.failure.rate_limit == rate_limit


def repository() -> GitHubRepository:
    return GitHubRepository(owner="example-org", name="ci-coordinator")
