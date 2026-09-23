from __future__ import annotations

import asyncio
import json

import httpx2 as httpx
import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import GitHubReviewerEvidence
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.reviewer_permission import (
    GitHubReviewerPermissionReaderAdapter,
)
from ci_coordinator.proposal_review import GitHubReviewerRejected, GitHubReviewerUnavailable

from ._github_app_transport_support import _factory, _private_key, _token_response


def test_recheck_uses_installation_authority_and_preserves_exact_reviewer_identity() -> None:
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if request.url.path == "/repositories/2":
            return _response({"id": 2, "name": "repo", "owner": {"login": "example"}})
        return _response(
            {
                "permission": "write",
                "role_name": "maintain",
                "user": {"id": 17, "login": "maintainer"},
            }
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        result = asyncio.run(
            GitHubReviewerPermissionReaderAdapter(factory).recheck(
                scope=RepositoryScope(1, 2),
                reviewer_user_id=17,
                reviewer_login="maintainer",
            )
        )
    finally:
        asyncio.run(factory.aclose())

    assert result == GitHubReviewerEvidence(17, "maintainer", "maintain")
    assert paths == [
        "/app/installations/1/access_tokens",
        "/repositories/2",
        "/repos/example/repo/collaborators/maintainer/permission",
    ]


def test_repository_identity_mismatch_stops_before_permission_lookup() -> None:
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        return _response({"id": 3, "name": "repo", "owner": {"login": "example"}})

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        with pytest.raises(GitHubReviewerUnavailable):
            asyncio.run(
                GitHubReviewerPermissionReaderAdapter(factory).recheck(
                    scope=RepositoryScope(1, 2),
                    reviewer_user_id=17,
                    reviewer_login="maintainer",
                )
            )
    finally:
        asyncio.run(factory.aclose())

    assert paths == ["/app/installations/1/access_tokens", "/repositories/2"]


@pytest.mark.parametrize(
    ("user_id", "login"),
    [(0, "maintainer"), (17, "not/canonical")],
    ids=("user-id", "login"),
)
def test_invalid_reviewer_identity_is_rejected_before_provider_io(
    user_id: int,
    login: str,
) -> None:
    observed = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed += 1
        return _response({})

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        with pytest.raises(GitHubReviewerRejected):
            asyncio.run(
                GitHubReviewerPermissionReaderAdapter(factory).recheck(
                    scope=RepositoryScope(1, 2),
                    reviewer_user_id=user_id,
                    reviewer_login=login,
                )
            )
    finally:
        asyncio.run(factory.aclose())

    assert observed == 0


@pytest.mark.parametrize(
    "permission_payload",
    [
        {"permission": "read", "role_name": "read", "user": {"id": 17, "login": "maintainer"}},
        {
            "permission": "write",
            "role_name": "maintain",
            "user": {"id": 18, "login": "maintainer"},
        },
        {
            "permission": "write",
            "role_name": "maintain",
            "user": {"id": 17, "login": "renamed"},
        },
    ],
    ids=("revoked", "user-id-changed", "login-changed"),
)
def test_recheck_rejects_each_permission_or_identity_change(
    permission_payload: dict[str, object],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if request.url.path == "/repositories/2":
            return _response({"id": 2, "name": "repo", "owner": {"login": "example"}})
        return _response(permission_payload)

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        with pytest.raises(GitHubReviewerRejected):
            asyncio.run(
                GitHubReviewerPermissionReaderAdapter(factory).recheck(
                    scope=RepositoryScope(1, 2),
                    reviewer_user_id=17,
                    reviewer_login="maintainer",
                )
            )
    finally:
        asyncio.run(factory.aclose())


@pytest.mark.parametrize("status", [404, 429, 500], ids=("revoked", "rate-limit", "provider"))
def test_recheck_preserves_rejection_vs_unavailability(status: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if request.url.path == "/repositories/2":
            return _response({"id": 2, "name": "repo", "owner": {"login": "example"}})
        return _response({}, status=status)

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    expected = GitHubReviewerRejected if status == 404 else GitHubReviewerUnavailable
    try:
        with pytest.raises(expected):
            asyncio.run(
                GitHubReviewerPermissionReaderAdapter(factory).recheck(
                    scope=RepositoryScope(1, 2),
                    reviewer_user_id=17,
                    reviewer_login="maintainer",
                )
            )
    finally:
        asyncio.run(factory.aclose())


def _response(payload: object, *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"x-github-api-version-selected": GITHUB_API_VERSION},
        content=json.dumps(payload).encode(),
    )
