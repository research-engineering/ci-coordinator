from __future__ import annotations

import asyncio
import json

import httpx2 as httpx
import jwt
import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.provider_inventory import GitHubProviderInventory
from ci_coordinator.integrations.github.repository_membership import GitHubRepositoryAccess
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

from ._github_app_transport_support import (
    APP_ID,
    INSTALLATION_TOKEN,
    _factory,
    _private_key,
    _token_response,
)
from .test_github_provider_inventory import _installation_body, _repository_page_body, _response

SCOPE = RepositoryScope(77, 501)


def _page(ids: list[int], *, total: int | None = None, **changes: object) -> bytes:
    repository = json.loads(_repository_page_body(total_count=1))["repositories"][0]
    return json.dumps(
        {
            "total_count": len(ids) if total is None else total,
            "repositories": [
                {**repository, "id": identity, "node_id": f"R_{identity}", **changes}
                for identity in ids
            ],
        }
    ).encode()


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("visibility", ["private", "public"])
async def test_membership_uses_app_identity_and_positive_installation_repository_evidence(
    archived: bool,
    visibility: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if request.url.path == "/app/installations/77":
            return _response(_installation_body())
        assert request.url.path == "/installation/repositories"
        return _response(_page([501], archived=archived, visibility=visibility))

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        assert await GitHubRepositoryAccess(factory).allows_repository(SCOPE)
        assert [request.url.path for request in requests] == [
            "/app/installations/77",
            "/app/installations/77/access_tokens",
            "/installation/repositories",
        ]
        token = requests[0].headers["authorization"].removeprefix("Bearer ")
        assert jwt.decode(token, options={"verify_signature": False})["iss"] == APP_ID
        assert requests[2].headers["authorization"] == f"Bearer {INSTALLATION_TOKEN}"
        assert str(requests[2].url.params) == "page=1&per_page=100"
    finally:
        await factory.aclose()


@pytest.mark.parametrize("change", ["removed", "suspended", "disabled", "unavailable"])
async def test_same_reader_rechecks_membership_after_a_successful_grant(change: str) -> None:
    changed = False
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if request.url.path == "/app/installations/77":
            installation = json.loads(_installation_body())
            if changed and change == "suspended":
                installation["suspended_at"] = "2026-09-09T00:00:00Z"
            return _response(json.dumps(installation).encode())
        assert request.url.path == "/installation/repositories"
        if changed and change == "unavailable":
            return httpx.Response(503)
        return _response(
            _page(
                [502] if changed and change == "removed" else [501],
                disabled=changed and change == "disabled",
                visibility="public",
            )
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    reader = GitHubRepositoryAccess(factory)
    try:
        assert await reader.allows_repository(SCOPE)
        changed = True
        paths.clear()
        if change == "unavailable":
            with pytest.raises(RepositoryAccessUnavailable):
                await reader.allows_repository(SCOPE)
        else:
            assert not await reader.allows_repository(SCOPE)
        assert paths == (
            ["/app/installations/77"]
            if change == "suspended"
            else ["/app/installations/77", "/installation/repositories"]
        )
    finally:
        await factory.aclose()


@pytest.mark.parametrize("stage", ["installation", "repositories"])
@pytest.mark.parametrize("status", [404, 403, 429, 500])
@pytest.mark.parametrize("version_provenance", [False, True])
async def test_provider_failure_is_distinct_from_missing_membership(
    stage: str, status: int, version_provenance: bool
) -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        installation = request.url.path == "/app/installations/77"
        if installation == (stage == "installation"):
            return httpx.Response(
                status,
                headers={"x-github-api-version-selected": GITHUB_API_VERSION}
                if version_provenance
                else {},
                json={"message": "private provider diagnostic"},
            )
        return _response(_installation_body() if installation else _page([501]))

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        if status == 404 and version_provenance:
            assert not await GitHubRepositoryAccess(factory).allows_repository(SCOPE)
        else:
            with pytest.raises(RepositoryAccessUnavailable):
                await GitHubRepositoryAccess(factory).allows_repository(SCOPE)
        if stage == "installation":
            assert requests == ["/app/installations/77"]
    finally:
        await factory.aclose()


@pytest.mark.parametrize(
    "case,expected",
    [
        ("owner_id", "unavailable"),
        ("installation_id", "unavailable"),
        ("suspended", "denied"),
        ("user", "denied"),
        ("disabled", "denied"),
        ("empty", "denied"),
        ("name_reused", "denied"),
        ("malformed", "unavailable"),
        ("pagination", "unavailable"),
        ("api_version", "unavailable"),
        ("cancelled", "cancelled"),
    ],
)
async def test_membership_rejects_independent_identity_and_evidence_substitutions(
    case: str,
    expected: str,
) -> None:
    installation = json.loads(_installation_body())
    if case == "owner_id":
        installation["account"]["id"] = 102
    if case == "installation_id":
        installation["id"] = 78
    if case == "suspended":
        installation["suspended_at"] = "2026-09-09T00:00:00Z"
    if case == "user":
        installation["account"]["type"] = "User"
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if request.url.path == "/app/installations/77":
            return _response(json.dumps(installation).encode())
        if case == "cancelled":
            raise asyncio.CancelledError()
        assert request.url.path == "/installation/repositories"
        ids = [] if case == "empty" else [502] if case == "name_reused" else [501]
        headers = {}
        if case == "pagination":
            headers["link"] = '<https://foreign.example/repositories?page=2>; rel="next"'
        if case == "api_version":
            headers["x-github-api-version-selected"] = "2022-11-28"
        return _response(
            b"{}"
            if case == "malformed"
            else _page(
                ids,
                disabled=case == "disabled",
                visibility="public",
            ),
            headers=headers,
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        if expected == "denied":
            assert not await GitHubRepositoryAccess(factory).allows_repository(SCOPE)
        else:
            with pytest.raises(
                asyncio.CancelledError if expected == "cancelled" else RepositoryAccessUnavailable
            ):
                await GitHubRepositoryAccess(factory).allows_repository(SCOPE)
        if case in {"suspended", "user", "installation_id"}:
            assert paths == ["/app/installations/77"]
        assert not any(path.startswith(("/repos/", "/repositories/")) for path in paths)
    finally:
        await factory.aclose()


@pytest.mark.parametrize("case", ["second_page", "page_bound", "deadline"])
async def test_membership_has_bounded_paging_and_one_whole_operation_deadline(case: str) -> None:
    pages: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if case == "deadline":
            await asyncio.Event().wait()
        if request.url.path == "/app/installations/77":
            return _response(_installation_body())
        page = int(request.url.params["page"])
        pages.append(page)
        if case == "second_page" and page == 2:
            return _response(_page([501], total=101))
        ids = list(range(100_000 + page * 100, 100_100 + page * 100))
        total = 101 if case == "second_page" else 10_001
        return _response(
            _page(ids, total=total),
            headers={
                "link": (
                    "<https://api.github.com/installation/repositories"
                    f'?page={page + 1}&per_page=100>; rel="next"'
                ),
            },
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        reader = GitHubRepositoryAccess(factory, timeout_seconds=0.01 if case == "deadline" else 30)
        if case == "second_page":
            assert await reader.allows_repository(SCOPE)
            assert pages == [1, 2]
        else:
            with pytest.raises(RepositoryAccessUnavailable):
                await reader.allows_repository(SCOPE)
            assert pages == ([] if case == "deadline" else list(range(1, 101)))
    finally:
        await factory.aclose()


async def test_unowned_timeout_is_not_reclassified_as_membership_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(_self: object, _installation_id: int) -> object:
        raise TimeoutError("internal defect")

    monkeypatch.setattr(GitHubProviderInventory, "get_installation", broken)
    factory = _factory(_private_key(), httpx.MockTransport(lambda _: _response(b"{}")))
    try:
        with pytest.raises(TimeoutError, match="internal defect"):
            await GitHubRepositoryAccess(factory).allows_repository(SCOPE)
    finally:
        await factory.aclose()


async def test_membership_deadline_covers_credentials_and_later_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    current_time = loop.time()
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal current_time
        paths.append(request.url.path)
        if request.url.path == "/app/installations/77":
            return _response(_installation_body())
        credential = request.url.path.endswith("/access_tokens")
        if credential or request.url.params.get("page") == "2":
            current_time += 0.6
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        if credential:
            return _token_response()
        assert request.url.path == "/installation/repositories"
        if request.url.params["page"] == "2":
            return _response(_page([501], total=101))
        return _response(
            _page(list(range(1000, 1100)), total=101),
            headers={
                "link": (
                    "<https://api.github.com/installation/repositories"
                    '?page=2&per_page=100>; rel="next"'
                )
            },
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        with monkeypatch.context() as clock:
            clock.setattr(loop, "time", lambda: current_time)
            with pytest.raises(RepositoryAccessUnavailable):
                await GitHubRepositoryAccess(factory, timeout_seconds=1).allows_repository(SCOPE)
        assert paths == [
            "/app/installations/77",
            "/app/installations/77/access_tokens",
            "/installation/repositories",
            "/installation/repositories",
        ]
    finally:
        await factory.aclose()


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_membership_rejects_unbounded_deadline(timeout: float) -> None:
    factory = _factory(_private_key(), httpx.MockTransport(lambda _: _response(b"{}")))
    try:
        with pytest.raises(ValueError):
            GitHubRepositoryAccess(factory, timeout_seconds=timeout)
    finally:
        asyncio.run(factory.aclose())
