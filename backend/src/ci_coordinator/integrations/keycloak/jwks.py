"""Bounded single-flight Keycloak JWKS cache."""

from __future__ import annotations

import asyncio
import base64
import binascii
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, cast

from joserfc.errors import JoseError
from joserfc.jwk import RSAKey

from ci_coordinator.control_plane_identity import KeycloakUnavailable
from ci_coordinator.integrations.keycloak._text import bounded_text as _bounded_text
from ci_coordinator.integrations.keycloak.discovery import (
    FAILED_REFRESH_RETRY_SECONDS,
    KeycloakDiscovery,
    KeycloakDocumentClient,
)
from ci_coordinator.kernel import (
    JsonResourceLimits,
    MonotonicClock,
    StrictJsonError,
    load_strict_json,
)

JWKS_CACHE_MAXIMUM_SECONDS = 300
JWKS_MAXIMUM_BYTES = 1_048_576
JWKS_MAXIMUM_KEYS = 64
UNKNOWN_KID_REFRESH_MINIMUM_SECONDS = 30
_JWKS_LIMITS = JsonResourceLimits(max_depth=6, max_nodes=2_048)
_ALGORITHM: Final = "RS256"
_PRIVATE_MEMBERS: Final = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth"})
_MAXIMUM_KID_BYTES = 256
_MAXIMUM_MODULUS_BITS = 8_192
_MINIMUM_MODULUS_BITS = 2_048


@dataclass(frozen=True, slots=True)
class KeycloakSigningKey:
    kid: str
    value: RSAKey = field(repr=False)


@dataclass(frozen=True, slots=True)
class _JwksSnapshot:
    keys: MappingProxyType[str, KeycloakSigningKey]
    jwks_uri: str
    obtained_at: float


class KeycloakJwksCache:
    """Select one exact key and perform at most one fresh load per lookup."""

    def __init__(
        self,
        *,
        discovery: KeycloakDiscovery,
        http: KeycloakDocumentClient,
        clock: MonotonicClock,
    ) -> None:
        self._discovery = discovery
        self._http = http
        self._clock = clock
        self._lock = asyncio.Lock()
        self._snapshot: _JwksSnapshot | None = None
        self._refresh_task: asyncio.Task[_JwksSnapshot] | None = None
        self._retry_at = 0.0
        self._closed = False

    async def get_key(self, kid: str) -> KeycloakSigningKey | None:
        if not _bounded_text(kid, _MAXIMUM_KID_BYTES):
            raise ValueError("Keycloak key id must be bounded non-empty text")
        now = _monotonic_now(self._clock)
        async with self._lock:
            if self._closed:
                raise _unavailable()
            snapshot = self._current_snapshot(now)
            if snapshot is not None:
                key = snapshot.keys.get(kid)
                if key is not None:
                    return key
                if now < snapshot.obtained_at + UNKNOWN_KID_REFRESH_MINIMUM_SECONDS:
                    return None
            task = self._select_refresh()
        snapshot = await self._settle(task)
        return snapshot.keys.get(kid)

    async def prepare(self) -> None:
        """Preload a current set without selecting a caller-supplied key id."""
        now = _monotonic_now(self._clock)
        async with self._lock:
            if self._closed:
                raise _unavailable()
            if self._current_snapshot(now) is not None:
                return
            task = self._select_refresh()
        await self._settle(task)

    async def refresh(self) -> None:
        """Force one coalesced refresh before the current key set expires."""
        async with self._lock:
            if self._closed:
                raise _unavailable()
            task = self._select_refresh()
        await self._settle(task)

    def _select_refresh(self) -> asyncio.Task[_JwksSnapshot]:
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

    async def _settle(self, task: asyncio.Task[_JwksSnapshot]) -> _JwksSnapshot:
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
            snapshot = self._current_snapshot(now)
        if snapshot is None:
            raise _unavailable()
        return snapshot

    async def _clear_refresh(self, task: asyncio.Task[_JwksSnapshot]) -> None:
        async with self._lock:
            if self._refresh_task is task:
                self._refresh_task = None

    async def _load(self) -> _JwksSnapshot:
        try:
            metadata = await self._discovery.get()
            body = await self._http.get_document(
                metadata.jwks_uri,
                maximum_bytes=JWKS_MAXIMUM_BYTES,
            )
            obtained_at = _monotonic_now(self._clock)
            keys = _decode_key_set(body)
            return _JwksSnapshot(keys, metadata.jwks_uri, obtained_at)
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

    def _current_snapshot(self, now: float) -> _JwksSnapshot | None:
        snapshot = self._snapshot
        if snapshot is None or not _is_fresh(snapshot.obtained_at, now):
            return None
        try:
            metadata = self._discovery.current()
        except KeycloakUnavailable:
            return None
        return snapshot if snapshot.jwks_uri == metadata.jwks_uri else None


def _decode_key_set(body: bytes) -> MappingProxyType[str, KeycloakSigningKey]:
    try:
        value = load_strict_json(
            body,
            max_bytes=JWKS_MAXIMUM_BYTES,
            resource_limits=_JWKS_LIMITS,
        )
    except StrictJsonError:
        raise _unavailable() from None
    if type(value) is not dict:
        raise _unavailable()
    raw_keys = cast(dict[str, object], value).get("keys")
    if type(raw_keys) is not list or not 1 <= len(raw_keys) <= JWKS_MAXIMUM_KEYS:
        raise _unavailable()
    admitted: dict[str, KeycloakSigningKey] = {}
    for raw_key in raw_keys:
        if type(raw_key) is not dict:
            continue
        key = _admit_key(cast(dict[str, object], raw_key))
        if key is None:
            continue
        if key.kid in admitted:
            raise _unavailable()
        admitted[key.kid] = key
    if not admitted:
        raise _unavailable()
    return MappingProxyType(admitted)


def _admit_key(value: dict[str, object]) -> KeycloakSigningKey | None:
    if _PRIVATE_MEMBERS.intersection(value):
        raise _unavailable()
    if value.get("kty") != "RSA" or value.get("alg") != _ALGORITHM:
        return None
    if value.get("use") != "sig":
        return None
    key_operations = value.get("key_ops")
    if key_operations is not None and key_operations != ["verify"]:
        return None
    kid = value.get("kid")
    modulus = value.get("n")
    exponent = value.get("e")
    if (
        type(kid) is not str
        or not _bounded_text(kid, _MAXIMUM_KID_BYTES)
        or type(modulus) is not str
        or not 1 <= len(modulus) <= 2_048
        or type(exponent) is not str
        or not 1 <= len(exponent) <= 16
    ):
        return None
    modulus_bytes = _decode_base64url(modulus)
    exponent_bytes = _decode_base64url(exponent)
    if modulus_bytes is None or exponent_bytes is None:
        return None
    modulus_bits = int.from_bytes(modulus_bytes, "big").bit_length()
    exponent_value = int.from_bytes(exponent_bytes, "big")
    if (
        not _MINIMUM_MODULUS_BITS <= modulus_bits <= _MAXIMUM_MODULUS_BITS
        or not 3 <= exponent_value <= 0xFFFF_FFFF
        or exponent_value % 2 == 0
    ):
        return None
    serialized: dict[str, str | list[str]] = {
        "alg": _ALGORITHM,
        "e": exponent,
        "kid": kid,
        "kty": "RSA",
        "n": modulus,
        "use": "sig",
    }
    if key_operations is not None:
        serialized["key_ops"] = ["verify"]
    try:
        imported = RSAKey.import_key(serialized)
    except (JoseError, TypeError, ValueError):
        return None
    if (
        imported.is_private
        or not _MINIMUM_MODULUS_BITS <= imported.public_key.key_size <= _MAXIMUM_MODULUS_BITS
    ):
        return None
    return KeycloakSigningKey(kid, imported)


def _decode_base64url(value: str) -> bytes | None:
    if not value.isascii() or "=" in value:
        return None
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        return None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    return decoded if canonical == value else None


def _monotonic_now(clock: MonotonicClock) -> float:
    value = clock.now()
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise _unavailable()
    return float(value)


def _is_fresh(obtained_at: float, now: float) -> bool:
    return obtained_at <= now < obtained_at + JWKS_CACHE_MAXIMUM_SECONDS


def _unavailable() -> KeycloakUnavailable:
    return KeycloakUnavailable("Keycloak JWKS is unavailable")
