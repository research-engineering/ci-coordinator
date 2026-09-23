from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import GitHubReviewerEvidence, ReviewerPermission
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.reviewer_attestation import (
    GitHubReviewerProviderAdapter,
)
from ci_coordinator.proposal_review import GitHubReviewerRejected, GitHubReviewerUnavailable

from ._github_app_transport_support import _factory, _private_key

_CLIENT_SECRET = "s" * 32
_REDIRECT_URI = "https://ci.example.test/api/v1/repository-attestations/github/callback"
_STATE = "S" * 43
_VERIFIER = "V" * 43


@pytest.mark.parametrize(
    "headers",
    [
        (("content-type", "application/json"), ("content-encoding", "gzip")),
        (("content-type", "application/json"), ("content-length", "65537")),
        (("content-type", "application/json"), ("content-length", "01")),
        (
            ("content-type", "application/json"),
            ("content-length", "1"),
            ("content-length", "1"),
        ),
        (("content-type", "application/json"), ("content-type", "text/json")),
    ],
    ids=("encoding", "oversize", "noncanonical-length", "duplicate-length", "media-type"),
)
def test_oauth_response_metadata_is_rejected_before_stream_iteration(
    headers: tuple[tuple[str, str], ...],
) -> None:
    class UnreadableStream(httpx.AsyncByteStream):
        iterated = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.iterated = True
            raise AssertionError("rejected OAuth response body must not be read")
            yield b""  # pragma: no cover

        async def aclose(self) -> None:
            return None

    stream = UnreadableStream()
    observed_accept_encoding: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_accept_encoding
        observed_accept_encoding = request.headers.get("accept-encoding")
        return httpx.Response(200, headers=headers, stream=stream)

    provider = GitHubReviewerProviderAdapter(
        client_id="reviewer-client",
        client_secret=_CLIENT_SECRET,
        redirect_uri=_REDIRECT_URI,
        transport_factory=cast(GitHubAppTransportFactory, object()),
        oauth_transport=httpx.MockTransport(handler),
    )

    async def scenario() -> None:
        with pytest.raises(GitHubReviewerUnavailable):
            await provider._exchange_code(
                code="authorization-code",
                code_verifier="A" * 43,
            )

    asyncio.run(scenario())
    assert observed_accept_encoding == "identity"
    assert stream.iterated is False


def test_authorization_url_binds_exact_client_redirect_state_and_pkce_context() -> None:
    provider = GitHubReviewerProviderAdapter(
        client_id="reviewer-client",
        client_secret=_CLIENT_SECRET,
        redirect_uri=_REDIRECT_URI,
        transport_factory=cast(GitHubAppTransportFactory, object()),
    )

    parsed = urlsplit(provider.authorization_url(state=_STATE, code_challenge="C" * 43))

    assert (parsed.scheme, parsed.netloc, parsed.path, parsed.fragment) == (
        "https",
        "github.com",
        "/login/oauth/authorize",
        "",
    )
    assert parse_qs(parsed.query, strict_parsing=True) == {
        "allow_signup": ["false"],
        "client_id": ["reviewer-client"],
        "code_challenge": ["C" * 43],
        "code_challenge_method": ["S256"],
        "prompt": ["select_account"],
        "redirect_uri": [_REDIRECT_URI],
        "state": [_STATE],
    }


@pytest.mark.parametrize(
    ("permissions", "expected_permission"),
    [
        ({"admin": True, "maintain": False}, "admin"),
        ({"admin": False, "maintain": True}, "maintain"),
    ],
    ids=("admin", "maintain"),
)
def test_exchange_resolves_exact_reviewer_and_manager_permission(
    permissions: dict[str, bool],
    expected_permission: ReviewerPermission,
) -> None:
    result, paths, authorizations, oauth_body = _exchange(
        user_payload={"id": 17, "login": "maintainer"},
        repository_payload={"id": 2, "permissions": permissions},
    )

    assert result == GitHubReviewerEvidence(17, "maintainer", expected_permission)
    assert paths == ("/user", "/repositories/2")
    assert authorizations == ("Bearer reviewer-token", "Bearer reviewer-token")
    assert parse_qs(oauth_body.decode("ascii"), strict_parsing=True) == {
        "client_id": ["reviewer-client"],
        "client_secret": [_CLIENT_SECRET],
        "code": ["authorization-code"],
        "code_verifier": [_VERIFIER],
        "redirect_uri": [_REDIRECT_URI],
    }


@pytest.mark.parametrize(
    ("status", "content_type", "body", "expected_error"),
    [
        (400, "application/json", b"{}", GitHubReviewerRejected),
        (429, "application/json", b"{}", GitHubReviewerUnavailable),
        (500, "application/json", b"{}", GitHubReviewerUnavailable),
        (200, "text/plain", b"{}", GitHubReviewerUnavailable),
        (
            200,
            "application/json",
            b'{"error":"bad_verification_code"}',
            GitHubReviewerRejected,
        ),
        (
            200,
            "application/json",
            b'{"access_token":"reviewer-token","token_type":"bearer","expires_in":0}',
            GitHubReviewerRejected,
        ),
        (200, "application/json", b"not-json", GitHubReviewerUnavailable),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "access_token": _CLIENT_SECRET,
                    "token_type": "bearer",
                    "expires_in": 3_600,
                }
            ).encode(),
            GitHubReviewerUnavailable,
        ),
    ],
    ids=(
        "provider-rejection",
        "rate-limit",
        "provider-failure",
        "media-type",
        "oauth-error",
        "non-expiring-token",
        "malformed-json",
        "credential-reflection",
    ),
)
def test_exchange_preserves_oauth_rejection_and_unavailability(
    status: int,
    content_type: str,
    body: bytes,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        _exchange(
            user_payload={"id": 17, "login": "maintainer"},
            repository_payload={
                "id": 2,
                "permissions": {"admin": False, "maintain": True},
            },
            oauth_status=status,
            oauth_content_type=content_type,
            oauth_body=body,
        )


@pytest.mark.parametrize(
    ("user_payload", "repository_payload", "expected_error"),
    [
        (
            {"id": 0, "login": "maintainer"},
            {"id": 2, "permissions": {"admin": False, "maintain": True}},
            GitHubReviewerUnavailable,
        ),
        (
            {"id": 17, "login": "not/canonical"},
            {"id": 2, "permissions": {"admin": False, "maintain": True}},
            GitHubReviewerUnavailable,
        ),
        (
            {"id": 17, "login": "maintainer"},
            {"id": 3, "permissions": {"admin": False, "maintain": True}},
            GitHubReviewerUnavailable,
        ),
        (
            {"id": 17, "login": "maintainer"},
            {"id": 2, "permissions": {"admin": "yes", "maintain": False}},
            GitHubReviewerUnavailable,
        ),
        (
            {"id": 17, "login": "maintainer"},
            {"id": 2, "permissions": {"admin": False, "maintain": False}},
            GitHubReviewerRejected,
        ),
    ],
    ids=(
        "user-id",
        "login",
        "repository-id",
        "permission-shape",
        "insufficient-permission",
    ),
)
def test_exchange_rejects_malformed_or_insufficient_reviewer_evidence(
    user_payload: object,
    repository_payload: object,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        _exchange(
            user_payload=user_payload,
            repository_payload=repository_payload,
        )


def _exchange(
    *,
    user_payload: object,
    repository_payload: object,
    oauth_status: int = 200,
    oauth_content_type: str = "application/json; charset=utf-8",
    oauth_body: bytes | None = None,
) -> tuple[GitHubReviewerEvidence, tuple[str, ...], tuple[str, ...], bytes]:
    paths: list[str] = []
    authorizations: list[str] = []
    oauth_requests: list[httpx.Request] = []

    async def github_handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        authorizations.append(request.headers["authorization"])
        if request.url.path == "/user":
            payload = user_payload
        elif request.url.path == "/repositories/2":
            payload = repository_payload
        else:
            raise AssertionError(f"unexpected reviewer request: {request.url.path}")
        return httpx.Response(
            200,
            headers={"x-github-api-version-selected": GITHUB_API_VERSION},
            content=json.dumps(payload).encode(),
        )

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        oauth_requests.append(request)
        content = (
            oauth_body
            if oauth_body is not None
            else json.dumps(
                {
                    "access_token": "reviewer-token",
                    "token_type": "bearer",
                    "expires_in": 3_600,
                }
            ).encode()
        )
        return httpx.Response(
            oauth_status,
            headers={"content-type": oauth_content_type},
            content=content,
        )

    factory = _factory(_private_key(), httpx.MockTransport(github_handler))
    provider = GitHubReviewerProviderAdapter(
        client_id="reviewer-client",
        client_secret=_CLIENT_SECRET,
        redirect_uri=_REDIRECT_URI,
        transport_factory=factory,
        oauth_transport=httpx.MockTransport(oauth_handler),
    )

    async def scenario() -> GitHubReviewerEvidence:
        try:
            return await provider.exchange_and_resolve(
                code="authorization-code",
                code_verifier=_VERIFIER,
                scope=RepositoryScope(1, 2),
            )
        finally:
            await factory.aclose()

    result = asyncio.run(scenario())
    assert len(oauth_requests) == 1
    return result, tuple(paths), tuple(authorizations), oauth_requests[0].content
