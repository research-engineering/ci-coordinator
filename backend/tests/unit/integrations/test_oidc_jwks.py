from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx2 as httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from ci_coordinator.identity_admission import JwksTransportFailure, JwksUnavailable
from ci_coordinator.identity_admission.oidc_verifier import (
    MAXIMUM_ACTIONS_OIDC_JWKS_KEYS,
    MINIMUM_ACTIONS_OIDC_RSA_MODULUS_BITS,
)
from ci_coordinator.integrations import (
    GITHUB_ACTIONS_JWKS_CONFIG,
    GITHUB_ACTIONS_JWKS_TIMEOUT_SECONDS,
    GITHUB_ACTIONS_JWKS_URI,
    GitHubActionsJwksProvider,
    HttpxJwksTransport,
)


@dataclass
class ManualMonotonicClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value


def test_transport_uses_only_the_fixed_credential_free_request_shape() -> None:
    async def scenario() -> None:
        observed: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            observed.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b'{"keys": []}',
            )

        transport = HttpxJwksTransport(
            timeout_seconds=5,
            maximum_response_bytes=1024,
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await transport.fetch()
        finally:
            await transport.aclose()

        assert not isinstance(result, JwksTransportFailure)
        assert result.status == 200
        assert result.body == b'{"keys": []}'
        assert len(observed) == 1
        request = observed[0]
        assert str(request.url) == GITHUB_ACTIONS_JWKS_URI
        assert request.method == "GET"
        assert request.headers["Accept"] == "application/json"
        assert "Authorization" not in request.headers

    asyncio.run(scenario())


def test_transport_uses_only_the_explicit_proxy_route(
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
    transport = HttpxJwksTransport(
        timeout_seconds=5,
        maximum_response_bytes=1024,
        outbound_proxy_url="http://proxy.example.test:3128",
    )

    asyncio.run(transport.aclose())

    assert captured["proxy"] == "http://proxy.example.test:3128"
    assert captured["trust_env"] is False
    assert captured["transport"] is None
    assert captured["verify"] is True


def test_transport_rejects_proxy_with_an_injected_transport() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200))

    with pytest.raises(ValueError, match="mutually exclusive"):
        HttpxJwksTransport(
            timeout_seconds=5,
            maximum_response_bytes=1024,
            transport=transport,
            outbound_proxy_url="http://proxy.example.test:3128",
        )


def test_transport_falsifies_redirect_timeout_and_oversize_bodies() -> None:
    async def scenario() -> None:
        async def redirect_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://attacker.invalid/jwks"})

        async def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("deadline elapsed", request=request)

        async def oversize_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"012345678")

        cases = [
            (httpx.MockTransport(redirect_handler), 1024, "redirected"),
            (httpx.MockTransport(timeout_handler), 1024, "timeout"),
            (httpx.MockTransport(oversize_handler), 8, "response_oversize"),
        ]
        for mock_transport, maximum_response_bytes, expected_kind in cases:
            transport = HttpxJwksTransport(
                timeout_seconds=5,
                maximum_response_bytes=maximum_response_bytes,
                transport=mock_transport,
            )
            try:
                result = await transport.fetch()
            finally:
                await transport.aclose()
            assert isinstance(result, JwksTransportFailure)
            assert result.kind == expected_kind

    asyncio.run(scenario())


def test_transport_enforces_one_deadline_across_the_complete_response_stream() -> None:
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            while True:
                await asyncio.sleep(0.003)
                yield b"x"

        async def aclose(self) -> None:
            return None

    async def scenario() -> JwksTransportFailure:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=SlowStream())

        transport = HttpxJwksTransport(
            timeout_seconds=0.01,
            maximum_response_bytes=1024,
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await transport.fetch()
        finally:
            await transport.aclose()
        assert isinstance(result, JwksTransportFailure)
        return result

    assert asyncio.run(scenario()).kind == "timeout"


def test_provider_composition_uses_the_exact_profile_and_owns_client_closure() -> None:
    async def scenario() -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=json.dumps({"keys": [_jwk("current")]}).encode(),
            )

        provider = GitHubActionsJwksProvider(
            ManualMonotonicClock(),
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await provider.get_key_set("current")
        finally:
            await provider.aclose()

        assert not isinstance(result, JwksUnavailable)
        assert GITHUB_ACTIONS_JWKS_CONFIG.maximum_cache_age_seconds == 300
        assert GITHUB_ACTIONS_JWKS_CONFIG.minimum_refresh_interval_seconds == 30
        assert GITHUB_ACTIONS_JWKS_CONFIG.failure_backoff_seconds == 30
        assert GITHUB_ACTIONS_JWKS_CONFIG.maximum_response_bytes == 1024 * 1024
        assert GITHUB_ACTIONS_JWKS_TIMEOUT_SECONDS == 5

    asyncio.run(scenario())


def test_provider_probe_reports_only_an_admitted_current_key_set_as_available() -> None:
    async def scenario() -> None:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=json.dumps({"keys": [_jwk("current")]}).encode(),
            )

        provider = GitHubActionsJwksProvider(
            ManualMonotonicClock(),
            transport=httpx.MockTransport(handler),
        )
        try:
            assert await provider.probe() is True
            assert await provider.probe() is True
        finally:
            await provider.aclose()
        assert calls == 1

    asyncio.run(scenario())


def test_composition_constants_match_the_machine_owned_jwks_profile() -> None:
    profile_path = (
        Path(__file__).resolve().parents[4]
        / "docs/specs/ci-coordinator-core/jwks-provider-profile.v1.json"
    )
    profile = json.loads(profile_path.read_text())

    assert profile["issuer"] == "https://token.actions.githubusercontent.com"
    assert profile["jwksUri"] == GITHUB_ACTIONS_JWKS_URI
    assert profile["transport"] == {
        "method": "GET",
        "redirects": "forbidden",
        "timeoutSeconds": GITHUB_ACTIONS_JWKS_TIMEOUT_SECONDS,
        "maximumBodyBytes": GITHUB_ACTIONS_JWKS_CONFIG.maximum_response_bytes,
        "requiredContentTypePrefix": GITHUB_ACTIONS_JWKS_CONFIG.required_content_type_prefix,
        "credentials": "forbidden",
        "trustEnvironment": False,
        "tlsVerification": "required",
        "outboundProxy": "explicit-admitted-or-absent",
        "proxyScheme": "http",
        "proxyTrailingSlash": "normalized",
        "proxyCredentials": "forbidden",
        "injectedTransportWithProxy": "forbidden",
    }
    assert profile["keySet"] == {
        "maximumKeys": MAXIMUM_ACTIONS_OIDC_JWKS_KEYS,
        "minimumRsaModulusBits": MINIMUM_ACTIONS_OIDC_RSA_MODULUS_BITS,
        "algorithm": "RS256",
        "keyType": "RSA",
        "keyUse": "sig",
        "retainedMembers": ["alg", "e", "key_ops", "kid", "kty", "n", "use"],
        "unknownMemberPolicy": "ignored",
        "privateMemberPolicy": "discard-entry",
        "unsupportedEntryPolicy": "discard-entry",
        "duplicateAdmittedKeyIdPolicy": "reject-response",
        "emptyAdmittedSetPolicy": "reject-response",
    }
    assert profile["cache"] == {
        "maximumAgeSeconds": GITHUB_ACTIONS_JWKS_CONFIG.maximum_cache_age_seconds,
        "minimumRefreshIntervalSeconds": (
            GITHUB_ACTIONS_JWKS_CONFIG.minimum_refresh_interval_seconds
        ),
        "failureBackoffSeconds": GITHUB_ACTIONS_JWKS_CONFIG.failure_backoff_seconds,
        "expiredSnapshot": "forbidden",
        "refresh": "single-flight",
        "unknownKeyPolicy": "at-most-one-refresh-per-minimum-interval",
    }


@pytest.mark.parametrize("fails", [False, True])
def test_pinned_httpx_repeat_close_does_not_retry_a_partially_closed_transport(fails: bool) -> None:
    class PartialTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.owned = {"A", "B"}
            self.close_calls = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert request.url == GITHUB_ACTIONS_JWKS_URI
            return httpx.Response(200, content=b"valid-prior-response")

        async def aclose(self) -> None:
            self.close_calls += 1
            self.owned.discard("A")
            if fails and self.close_calls == 1:
                raise OSError("partial transport close")
            self.owned.discard("B")

    async def scenario() -> None:
        transport = PartialTransport()
        client = httpx.AsyncClient(transport=transport, trust_env=False)
        response = await client.get(GITHUB_ACTIONS_JWKS_URI)
        assert response.content == b"valid-prior-response"
        if fails:
            with pytest.raises(OSError, match="partial transport close"):
                await client.aclose()
        else:
            await client.aclose()
        await client.aclose()
        assert transport.close_calls == 1
        assert transport.owned == ({"B"} if fails else set())
        if fails:
            await transport.aclose()
            assert transport.owned == set()
            assert transport.close_calls == 2

    asyncio.run(scenario())


def test_jwks_fetch_is_fenced_before_a_scheduled_close_can_reach_httpx() -> None:
    async def scenario() -> None:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=b"{}")

        transport = HttpxJwksTransport(
            timeout_seconds=5, maximum_response_bytes=1024, transport=httpx.MockTransport(handler)
        )
        assert not isinstance(await transport.fetch(), JwksTransportFailure)
        closing = asyncio.create_task(transport.aclose())

        async def fetch_before_lower_close_runs() -> JwksTransportFailure:
            assert transport._close_task is not None
            assert transport._client.is_closed is False
            result = await transport.fetch()
            assert isinstance(result, JwksTransportFailure)
            return result

        pending_fetch = asyncio.create_task(fetch_before_lower_close_runs())
        rejected = await pending_fetch
        assert rejected.kind == "unavailable"
        assert calls == 1
        await closing

    asyncio.run(scenario())


def _jwk(key_id: str) -> dict[str, object]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    assert type(public) is dict
    return {**public, "kid": key_id, "use": "sig", "alg": "RS256"}
