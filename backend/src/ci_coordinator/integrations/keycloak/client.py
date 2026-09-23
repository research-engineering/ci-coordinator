"""Keycloak browser port and integration composition."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Final, cast
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx2 as httpx

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutEvidence,
    KeycloakBrowserEvidence,
    KeycloakUnavailable,
)
from ci_coordinator.control_plane_identity.model import is_canonical_oidc_issuer
from ci_coordinator.integrations.keycloak._http import _KeycloakHttpClient
from ci_coordinator.integrations.keycloak._text import bounded_text as _bounded_text
from ci_coordinator.integrations.keycloak.discovery import (
    FAILED_REFRESH_RETRY_SECONDS,
    KeycloakDiscovery,
)
from ci_coordinator.integrations.keycloak.jwks import KeycloakJwksCache
from ci_coordinator.integrations.keycloak.tokens import KeycloakTokenVerifier
from ci_coordinator.kernel import MonotonicClock

_URL_SAFE: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
_PROACTIVE_REFRESH_SECONDS = 240


@dataclass(frozen=True, slots=True)
class _BrowserConfiguration:
    client_id: str
    redirect_uri: str
    post_logout_redirect_uri: str


class KeycloakBrowserClient:
    def __init__(
        self,
        *,
        configuration: _BrowserConfiguration,
        discovery: KeycloakDiscovery,
        http: _KeycloakHttpClient,
        tokens: KeycloakTokenVerifier,
    ) -> None:
        self._configuration = configuration
        self._discovery = discovery
        self._http = http
        self._tokens = tokens

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        _opaque_value(state, "OAuth state")
        _opaque_value(nonce, "OIDC nonce")
        _opaque_value(code_challenge, "PKCE challenge")
        if not self._http.is_open:
            raise _unavailable()
        metadata = self._discovery.current()
        generated = cast(
            object,
            self._http.oauth.create_authorization_url(
                metadata.authorization_endpoint,
                state=state,
                nonce=nonce,
                code_challenge=code_challenge,
                code_challenge_method="S256",
            ),
        )
        if type(generated) is not tuple or len(generated) != 2:
            raise _unavailable()
        url, returned_state = generated
        if (
            type(url) is not str
            or type(returned_state) is not str
            or returned_state != state
            or not _authorization_url_is_exact(
                url,
                metadata.authorization_endpoint,
                state=state,
                nonce=nonce,
                code_challenge=code_challenge,
                configuration=self._configuration,
            )
        ):
            raise _unavailable()
        return url

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> KeycloakBrowserEvidence:
        _bounded_required_text(code, "authorization code", 1_024)
        _opaque_value(code_verifier, "PKCE verifier")
        _opaque_value(expected_nonce, "OIDC nonce")
        metadata = await self._discovery.get()
        id_token = await self._http.exchange_code(
            token_endpoint=metadata.token_endpoint,
            code=code,
            code_verifier=code_verifier,
        )
        try:
            return await self._tokens.verify_browser_id_token(
                id_token,
                expected_nonce=expected_nonce,
            )
        finally:
            id_token = ""

    async def logout_url(self) -> str | None:
        metadata = await self._discovery.get()
        endpoint = metadata.end_session_endpoint
        if endpoint is None:
            return None
        query = urlencode(
            {
                "client_id": self._configuration.client_id,
                "post_logout_redirect_uri": self._configuration.post_logout_redirect_uri,
            }
        )
        value = f"{endpoint}?{query}"
        if len(value.encode("ascii")) > 2_048:
            raise _unavailable()
        return value


class KeycloakIntegration:
    """Own one Authlib client shared by discovery, JWKS, and code exchange."""

    def __init__(
        self,
        *,
        issuer: str,
        browser_client_id: str,
        browser_client_secret: str,
        api_client_id: str,
        redirect_uri: str,
        post_logout_redirect_uri: str,
        clock: MonotonicClock,
        transport: httpx.AsyncBaseTransport | None = None,
        outbound_proxy_url: str | None = None,
    ) -> None:
        if not is_canonical_oidc_issuer(issuer):
            raise ValueError("Keycloak issuer must be a canonical HTTPS URL")
        _bounded_required_text(browser_client_id, "browser client id", 256)
        _bounded_required_text(browser_client_secret, "browser client secret", 4_096)
        _bounded_required_text(api_client_id, "API client id", 256)
        _redirect_uri(redirect_uri, "browser redirect URI")
        _redirect_uri(post_logout_redirect_uri, "post-logout redirect URI")
        configuration = _BrowserConfiguration(
            browser_client_id,
            redirect_uri,
            post_logout_redirect_uri,
        )
        self._http = _KeycloakHttpClient(
            client_id=browser_client_id,
            client_secret=browser_client_secret,
            redirect_uri=redirect_uri,
            transport=transport,
            outbound_proxy_url=outbound_proxy_url,
        )
        self._discovery = KeycloakDiscovery(issuer=issuer, http=self._http, clock=clock)
        self._keys = KeycloakJwksCache(
            discovery=self._discovery,
            http=self._http,
            clock=clock,
        )
        self._tokens = KeycloakTokenVerifier(
            issuer=issuer,
            browser_client_id=browser_client_id,
            api_client_id=api_client_id,
            keys=self._keys,
        )
        self._browser = KeycloakBrowserClient(
            configuration=configuration,
            discovery=self._discovery,
            http=self._http,
            tokens=self._tokens,
        )
        self._lifecycle_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._close_started = False
        self._closed = False

    @property
    def browser(self) -> KeycloakBrowserClient:
        return self._browser

    @property
    def machine(self) -> KeycloakTokenVerifier:
        return self._tokens

    async def prepare(self) -> None:
        """Load fresh discovery and keys before exposing synchronous login start."""
        async with self._lifecycle_lock:
            if self._closed or self._close_started:
                raise _unavailable()
            if self._refresh_task is not None:
                return
            await self._discovery.get()
            await self._keys.prepare()
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def verify_back_channel_logout(
        self,
        token: str,
    ) -> BackChannelLogoutEvidence:
        return await self._tokens.verify_logout_token(token)

    async def aclose(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._close_started = True
            close_task = self._close_task
            if close_task is None:
                close_task = asyncio.create_task(self._close_all())
                self._close_task = close_task
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            if close_task.cancelled():
                async with self._lifecycle_lock:
                    if self._close_task is close_task:
                        self._close_task = None
            raise
        except BaseException:
            async with self._lifecycle_lock:
                if self._close_task is close_task:
                    self._close_task = None
            raise
        async with self._lifecycle_lock:
            self._closed = True

    async def _close_all(self) -> None:
        refresh_task = self._refresh_task
        self._refresh_task = None
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
        try:
            await self._keys.aclose()
        finally:
            try:
                await self._discovery.aclose()
            finally:
                await self._http.aclose()

    async def _refresh_loop(self) -> None:
        delay = _PROACTIVE_REFRESH_SECONDS
        while True:
            await asyncio.sleep(delay)
            try:
                await self._discovery.refresh()
                await self._keys.refresh()
            except asyncio.CancelledError:
                raise
            except KeycloakUnavailable:
                delay = FAILED_REFRESH_RETRY_SECONDS
            else:
                delay = _PROACTIVE_REFRESH_SECONDS


def _authorization_url_is_exact(
    value: str,
    endpoint: str,
    *,
    state: str,
    nonce: str,
    code_challenge: str,
    configuration: _BrowserConfiguration,
) -> bool:
    if type(value) is not str or len(value.encode("utf-8")) > 2_048:
        return False
    parsed = urlsplit(value)
    endpoint_parsed = urlsplit(endpoint)
    if parsed._replace(query="", fragment="") != endpoint_parsed:
        return False
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    expected = {
        "client_id": configuration.client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,
        "redirect_uri": configuration.redirect_uri,
        "response_type": "code",
        "scope": "openid",
        "state": state,
    }
    return len(pairs) == len(expected) and dict(pairs) == expected


def _redirect_uri(value: object, name: str) -> None:
    _bounded_required_text(value, name, 2_048)
    parsed = urlsplit(cast(str, value))
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise ValueError(f"{name} must be an exact HTTPS URL")


def _opaque_value(value: object, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 43
        or any(character not in _URL_SAFE for character in value)
    ):
        raise ValueError(f"{name} must be canonical 32-byte base64url")


def _bounded_required_text(value: object, name: str, maximum_bytes: int) -> None:
    if type(value) is not str or not _bounded_text(value, maximum_bytes):
        raise ValueError(f"{name} must be bounded non-empty text")


def _unavailable() -> KeycloakUnavailable:
    return KeycloakUnavailable("Keycloak dependency is unavailable")
