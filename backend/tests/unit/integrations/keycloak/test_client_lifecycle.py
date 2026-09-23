from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from urllib.parse import parse_qsl, urlsplit

import httpx2 as httpx
import pytest

from ci_coordinator.control_plane_identity import KeycloakUnavailable
from ci_coordinator.integrations.keycloak.client import KeycloakIntegration

from ._support import (
    API_CLIENT_ID,
    AUTHORIZATION_ENDPOINT,
    BROWSER_CLIENT_ID,
    DISCOVERY_URL,
    END_SESSION_ENDPOINT,
    ISSUER,
    JWKS_URI,
    OPAQUE,
    POST_LOGOUT_REDIRECT_URI,
    REDIRECT_URI,
    TOKEN_ENDPOINT,
    ManualMonotonicClock,
    discovery_document,
    json_bytes,
    public_jwk,
    response,
    token,
)

Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


def _handler(request: httpx.Request) -> httpx.Response:
    if str(request.url) == DISCOVERY_URL:
        return response(body=discovery_document())
    if str(request.url) == JWKS_URI:
        return response(body=json_bytes({"keys": [public_jwk()]}))
    raise AssertionError(f"unexpected request: {request.method} {request.url}")


def _integration(
    handler: Handler = _handler, *, clock: ManualMonotonicClock | None = None
) -> KeycloakIntegration:
    return KeycloakIntegration(
        issuer=ISSUER,
        browser_client_id=BROWSER_CLIENT_ID,
        browser_client_secret="browser-secret",
        api_client_id=API_CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        post_logout_redirect_uri=POST_LOGOUT_REDIRECT_URI,
        clock=clock if clock is not None else ManualMonotonicClock(),
        transport=httpx.MockTransport(handler),
    )


def test_prepare_loads_discovery_and_jwks_before_exposing_browser_urls() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return _handler(request)

    integration = _integration(handler)

    async def scenario() -> tuple[str, str | None]:
        await integration.prepare()
        authorization = integration.browser.authorization_url(
            state=OPAQUE,
            nonce=OPAQUE,
            code_challenge=OPAQUE,
        )
        logout = await integration.browser.logout_url()
        await integration.aclose()
        with pytest.raises(KeycloakUnavailable):
            await integration.prepare()
        return authorization, logout

    authorization, logout = asyncio.run(scenario())

    assert requests == [DISCOVERY_URL, JWKS_URI]
    parsed = urlsplit(authorization)
    assert parsed._replace(query="") == urlsplit(AUTHORIZATION_ENDPOINT)
    assert dict(parse_qsl(parsed.query, strict_parsing=True)) == {
        "client_id": BROWSER_CLIENT_ID,
        "code_challenge": OPAQUE,
        "code_challenge_method": "S256",
        "nonce": OPAQUE,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid",
        "state": OPAQUE,
    }
    assert logout == (
        f"{END_SESSION_ENDPOINT}?client_id={BROWSER_CLIENT_ID}"
        f"&post_logout_redirect_uri=https%3A%2F%2Fcoordinator.example.test%2F"
    )


def test_prepare_failure_is_retryable_and_never_starts_background_refresh() -> None:
    reject = True

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL and reject:
            return response(503)
        return _handler(request)

    clock = ManualMonotonicClock()
    integration = _integration(handler, clock=clock)

    async def scenario() -> None:
        nonlocal reject
        with pytest.raises(KeycloakUnavailable):
            await integration.prepare()
        assert integration._refresh_task is None
        reject = False
        with pytest.raises(KeycloakUnavailable):
            await integration.prepare()
        assert integration._refresh_task is None
        clock.value = 30.0
        await integration.prepare()
        assert integration._refresh_task is not None
        await integration.aclose()

    asyncio.run(scenario())


def test_browser_code_exchange_composes_discovery_http_signature_and_claim_admission() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_ENDPOINT:
            return response(
                body=json_bytes(
                    {
                        "access_token": "access.canary.value",
                        "expires_in": 60,
                        "id_token": token("id"),
                        "token_type": "Bearer",
                    }
                )
            )
        return _handler(request)

    integration = _integration(handler)

    async def scenario() -> None:
        await integration.prepare()
        evidence = await integration.browser.exchange_code(
            code="authorization-code",
            code_verifier=OPAQUE,
            expected_nonce=OPAQUE,
        )
        assert evidence.subject == "subject-1"
        assert evidence.roles == frozenset({"read", "configure"})
        assert integration._http._client.token is None
        await integration.aclose()

    asyncio.run(scenario())


def test_refresh_loop_uses_failure_backoff_then_restores_proactive_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration = _integration()
    delays: list[float] = []
    refresh_attempts = 0
    original_sleep = asyncio.sleep

    async def controlled_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError
        await original_sleep(0)

    async def refresh_discovery() -> object:
        nonlocal refresh_attempts
        refresh_attempts += 1
        if refresh_attempts == 1:
            raise KeycloakUnavailable("transient")
        return object()

    async def refresh_keys() -> None:
        return None

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(asyncio, "sleep", controlled_sleep)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(integration._discovery, "refresh", refresh_discovery)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(integration._keys, "refresh", refresh_keys)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(integration._refresh_loop())

    assert delays == [240, 30, 240]
    assert refresh_attempts == 2


def test_close_failure_is_retryable_and_closes_each_owner_exactly_once_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration = _integration()
    attempts = 0

    async def close_http() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("transient close failure")

    monkeypatch.setattr(integration._http, "aclose", close_http)

    async def scenario() -> None:
        with pytest.raises(OSError, match="transient close failure"):
            await integration.aclose()
        await integration.aclose()
        await integration.aclose()

    asyncio.run(scenario())
    assert attempts == 2


def test_close_caller_cancellation_does_not_cancel_shared_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration = _integration()

    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def close_http() -> None:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()

        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(integration._http, "aclose", close_http)
        caller = asyncio.create_task(integration.aclose())
        await started.wait()
        peer = asyncio.create_task(integration.aclose())
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await peer
        await integration.aclose()
        assert calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("issuer", "http://auth.example.test/realms/coordinator"),
        ("browser_client_id", ""),
        ("browser_client_secret", ""),
        ("api_client_id", ""),
        ("redirect_uri", "http://coordinator.example.test/callback"),
        ("post_logout_redirect_uri", "https://coordinator.example.test/?query=1"),
    ],
)
def test_integration_constructor_rejects_profile_mutations(field: str, value: str) -> None:
    arguments: dict[str, object] = {
        "issuer": ISSUER,
        "browser_client_id": BROWSER_CLIENT_ID,
        "browser_client_secret": "browser-secret",
        "api_client_id": API_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "post_logout_redirect_uri": POST_LOGOUT_REDIRECT_URI,
        "clock": ManualMonotonicClock(),
        "transport": httpx.MockTransport(_handler),
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        KeycloakIntegration(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", "A" * 42, "A" * 44, "+" * 43])
def test_browser_rejects_noncanonical_oauth_handles_before_provider_use(value: str) -> None:
    integration = _integration()

    with pytest.raises(ValueError, match="canonical 32-byte base64url"):
        integration.browser.authorization_url(
            state=value,
            nonce=OPAQUE,
            code_challenge=OPAQUE,
        )
