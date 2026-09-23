from __future__ import annotations

import asyncio
from typing import cast

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import JwksTransportFailure
from ci_coordinator.integrations import oidc_jwks
from ci_coordinator.integrations.github import app_http, reviewer_attestation
from ci_coordinator.integrations.github.app_http import _GitHubAppHttpClient, _HttpFailure
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.reviewer_attestation import GitHubReviewerProviderAdapter
from ci_coordinator.proposal_review import GitHubReviewerUnavailable


def test_every_github_http_client_reaches_the_explicit_connect_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> list[str]:
        requests: list[str] = []

        async def reject_connect(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                requests.append(request.split(b"\r\n", 1)[0].decode("ascii"))
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(reject_connect, "127.0.0.1", 0)
        socket = server.sockets[0]
        proxy_url = f"http://127.0.0.1:{socket.getsockname()[1]}"
        try:
            jwks = oidc_jwks.HttpxJwksTransport(
                timeout_seconds=1,
                maximum_response_bytes=1_024,
                outbound_proxy_url=proxy_url,
            )
            try:
                jwks_result = await jwks.fetch()
            finally:
                await jwks.aclose()
            assert isinstance(jwks_result, JwksTransportFailure)
            assert jwks_result.kind == "unavailable"

            app_client = _GitHubAppHttpClient(None, outbound_proxy_url=proxy_url)
            try:
                app_result = await app_client.exchange(
                    method="GET",
                    path="/repos/example/ci-coordinator",
                    headers=(),
                    body=None,
                )
            finally:
                await app_client.aclose()
            assert isinstance(app_result, _HttpFailure)
            assert app_result.kind == "unavailable"

            provider = GitHubReviewerProviderAdapter(
                client_id="Iv1.client",
                client_secret="client-secret",
                redirect_uri=(
                    "https://coordinator.example/api/v1/repository-attestations/github/callback"
                ),
                transport_factory=cast(GitHubAppTransportFactory, object()),
                outbound_proxy_url=proxy_url,
            )
            with pytest.raises(GitHubReviewerUnavailable):
                await provider.exchange_and_resolve(
                    code="oauth-code",
                    code_verifier="v" * 43,
                    scope=RepositoryScope(1, 2),
                )
        finally:
            server.close()
            await server.wait_closed()
        return requests

    monkeypatch.setattr(
        oidc_jwks,
        "GITHUB_ACTIONS_JWKS_URI",
        "https://jwks.proxy-falsifier.invalid/.well-known/jwks",
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(app_http, "GITHUB_API_BASE_URL", "https://api.proxy-falsifier.invalid")
    monkeypatch.setattr(
        reviewer_attestation,
        "_OAUTH_BASE_URL",
        "https://oauth.proxy-falsifier.invalid",
    )

    assert asyncio.run(scenario()) == [
        "CONNECT jwks.proxy-falsifier.invalid:443 HTTP/1.1",
        "CONNECT api.proxy-falsifier.invalid:443 HTTP/1.1",
        "CONNECT oauth.proxy-falsifier.invalid:443 HTTP/1.1",
    ]
