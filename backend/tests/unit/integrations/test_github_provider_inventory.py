from __future__ import annotations

import asyncio
import json

import httpx2 as httpx
import jwt
import pytest

from ci_coordinator.integrations.github import GitHubAppTransportFactory, GitHubProviderInventory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.provider_inventory_decoding import decode_repository_page
from ci_coordinator.provider_inventory import (
    InstallationPageReadResult,
    InstallationReadResult,
    InstallationSummary,
    ProviderInstallationPage,
    ProviderInventoryUnavailable,
    ProviderRepositoryPage,
    RepositoryReadResult,
)

from ._github_app_transport_support import (
    APP_ID,
    INSTALLATION_TOKEN,
    _factory,
    _private_key,
    _token_response,
)


def test_installation_identity_uses_app_jwt_without_minting_installation_token() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response(_installation_body())

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read_installation(factory))

    assert isinstance(result, InstallationSummary)
    assert result.account_login == "example-org"
    assert [request.url.path for request in requests] == ["/app/installations/77"]
    authorization = requests[0].headers["authorization"]
    assert authorization.startswith("Bearer ")
    token = authorization.removeprefix("Bearer ")
    assert token != INSTALLATION_TOKEN
    assert jwt.decode(token, options={"verify_signature": False})["iss"] == APP_ID


def test_repository_page_uses_exact_installation_token_and_validates_next_link() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        return _response(
            _repository_page_body(total_count=2),
            headers={
                "link": (
                    "<https://api.github.com/installation/repositories?page=2&per_page=1>; "
                    'rel="next"'
                )
            },
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read_repositories(factory, per_page=1))

    assert isinstance(result, ProviderRepositoryPage)
    assert result.has_next_page is True
    assert result.repositories[0].scope.installation_id == 77
    assert result.repositories[0].created_at is not None
    assert result.repositories[0].created_at.isoformat() == "2020-01-01T12:34:56+00:00"
    assert [request.url.path for request in requests] == [
        "/app/installations/77/access_tokens",
        "/installation/repositories",
    ]
    assert requests[1].headers["authorization"] == f"Bearer {INSTALLATION_TOKEN}"
    assert str(requests[1].url.params) == "page=1&per_page=1"


def test_foreign_or_malformed_inventory_never_becomes_empty_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        return _response(b'{"total_count":1,"repositories":[]}')

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read_repositories(factory, per_page=100))

    assert result == ProviderInventoryUnavailable("malformed_provider_response")


@pytest.mark.parametrize(
    "created_at",
    [123, "2020-01-01", "2020-01-01T00:00:00", "2020-02-30T00:00:00Z"],
)
def test_repository_creation_metadata_rejects_malformed_provider_values(created_at: object) -> None:
    page = json.loads(_repository_page_body(total_count=1))
    page["repositories"][0]["created_at"] = created_at
    assert (
        decode_repository_page(
            json.dumps(page).encode(),
            installation_id=77,
            page=1,
            per_page=1,
            has_next_page=False,
        )
        is None
    )


def test_missing_repository_creation_metadata_preserves_manual_import_path() -> None:
    page = json.loads(_repository_page_body(total_count=1))
    del page["repositories"][0]["created_at"]
    result = decode_repository_page(
        json.dumps(page).encode(),
        installation_id=77,
        page=1,
        per_page=1,
        has_next_page=False,
    )
    assert result is not None
    assert result.repositories[0].created_at is None


def test_rate_limit_is_preserved_without_provider_body_disclosure() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={
                "retry-after": "30",
                "x-github-api-version-selected": GITHUB_API_VERSION,
            },
            content=b'{"secret":"provider diagnostic"}',
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read_installation(factory))

    assert result == ProviderInventoryUnavailable("rate_limited", 30)
    assert "provider diagnostic" not in repr(result)


@pytest.mark.parametrize("next_link", [False, True])
def test_app_catalog_reads_bounded_pages_without_installation_credentials(next_link: bool) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        headers = (
            {"link": '<https://api.github.com/app/installations?page=3&per_page=1>; rel="next"'}
            if next_link
            else {}
        )
        return _response(b"[" + _installation_body() + b"]", headers=headers)

    result = asyncio.run(
        _read_installations(_factory(_private_key(), httpx.MockTransport(handler)), page=2)
    )
    assert isinstance(result, ProviderInstallationPage)
    assert (result.page, result.per_page, result.has_next_page) == (2, 1, next_link)
    assert result.installations[0].installation_id == 77
    assert len(requests) == 1
    assert requests[0].url.path == "/app/installations"
    assert str(requests[0].url.params) == "page=2&per_page=1"
    token = requests[0].headers["authorization"].removeprefix("Bearer ")
    assert jwt.decode(token, options={"verify_signature": False})["iss"] == APP_ID


@pytest.mark.parametrize(
    "case,link",
    [
        ("object", None),
        ("malformed", None),
        ("duplicate", None),
        ("empty", '<https://api.github.com/app/installations?page=2&per_page=1>; rel="next"'),
        (
            "valid",
            '<https://example.com/app/installations?page=2&per_page=1>; rel="next"',
        ),
        (
            "valid",
            '<https://api.github.com/user/installations?page=2&per_page=1>; rel="next"',
        ),
    ],
)
def test_app_catalog_rejects_incomplete_or_substituted_evidence(
    case: str, link: str | None
) -> None:
    body = {
        "object": b"{}",
        "malformed": b"[{}]",
        "empty": b"[]",
        "duplicate": b"[" + _installation_body() + b"," + _installation_body() + b"]",
        "valid": b"[" + _installation_body() + b"]",
    }[case]

    async def handler(_: httpx.Request) -> httpx.Response:
        return _response(body, headers={"link": link} if link else {})

    result = asyncio.run(
        _read_installations(_factory(_private_key(), httpx.MockTransport(handler)))
    )
    assert isinstance(result, ProviderInventoryUnavailable)


async def _read_installations(
    factory: GitHubAppTransportFactory, *, page: int = 1
) -> InstallationPageReadResult:
    try:
        return await GitHubProviderInventory(factory).list_installations(page=page, per_page=1)
    finally:
        await factory.aclose()


def _response(body: bytes, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        headers={
            "content-type": "application/json",
            "x-github-api-version-selected": GITHUB_API_VERSION,
            **(headers or {}),
        },
        content=body,
    )


async def _read_installation(factory: GitHubAppTransportFactory) -> InstallationReadResult:
    try:
        return await GitHubProviderInventory(factory).get_installation(77)
    finally:
        await factory.aclose()


async def _read_repositories(
    factory: GitHubAppTransportFactory,
    *,
    per_page: int,
) -> RepositoryReadResult:
    try:
        return await GitHubProviderInventory(factory).list_repositories(
            77,
            page=1,
            per_page=per_page,
        )
    finally:
        await factory.aclose()


def _installation_body() -> bytes:
    return json.dumps(
        {
            "id": 77,
            "account": {
                "id": 101,
                "login": "example-org",
                "type": "Organization",
            },
            "repository_selection": "selected",
            "suspended_at": None,
        }
    ).encode()


def _repository_page_body(*, total_count: int) -> bytes:
    return json.dumps(
        {
            "total_count": total_count,
            "repositories": [
                {
                    "id": 501,
                    "node_id": "R_501",
                    "owner": {"id": 101, "login": "example-org"},
                    "name": "ci-coordinator",
                    "full_name": "example-org/ci-coordinator",
                    "visibility": "private",
                    "default_branch": "master",
                    "created_at": "2020-01-01T12:34:56Z",
                    "archived": False,
                    "disabled": False,
                    "fork": False,
                }
            ],
        }
    ).encode()
