from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2 as httpx
import pytest

from ci_coordinator.integrations.github import (
    GitHubQueryParameter,
    GitHubRequest,
    GitHubResponse,
    GitHubTransportFailure,
)
from ci_coordinator.integrations.github import app_http as app_http_module
from ci_coordinator.integrations.github import app_transport as app_transport_module
from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.integrations.github.app_http import _GitHubAppHttpClient, _HttpFailure
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_ACCEPT,
    GITHUB_API_ACCEPT_ENCODING,
    GITHUB_API_BASE_URL,
    GITHUB_API_USER_AGENT,
    GITHUB_API_VERSION,
    GITHUB_APP_JWT_BACKDATE_SECONDS,
    GITHUB_APP_JWT_LIFETIME_SECONDS,
    GITHUB_INSTALLATION_TOKEN_REFRESH_SKEW_SECONDS,
    GITHUB_MAXIMUM_CACHED_INSTALLATIONS,
    GITHUB_MAXIMUM_CONCURRENT_EXCHANGES,
    GITHUB_MAXIMUM_CONCURRENT_REFRESHES,
    GITHUB_MAXIMUM_INSTALLATION_ID,
    GITHUB_MAXIMUM_REQUEST_BODY_BYTES,
    GITHUB_MAXIMUM_RESPONSE_BODY_BYTES,
    GITHUB_REQUEST_TIMEOUT_SECONDS,
)
from ci_coordinator.integrations.github.governance_observation_client import (
    effective_branch_rules_path,
)
from ci_coordinator.integrations.github.installation_request_admission import (
    governance_observation_rules_path_is_admitted,
    installation_request_is_admitted,
)
from ci_coordinator.integrations.github.request_admission import parse_canonical_page_query

from ._github_app_transport_support import (
    _api_response,
    _api_response_handler,
    _factory,
    _private_key,
    _request,
    _token_response,
)


def test_factory_rejects_unsafe_installation_id_before_any_provider_request() -> None:
    factory = _factory(_private_key(), httpx.MockTransport(_api_response_handler))
    try:
        with pytest.raises(ValueError, match="safe integer"):
            factory.for_installation(GITHUB_MAXIMUM_INSTALLATION_ID + 1)
    finally:
        asyncio.run(factory.aclose())


@pytest.mark.parametrize(
    "candidate",
    [
        GitHubRequest("repositories.get_by_id", "GET", "/repositories/11", GITHUB_API_VERSION),
        GitHubRequest(
            "actions.get_workflow_run",
            "GET",
            "/repos/example/ci/actions/runs/7",
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "actions.get_workflow_run_attempt",
            "GET",
            "/repos/example/ci/actions/runs/7/attempts/2",
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "actions.list_workflow_runs",
            "GET",
            "/repos/example/ci/actions/workflows/ci.yml/runs",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            "actions.list_workflow_run_attempt_jobs",
            "GET",
            "/repos/example/ci/actions/runs/7/attempts/2/jobs",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            "checks.list_check_runs",
            "GET",
            "/repos/example/ci/commits/" + "a" * 40 + "/check-runs",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            "diff.get_pull_request", "GET", "/repos/example/ci/pulls/7", GITHUB_API_VERSION
        ),
        GitHubRequest(
            "diff.list_pull_request_files",
            "GET",
            "/repos/example/ci/pulls/7/files",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            "diff.compare",
            "GET",
            "/repos/example/ci/compare/main...feature%2Fone",
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "provider_inventory.list_repositories",
            "GET",
            "/installation/repositories",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            "runner.list_self_hosted_runners",
            "GET",
            "/repos/example/ci/actions/runners",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            "runner.list_visible_self_hosted_runner_groups",
            "GET",
            "/orgs/example/actions/runner-groups",
            GITHUB_API_VERSION,
            query=(
                GitHubQueryParameter("visible_to_repository", "ci"),
                GitHubQueryParameter("page", "1"),
                GitHubQueryParameter("per_page", "100"),
            ),
        ),
        GitHubRequest(
            "runner.list_group_self_hosted_runners",
            "GET",
            "/orgs/example/actions/runner-groups/7/runners",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            "workflow_catalog.list_workflows",
            "GET",
            "/repos/example/ci/actions/workflows",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            "workflow_catalog.get_workflow",
            "GET",
            "/repos/example/ci/actions/workflows/ci.yml",
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "workflow_catalog.get_content",
            "GET",
            "/repos/example/ci/contents/.github/workflows/ci.yml",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("ref", "a" * 40),),
        ),
        GitHubRequest(
            "workflow_discovery.get_repository", "GET", "/repositories/11", GITHUB_API_VERSION
        ),
        GitHubRequest(
            "workflow_authority.get_repository", "GET", "/repositories/11", GITHUB_API_VERSION
        ),
        GitHubRequest(
            "governance_observation.get_repository",
            "GET",
            "/repositories/11",
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "governance_observation.list_effective_branch_rules",
            "GET",
            "/repos/example/ci/rules/branches/feature%2Fone",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            "reviewer_attestation.get_permission",
            "GET",
            "/repos/example/ci/collaborators/maintainer/permission",
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "workflow_discovery.get_reference",
            "GET",
            "/repos/example/ci/git/ref/heads/main",
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "workflow_discovery.get_commit",
            "GET",
            "/repos/example/ci/git/commits/" + "a" * 40,
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "workflow_discovery.get_tree",
            "GET",
            "/repos/example/ci/git/trees/" + "b" * 40,
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "workflow_discovery.get_recursive_tree",
            "GET",
            "/repos/example/ci/git/trees/" + "b" * 40,
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("recursive", "1"),),
        ),
        GitHubRequest(
            "workflow_discovery.get_blob",
            "GET",
            "/repos/example/ci/git/blobs/" + "c" * 40,
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "workflow_authority.get_commit",
            "GET",
            "/repos/example/ci/git/commits/" + "a" * 40,
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "workflow_authority.get_tree",
            "GET",
            "/repos/example/ci/git/trees/" + "b" * 40,
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "workflow_authority.get_blob",
            "GET",
            "/repos/example/ci/git/blobs/" + "c" * 40,
            GITHUB_API_VERSION,
        ),
    ],
)
def test_installation_capability_catalog_admits_every_owned_protocol_request(
    candidate: GitHubRequest,
) -> None:
    assert installation_request_is_admitted(candidate)


@pytest.mark.parametrize(
    "query",
    [
        (),
        (GitHubQueryParameter("recursive", "0"),),
        (GitHubQueryParameter("recursive", "true"),),
        (GitHubQueryParameter("recursive", "1"), GitHubQueryParameter("page", "1")),
        (GitHubQueryParameter("recursive", "1"), GitHubQueryParameter("recursive", "1")),
    ],
)
def test_recursive_tree_capability_rejects_unowned_query_shapes(
    query: tuple[GitHubQueryParameter, ...],
) -> None:
    assert not installation_request_is_admitted(
        GitHubRequest(
            "workflow_discovery.get_recursive_tree",
            "GET",
            "/repos/example/ci/git/trees/" + "b" * 40,
            GITHUB_API_VERSION,
            query=query,
        )
    )


@pytest.mark.parametrize(
    "candidate",
    [
        GitHubRequest(
            "reviewer_attestation.get_user",
            "GET",
            "/user",
            GITHUB_API_VERSION,
        ),
        GitHubRequest(
            "reviewer_attestation.get_repository",
            "GET",
            "/repositories/11",
            GITHUB_API_VERSION,
        ),
    ],
    ids=("reviewer", "repository"),
)
def test_reviewer_user_transport_admits_only_its_two_read_capabilities(
    candidate: GitHubRequest,
) -> None:
    observed: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return _api_response()

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        result = asyncio.run(factory.for_reviewer("ephemeral-reviewer-token").send(candidate))
    finally:
        asyncio.run(factory.aclose())

    assert isinstance(result, GitHubResponse)
    assert len(observed) == 1


@pytest.mark.parametrize(
    "candidate",
    [
        GitHubRequest("reviewer_attestation.get_user", "GET", "/users", GITHUB_API_VERSION),
        GitHubRequest(
            "reviewer_attestation.get_repository",
            "GET",
            "/repositories/11",
            GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"),),
        ),
        GitHubRequest(
            "reviewer_attestation.get_permission",
            "GET",
            "/repos/example/ci/collaborators/maintainer/permission",
            GITHUB_API_VERSION,
        ),
    ],
    ids=("wrong-path", "query", "installation-capability"),
)
def test_reviewer_user_transport_rejects_cross_capability_requests_before_io(
    candidate: GitHubRequest,
) -> None:
    observed = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed += 1
        return _api_response()

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        result = asyncio.run(factory.for_reviewer("ephemeral-reviewer-token").send(candidate))
    finally:
        asyncio.run(factory.aclose())

    assert isinstance(result, GitHubTransportFailure)
    assert observed == 0


@pytest.mark.parametrize(
    ("repository", "branch", "admitted"),
    [
        (GitHubRepository("example", "ci"), "a" * 512, True),
        (GitHubRepository("example", "ci"), "a" * 513, False),
        (
            GitHubRepository("example", "ci"),
            "\N{LATIN SMALL LETTER E WITH ACUTE}" * 256,
            True,
        ),
        (
            GitHubRepository(
                "\N{LATIN SMALL LETTER E WITH ACUTE}" * 256,
                "\N{LATIN SMALL LETTER E WITH ACUTE}" * 256,
            ),
            "\N{LATIN SMALL LETTER E WITH ACUTE}" * 256,
            False,
        ),
    ],
)
def test_governance_rule_path_admission_closes_component_and_encoded_path_bounds(
    repository: GitHubRepository,
    branch: str,
    admitted: bool,
) -> None:
    path = effective_branch_rules_path(repository, branch)

    assert governance_observation_rules_path_is_admitted(path) is admitted


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ((GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")), (1, 100)),
        (
            (GitHubQueryParameter("page", "10000"), GitHubQueryParameter("per_page", "1")),
            (10_000, 1),
        ),
        ((GitHubQueryParameter("page", "0"), GitHubQueryParameter("per_page", "100")), None),
        ((GitHubQueryParameter("page", "01"), GitHubQueryParameter("per_page", "100")), None),
        ((GitHubQueryParameter("page", "10001"), GitHubQueryParameter("per_page", "100")), None),
        ((GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "101")), None),
        ((GitHubQueryParameter("per_page", "100"), GitHubQueryParameter("page", "1")), None),
        ((GitHubQueryParameter("page", "1"), GitHubQueryParameter("page", "2")), None),
        ((GitHubQueryParameter("page", "1"),), None),
    ],
    ids=(
        "minimum",
        "maximum",
        "zero",
        "leading-zero",
        "page-overflow",
        "size-overflow",
        "order",
        "duplicate",
        "missing",
    ),
)
def test_page_query_admission_is_canonical_and_bounded(
    query: tuple[GitHubQueryParameter, ...],
    expected: tuple[int, int] | None,
) -> None:
    assert parse_canonical_page_query(query) == expected


def test_provider_rate_limit_metadata_is_bounded_before_integer_conversion() -> None:
    async def scenario() -> GitHubResponse:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/access_tokens"):
                return _token_response()
            return httpx.Response(
                200,
                headers={
                    "x-github-api-version-selected": GITHUB_API_VERSION,
                    "x-ratelimit-limit": "9" * 5_000,
                    "x-ratelimit-remaining": "1",
                },
                stream=httpx.ByteStream(b"{}"),
            )

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            result = await factory.for_installation(77).send(_request())
        finally:
            await factory.aclose()
        assert isinstance(result, GitHubResponse)
        return result

    result = asyncio.run(scenario())
    assert result.rate_limit is not None
    assert result.rate_limit.limit is None


@pytest.mark.parametrize(
    "candidate",
    [
        GitHubRequest(
            operation="app_identity.get_app",
            method="GET",
            path="/app",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="app_identity.get_installation",
            method="POST",
            path="/app/installations/77",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="app_identity.get_installation",
            method="GET",
            path="/app/installations/077",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="app_identity.get_installation",
            method="GET",
            path="/app/installations/77/access_tokens",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="app_identity.get_installation",
            method="GET",
            path="/app/installations/77",
            api_version=GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"),),
        ),
        GitHubRequest(
            operation="app_identity.get_installation",
            method="GET",
            path="/app/installations/77",
            api_version=GITHUB_API_VERSION,
            body=b"{}",
        ),
        GitHubRequest(
            operation="app_identity.get_repository_installation",
            method="GET",
            path="/repos/../user/installation",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="app_identity.get_repository_installation",
            method="GET",
            path="/repos/example%2Fother/ci/installation",
            api_version=GITHUB_API_VERSION,
        ),
        *(
            GitHubRequest(
                operation="app_identity.list_installations",
                method="GET",
                path="/app/installations",
                api_version=GITHUB_API_VERSION,
                query=query,
            )
            for query in (
                (),
                (GitHubQueryParameter("page", "0"), GitHubQueryParameter("per_page", "30")),
                (GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "101")),
                (GitHubQueryParameter("page", "1"), GitHubQueryParameter("page", "2")),
            )
        ),
    ],
    ids=(
        "app",
        "mutation",
        "noncanonical-id",
        "token-mint",
        "query",
        "body",
        "path-normalization",
        "encoded-owner-separator",
        "catalog-missing-query",
        "catalog-zero-page",
        "catalog-overflow",
        "catalog-duplicate-page",
    ),
)
def test_app_identity_transport_rejects_every_non_installation_capability_without_io(
    candidate: GitHubRequest,
) -> None:
    observed = 0

    async def scenario() -> GitHubTransportFailure:
        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal observed
            observed += 1
            return await _api_response_handler(request)

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            result = await factory.for_app().send(candidate)
        finally:
            await factory.aclose()
        assert isinstance(result, GitHubTransportFailure)
        return result

    result = asyncio.run(scenario())
    assert result.kind == "unavailable"
    assert observed == 0


@pytest.mark.parametrize(
    "candidate",
    [
        GitHubRequest(
            operation="user_access.get_repository",
            method="POST",
            path="/repositories/11",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="user_access.get_repository",
            method="GET",
            path="/repositories/011",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="user_identity.get_user",
            method="GET",
            path="/user/emails",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="user_inventory.list_installations",
            method="GET",
            path="/user/installations",
            api_version=GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            operation="user_inventory.list_repositories",
            method="GET",
            path="/user/installations/7/repositories",
            api_version=GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "0"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            operation="user_inventory.list_repositories",
            method="GET",
            path="/user/installations/7/repositories",
            api_version=GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "0")),
        ),
        GitHubRequest(
            operation="unknown",
            method="GET",
            path="/user",
            api_version=GITHUB_API_VERSION,
        ),
    ],
    ids=(
        "mutation",
        "noncanonical-id",
        "unadmitted-path",
        "unbounded-page",
        "zero-page",
        "zero-page-size",
        "unknown",
    ),
)
def test_user_transport_enforces_the_exact_read_only_capability_without_io(
    candidate: GitHubRequest,
) -> None:
    observed = 0

    async def scenario() -> GitHubTransportFailure:
        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal observed
            observed += 1
            return _api_response()

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            result = await factory.for_reviewer("reviewer-secret").send(candidate)
        finally:
            await factory.aclose()
        assert isinstance(result, GitHubTransportFailure)
        return result

    result = asyncio.run(scenario())

    assert result.kind == "unavailable"
    assert observed == 0
    assert "user-secret" not in repr(result)


@pytest.mark.parametrize(
    "candidate",
    [
        GitHubRequest(
            operation="unknown",
            method="GET",
            path="/repositories/11",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="repositories.get_by_id",
            method="POST",
            path="/repositories/11",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="repositories.get_by_id",
            method="GET",
            path="/user",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="repositories.get_by_id",
            method="GET",
            path="/repositories/11",
            api_version=GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"),),
        ),
        GitHubRequest(
            operation="repositories.get_by_id",
            method="GET",
            path="/repositories/11",
            api_version=GITHUB_API_VERSION,
            body=b"{}",
        ),
        GitHubRequest(
            operation="workflow_discovery.get_reference",
            method="GET",
            path="/repos/example/ci/git/ref/../../../../../user/installations",
            api_version=GITHUB_API_VERSION,
        ),
        GitHubRequest(
            operation="runner.list_self_hosted_runners",
            method="GET",
            path="/repos/example%2Fother/ci/actions/runners",
            api_version=GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
        GitHubRequest(
            operation="runner.list_visible_self_hosted_runner_groups",
            method="GET",
            path="/orgs/example%2Fother/actions/runner-groups",
            api_version=GITHUB_API_VERSION,
            query=(
                GitHubQueryParameter("visible_to_repository", "ci"),
                GitHubQueryParameter("page", "1"),
                GitHubQueryParameter("per_page", "100"),
            ),
        ),
        GitHubRequest(
            operation="runner.list_visible_self_hosted_runner_groups",
            method="GET",
            path="/orgs/example/actions/runner-groups",
            api_version=GITHUB_API_VERSION,
            query=(
                GitHubQueryParameter("page", "1"),
                GitHubQueryParameter("per_page", "100"),
            ),
        ),
        GitHubRequest(
            operation="runner.list_group_self_hosted_runners",
            method="GET",
            path="/orgs/example/actions/runner-groups/0/runners",
            api_version=GITHUB_API_VERSION,
            query=(GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100")),
        ),
    ],
    ids=(
        "operation",
        "method",
        "path",
        "query",
        "body",
        "path-normalization",
        "encoded-owner-separator",
        "encoded-organization-separator",
        "missing-visible-repository",
        "invalid-runner-group-id",
    ),
)
def test_installation_transport_rejects_capability_expansion_before_credential_io(
    candidate: GitHubRequest,
) -> None:
    observed = 0

    async def scenario() -> GitHubTransportFailure:
        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal observed
            observed += 1
            return _api_response()

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            result = await factory.for_installation(77).send(candidate)
        finally:
            await factory.aclose()
        assert isinstance(result, GitHubTransportFailure)
        return result

    result = asyncio.run(scenario())

    assert result.kind == "unavailable"
    assert observed == 0


def test_link_relations_split_relation_lists_and_combine_repeated_fields() -> None:
    pagination = app_transport_module._pagination_evidence(
        (
            '<https://api.github.com/items?page=2>; rel="next last"',
            '<https://api.github.com/items?page=1>; rel="prev first"',
        )
    )

    assert pagination.complete is False
    assert pagination.next_page == "https://api.github.com/items?page=2"
    assert pagination.termination == "next_page"


def test_ambiguous_link_relations_fail_closed_instead_of_claiming_exhaustion() -> None:
    pagination = app_transport_module._pagination_evidence(
        (
            (
                '<https://api.github.com/items?page=2>; rel="next", '
                '<https://api.github.com/items?page=3>; rel="next"'
            ),
        )
    )

    assert pagination.complete is False
    assert pagination.next_page is None
    assert pagination.termination == "unknown"


def test_http_exchange_enforces_one_absolute_deadline_across_response_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            while True:
                await asyncio.sleep(0.003)
                yield b"x"

        async def aclose(self) -> None:
            return None

    async def scenario() -> _HttpFailure:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=SlowStream())

        monkeypatch.setattr(app_http_module, "GITHUB_REQUEST_TIMEOUT_SECONDS", 0.01)
        client = _GitHubAppHttpClient(httpx.MockTransport(handler))
        try:
            result = await client.exchange(
                method="GET",
                path="/repos/example/ci",
                headers=(),
                body=None,
            )
        finally:
            await client.aclose()
        assert isinstance(result, _HttpFailure)
        return result

    assert asyncio.run(scenario()).kind == "timeout"


def test_github_http_client_uses_only_the_explicit_proxy_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def aclose(self) -> None:
            return None

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(httpx, "AsyncClient", CapturingClient)
    client = _GitHubAppHttpClient(
        None,
        outbound_proxy_url="http://proxy.example.test:3128",
    )

    asyncio.run(client.aclose())

    assert captured["proxy"] == "http://proxy.example.test:3128"
    assert captured["trust_env"] is False
    assert captured["transport"] is None
    assert captured["verify"] is True


def test_github_http_client_rejects_proxy_with_an_injected_transport() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200))

    with pytest.raises(ValueError, match="mutually exclusive"):
        _GitHubAppHttpClient(
            transport,
            outbound_proxy_url="http://proxy.example.test:3128",
        )


def test_http_exchange_bounds_shared_client_concurrency_within_the_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> tuple[int, _HttpFailure]:
        active = 0
        maximum_active = 0
        admitted = 0
        two_started = asyncio.Event()
        release = asyncio.Event()

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal active, admitted, maximum_active
            active += 1
            admitted += 1
            maximum_active = max(maximum_active, active)
            if admitted == 2:
                two_started.set()
            try:
                await release.wait()
                return httpx.Response(200, stream=httpx.ByteStream(b"{}"))
            finally:
                active -= 1

        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(app_http_module, "GITHUB_MAXIMUM_CONCURRENT_EXCHANGES", 2)
        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(app_http_module, "GITHUB_REQUEST_TIMEOUT_SECONDS", 1.0)
        client = _GitHubAppHttpClient(httpx.MockTransport(handler))
        try:
            first = asyncio.create_task(
                client.exchange(method="GET", path="/first", headers=(), body=None)
            )
            second = asyncio.create_task(
                client.exchange(method="GET", path="/second", headers=(), body=None)
            )
            await two_started.wait()
            # noinspection PyUnresolvedReferences
            monkeypatch.setattr(app_http_module, "GITHUB_REQUEST_TIMEOUT_SECONDS", 0.02)
            queued = asyncio.create_task(
                client.exchange(method="GET", path="/queued", headers=(), body=None)
            )
            result = await queued
            assert isinstance(result, _HttpFailure)
            assert admitted == 2
            release.set()
            await asyncio.gather(first, second)
            return maximum_active, result
        finally:
            await client.aclose()

    maximum_active, queued_result = asyncio.run(scenario())

    assert maximum_active == 2
    assert queued_result.kind == "timeout"


@pytest.mark.parametrize(
    ("headers", "expected_kind"),
    [
        ((("Content-Encoding", "gzip"),), "response_encoding"),
        (
            (("Content-Length", str(GITHUB_MAXIMUM_RESPONSE_BODY_BYTES + 1)),),
            "response_oversize",
        ),
        ((("Content-Length", "01"),), "response_oversize"),
        (
            (("Content-Length", "1"), ("Content-Length", "1")),
            "response_oversize",
        ),
    ],
    ids=("encoding", "declared-oversize", "noncanonical-length", "duplicate-length"),
)
def test_http_exchange_rejects_response_metadata_before_stream_allocation(
    headers: tuple[tuple[str, str], ...],
    expected_kind: str,
) -> None:
    class UnreadableStream(httpx.AsyncByteStream):
        iterated = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.iterated = True
            raise AssertionError("rejected response stream must not be iterated")
            yield b""  # pragma: no cover

        async def aclose(self) -> None:
            return None

    stream = UnreadableStream()
    observed_accept_encoding: str | None = None

    async def scenario() -> _HttpFailure:
        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal observed_accept_encoding
            observed_accept_encoding = request.headers.get("accept-encoding")
            return httpx.Response(200, headers=headers, stream=stream)

        client = _GitHubAppHttpClient(httpx.MockTransport(handler))
        try:
            result = await client.exchange(
                method="GET",
                path="/repos/example/ci",
                headers=(),
                body=None,
            )
        finally:
            await client.aclose()
        assert isinstance(result, _HttpFailure)
        return result

    assert asyncio.run(scenario()).kind == expected_kind
    assert observed_accept_encoding == GITHUB_API_ACCEPT_ENCODING
    assert stream.iterated is False


def test_http_exchange_reads_the_admitted_response_as_raw_bytes() -> None:
    encoded = b"\x1f\x8bnot-a-decoded-payload"

    async def scenario() -> bytes:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=httpx.ByteStream(encoded))

        client = _GitHubAppHttpClient(httpx.MockTransport(handler))
        try:
            result = await client.exchange(
                method="GET",
                path="/repos/example/ci",
                headers=(),
                body=None,
            )
        finally:
            await client.aclose()
        assert not isinstance(result, _HttpFailure)
        return result.body

    assert asyncio.run(scenario()) == encoded


def test_http_exchange_bounds_a_materialized_injected_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exchange(body: bytes) -> bytes | _HttpFailure:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Length": "0"},
                content=body,
            )

        client = _GitHubAppHttpClient(httpx.MockTransport(handler))
        try:
            result = await client.exchange(
                method="GET",
                path="/repos/example/ci",
                headers=(),
                body=None,
            )
        finally:
            await client.aclose()
        return result if isinstance(result, _HttpFailure) else result.body

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(app_http_module, "GITHUB_MAXIMUM_RESPONSE_BODY_BYTES", 8)

    assert asyncio.run(exchange(b"12345678")) == b"12345678"
    oversized = asyncio.run(exchange(b"123456789"))
    assert isinstance(oversized, _HttpFailure)
    assert oversized.kind == "response_oversize"


def test_composition_constants_match_the_machine_owned_transport_profile() -> None:
    profile_path = (
        Path(__file__).resolve().parents[4]
        / "docs/specs/ci-coordinator-runtime/github-app-transport-profile.v1.json"
    )
    profile = json.loads(profile_path.read_text())

    assert profile["api"] == {
        "baseUrl": GITHUB_API_BASE_URL,
        "version": GITHUB_API_VERSION,
        "accept": GITHUB_API_ACCEPT,
        "acceptEncoding": GITHUB_API_ACCEPT_ENCODING,
        "userAgent": GITHUB_API_USER_AGENT,
        "redirects": "forbidden",
        "trustEnvironment": False,
        "tlsVerification": "required",
        "outboundProxy": "explicit-admitted-or-absent",
        "proxyScheme": "http",
        "proxyTrailingSlash": "normalized",
        "proxyCredentials": "forbidden",
        "injectedTransportWithProxy": "forbidden",
        "requestTimeoutSeconds": GITHUB_REQUEST_TIMEOUT_SECONDS,
        "maximumConcurrentExchanges": GITHUB_MAXIMUM_CONCURRENT_EXCHANGES,
        "maximumRequestBodyBytes": GITHUB_MAXIMUM_REQUEST_BODY_BYTES,
        "maximumResponseBodyBytes": GITHUB_MAXIMUM_RESPONSE_BODY_BYTES,
        "maximumInstallationId": GITHUB_MAXIMUM_INSTALLATION_ID,
    }
    assert profile["appJwt"] == {
        "algorithm": "RS256",
        "issuedAtBackdateSeconds": GITHUB_APP_JWT_BACKDATE_SECONDS,
        "lifetimeSeconds": GITHUB_APP_JWT_LIFETIME_SECONDS,
        "maximumFutureExpirySeconds": 600,
    }
    assert profile["installationToken"] == {
        "creationPathTemplate": "/app/installations/{installationId}/access_tokens",
        "refreshSkewSeconds": GITHUB_INSTALLATION_TOKEN_REFRESH_SKEW_SECONDS,
        "refresh": "single-flight",
        "maximumCachedInstallations": GITHUB_MAXIMUM_CACHED_INSTALLATIONS,
        "maximumConcurrentRefreshes": GITHUB_MAXIMUM_CONCURRENT_REFRESHES,
    }
