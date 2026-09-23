"""Bounded exact-issuer discovery for the configured Keycloak realm."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import unquote, urlsplit

from ci_coordinator.control_plane_identity import KeycloakUnavailable
from ci_coordinator.control_plane_identity.model import is_canonical_oidc_issuer
from ci_coordinator.integrations.keycloak._text import bounded_text as _bounded_text
from ci_coordinator.kernel import (
    JsonResourceLimits,
    MonotonicClock,
    StrictJsonError,
    load_strict_json,
)

DISCOVERY_CACHE_MAXIMUM_SECONDS = 300
DISCOVERY_MAXIMUM_BYTES = 262_144
FAILED_REFRESH_RETRY_SECONDS = 30
_DISCOVERY_LIMITS = JsonResourceLimits(max_depth=6, max_nodes=512)
_MAXIMUM_ENDPOINT_BYTES = 2_048


class KeycloakDocumentClient(Protocol):
    async def get_document(self, url: str, *, maximum_bytes: int) -> bytes: ...


@dataclass(frozen=True, slots=True)
class KeycloakProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None


@dataclass(frozen=True, slots=True)
class _DiscoverySnapshot:
    metadata: KeycloakProviderMetadata
    obtained_at: float


class KeycloakDiscovery:
    """Cache only current metadata loaded from the issuer's fixed URL."""

    def __init__(
        self,
        *,
        issuer: str,
        http: KeycloakDocumentClient,
        clock: MonotonicClock,
    ) -> None:
        if not is_canonical_oidc_issuer(issuer):
            raise ValueError("Keycloak issuer must be a canonical HTTPS URL")
        self._issuer = issuer
        self._discovery_url = f"{issuer}/.well-known/openid-configuration"
        self._http = http
        self._clock = clock
        self._lock = asyncio.Lock()
        self._snapshot: _DiscoverySnapshot | None = None
        self._refresh_task: asyncio.Task[_DiscoverySnapshot] | None = None
        self._retry_at = 0.0
        self._closed = False

    def current(self) -> KeycloakProviderMetadata:
        """Return only a fresh cache entry; synchronous callers never perform I/O."""
        snapshot = self._snapshot
        now = _monotonic_now(self._clock)
        if self._closed or snapshot is None or not _is_fresh(snapshot.obtained_at, now):
            raise _unavailable()
        return snapshot.metadata

    async def get(self) -> KeycloakProviderMetadata:
        now = _monotonic_now(self._clock)
        async with self._lock:
            if self._closed:
                raise _unavailable()
            snapshot = self._snapshot
            if snapshot is not None and _is_fresh(snapshot.obtained_at, now):
                return snapshot.metadata
            task = self._select_refresh()
        return await self._settle(task)

    async def refresh(self) -> KeycloakProviderMetadata:
        """Force one coalesced refresh before the synchronous cache expires."""
        async with self._lock:
            if self._closed:
                raise _unavailable()
            task = self._select_refresh()
        return await self._settle(task)

    def _select_refresh(self) -> asyncio.Task[_DiscoverySnapshot]:
        """Select under the lock; retrieve orphaned failure before replacing it."""
        task = self._refresh_task
        if task is not None and task.done() and (task.cancelled() or task.exception() is not None):
            task = None
        if task is None:
            if _monotonic_now(self._clock) < self._retry_at:
                raise _unavailable()
            task = asyncio.create_task(self._load())
            self._refresh_task = task
        return task

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._snapshot = None
            task = self._refresh_task
            self._refresh_task = None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except KeycloakUnavailable:
                pass

    async def _settle(
        self,
        task: asyncio.Task[_DiscoverySnapshot],
    ) -> KeycloakProviderMetadata:
        try:
            if not task.done():
                await asyncio.wait((task,))
            loaded = task.result()
        except asyncio.CancelledError:
            raise
        except KeycloakUnavailable:
            await self._clear_refresh(task)
            raise
        except Exception:
            await self._clear_refresh(task)
            raise _unavailable() from None
        now = _monotonic_now(self._clock)
        async with self._lock:
            if self._closed:
                raise _unavailable()
            if self._refresh_task is task:
                self._snapshot = loaded
                self._refresh_task = None
            snapshot = self._snapshot
        if snapshot is None or not _is_fresh(snapshot.obtained_at, now):
            raise _unavailable()
        return snapshot.metadata

    async def _clear_refresh(self, task: asyncio.Task[_DiscoverySnapshot]) -> None:
        async with self._lock:
            if self._refresh_task is task:
                self._refresh_task = None

    async def _load(self) -> _DiscoverySnapshot:
        try:
            body = await self._http.get_document(
                self._discovery_url,
                maximum_bytes=DISCOVERY_MAXIMUM_BYTES,
            )
            obtained_at = _monotonic_now(self._clock)
            return _DiscoverySnapshot(_decode_metadata(body, self._issuer), obtained_at)
        except asyncio.CancelledError:
            if self._closed:
                raise _unavailable() from None
            raise
        except KeycloakUnavailable:
            self._retry_at = _monotonic_now(self._clock) + FAILED_REFRESH_RETRY_SECONDS
            raise
        except Exception:
            self._retry_at = _monotonic_now(self._clock) + FAILED_REFRESH_RETRY_SECONDS
            raise _unavailable() from None


def _decode_metadata(body: bytes, issuer: str) -> KeycloakProviderMetadata:
    try:
        value = load_strict_json(
            body,
            max_bytes=DISCOVERY_MAXIMUM_BYTES,
            resource_limits=_DISCOVERY_LIMITS,
        )
    except StrictJsonError:
        raise _unavailable() from None
    if type(value) is not dict:
        raise _unavailable()
    document = cast(dict[str, object], value)
    if document.get("issuer") != issuer:
        raise _unavailable()
    if not all(
        (
            _supports(document, "response_types_supported", "code"),
            _supports(document, "grant_types_supported", "authorization_code"),
            _supports(document, "code_challenge_methods_supported", "S256"),
            _supports(
                document,
                "token_endpoint_auth_methods_supported",
                "client_secret_basic",
            ),
            _supports(document, "id_token_signing_alg_values_supported", "RS256"),
        )
    ):
        raise _unavailable()
    return KeycloakProviderMetadata(
        issuer=issuer,
        authorization_endpoint=_same_origin_endpoint(
            document.get("authorization_endpoint"), issuer
        ),
        token_endpoint=_same_origin_endpoint(document.get("token_endpoint"), issuer),
        jwks_uri=_same_origin_endpoint(document.get("jwks_uri"), issuer),
        end_session_endpoint=_optional_same_origin_endpoint(
            document.get("end_session_endpoint"), issuer
        ),
    )


def _supports(document: dict[str, object], field: str, required: str) -> bool:
    value = document.get(field)
    return (
        type(value) is list
        and 1 <= len(value) <= 64
        and all(type(item) is str and _bounded_text(item, 256) for item in value)
        and len(set(cast(list[str], value))) == len(value)
        and required in value
    )


def _optional_same_origin_endpoint(value: object, issuer: str) -> str | None:
    if value is None:
        return None
    return _same_origin_endpoint(value, issuer)


def _same_origin_endpoint(value: object, issuer: str) -> str:
    if type(value) is not str or not _bounded_text(value, _MAXIMUM_ENDPOINT_BYTES):
        raise _unavailable()
    try:
        endpoint = urlsplit(value)
        configured = urlsplit(issuer)
        endpoint_port = endpoint.port
        decoded_path = unquote(endpoint.path, errors="strict")
    except (UnicodeError, ValueError):
        raise _unavailable() from None
    if (
        endpoint.scheme != "https"
        or endpoint.hostname is None
        or endpoint.username is not None
        or endpoint.password is not None
        or endpoint.query
        or endpoint.fragment
        or not endpoint.path.startswith("/")
        or "\\" in endpoint.path
        or any(ord(character) < 0x20 for character in decoded_path)
        or endpoint_port in {0, 443}
        or endpoint.netloc != configured.netloc
        or value != f"https://{endpoint.netloc}{endpoint.path}"
    ):
        raise _unavailable()
    return value


def _monotonic_now(clock: MonotonicClock) -> float:
    value = clock.now()
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise _unavailable()
    return float(value)


def _is_fresh(obtained_at: float, now: float) -> bool:
    return obtained_at <= now < obtained_at + DISCOVERY_CACHE_MAXIMUM_SECONDS


def _unavailable() -> KeycloakUnavailable:
    return KeycloakUnavailable("Keycloak discovery is unavailable")
