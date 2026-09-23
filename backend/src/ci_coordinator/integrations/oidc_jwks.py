"""HTTPX transport for the fixed GitHub Actions JWKS endpoint."""

from __future__ import annotations

import asyncio

import httpx2 as httpx

from ci_coordinator.identity_admission.jwks_contracts import (
    JwksFetchResponse,
    JwksProviderConfig,
    JwksTransportFailure,
    JwksUnavailable,
)
from ci_coordinator.identity_admission.jwks_provider import CachingJwksProvider
from ci_coordinator.identity_admission.oidc_verifier import ActionsOidcJwkSet
from ci_coordinator.kernel import MonotonicClock

GITHUB_ACTIONS_JWKS_URI = "https://token.actions.githubusercontent.com/.well-known/jwks"
GITHUB_ACTIONS_JWKS_TIMEOUT_SECONDS = 5
GITHUB_ACTIONS_JWKS_CONFIG = JwksProviderConfig(
    maximum_cache_age_seconds=300,
    minimum_refresh_interval_seconds=30,
    failure_backoff_seconds=30,
    maximum_response_bytes=1024 * 1024,
)


class HttpxJwksTransport:
    """Read one fixed endpoint without redirects or unbounded body materialization."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        maximum_response_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
        outbound_proxy_url: str | None = None,
    ) -> None:
        if type(timeout_seconds) not in {int, float} or timeout_seconds <= 0:
            raise ValueError("JWKS timeout must be positive")
        if type(maximum_response_bytes) is not int or maximum_response_bytes < 1:
            raise ValueError("JWKS response byte bound must be positive")
        if transport is not None and outbound_proxy_url is not None:
            raise ValueError("JWKS transport and outbound proxy are mutually exclusive")
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            headers={"Accept": "application/json"},
            timeout=timeout_seconds,
            transport=transport,
            proxy=outbound_proxy_url,
            trust_env=False,
            verify=True,
        )
        self._timeout_seconds = timeout_seconds
        self._maximum_response_bytes = maximum_response_bytes

    async def fetch(self) -> JwksFetchResponse | JwksTransportFailure:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._client.stream(
                    "GET",
                    GITHUB_ACTIONS_JWKS_URI,
                    follow_redirects=False,
                    timeout=self._timeout_seconds,
                ) as response:
                    if 300 <= response.status_code < 400:
                        return JwksTransportFailure(
                            kind="redirected",
                            message="JWKS endpoint returned a redirect",
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if len(body) + len(chunk) > self._maximum_response_bytes:
                            return JwksTransportFailure(
                                kind="response_oversize",
                                message="JWKS response exceeds the byte bound",
                            )
                        body.extend(chunk)
                    return JwksFetchResponse(
                        status=response.status_code,
                        content_type=response.headers.get("content-type"),
                        body=bytes(body),
                    )
        except (TimeoutError, httpx.TimeoutException):
            return JwksTransportFailure(kind="timeout", message="JWKS fetch timed out")
        except httpx.HTTPError:
            return JwksTransportFailure(kind="unavailable", message="JWKS fetch is unavailable")

    async def aclose(self) -> None:
        await self._client.aclose()


class GitHubActionsJwksProvider:
    """Lifecycle-owning composition of the fixed transport and cache profile."""

    def __init__(
        self,
        clock: MonotonicClock,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        outbound_proxy_url: str | None = None,
    ) -> None:
        self._transport = HttpxJwksTransport(
            timeout_seconds=GITHUB_ACTIONS_JWKS_TIMEOUT_SECONDS,
            maximum_response_bytes=GITHUB_ACTIONS_JWKS_CONFIG.maximum_response_bytes,
            transport=transport,
            outbound_proxy_url=outbound_proxy_url,
        )
        self._cache = CachingJwksProvider(
            transport=self._transport,
            clock=clock,
            config=GITHUB_ACTIONS_JWKS_CONFIG,
        )

    async def get_key_set(self, key_id: str) -> ActionsOidcJwkSet | JwksUnavailable:
        return await self._cache.get_key_set(key_id)

    async def probe(self) -> bool:
        """Return whether a current admitted key set can authenticate requests."""
        return await self._cache.probe() is None

    async def aclose(self) -> None:
        try:
            await self._cache.aclose()
        finally:
            await self._transport.aclose()
