"""Expiry-safe single-flight cache for admitted GitHub Actions JWK sets."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from ci_coordinator.identity_admission.jwks_contracts import (
    JwksFetchResponse,
    JwksProviderConfig,
    JwksTransport,
    JwksTransportFailure,
    JwksUnavailable,
    JwksUnavailableKind,
)
from ci_coordinator.identity_admission.oidc_verifier import (
    MAXIMUM_ACTIONS_OIDC_JWKS_KEYS,
    ActionsOidcJwkSet,
)
from ci_coordinator.kernel import MonotonicClock, StrictJsonError, load_strict_json


@dataclass(frozen=True, slots=True)
class _Snapshot:
    key_set: ActionsOidcJwkSet
    obtained_at: float


class CachingJwksProvider:
    """Return only a current matching key set; stale keys are never a fallback."""

    def __init__(
        self,
        transport: JwksTransport,
        clock: MonotonicClock,
        config: JwksProviderConfig,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._config = config
        self._lock = asyncio.Lock()
        self._snapshot: _Snapshot | None = None
        self._refresh_task: asyncio.Task[_Snapshot | JwksUnavailable] | None = None
        self._refresh_allowed_at = 0.0
        self._closed = False

    async def get_key_set(self, key_id: str) -> ActionsOidcJwkSet | JwksUnavailable:
        if type(key_id) is not str or not key_id:
            raise ValueError("JWKS lookup requires an admitted key id")
        now = self._clock.now()
        async with self._lock:
            if self._closed:
                return _unavailable("fetch_unavailable", "JWKS provider is closed")
            snapshot = self._matching_snapshot(key_id, now)
            if snapshot is not None:
                return snapshot.key_set
            task = self._refresh_task
            if task is None:
                if now < self._refresh_allowed_at:
                    return _unavailable("refresh_throttled", "JWKS refresh is throttled")
                task = asyncio.create_task(self._refresh())
                self._refresh_task = task
        refresh_result = await asyncio.shield(task)
        return await self._settle_refresh(task, refresh_result, key_id)

    async def probe(self) -> JwksUnavailable | None:
        """Refresh only when needed and report endpoint/cache availability."""
        now = self._clock.now()
        async with self._lock:
            if self._closed:
                return _unavailable("fetch_unavailable", "JWKS provider is closed")
            if self._has_current_snapshot(now):
                return None
            task = self._refresh_task
            if task is None:
                if now < self._refresh_allowed_at:
                    return _unavailable("refresh_throttled", "JWKS refresh is throttled")
                task = asyncio.create_task(self._refresh())
                self._refresh_task = task
        refresh_result = await asyncio.shield(task)
        await self._publish_refresh(task, refresh_result)
        async with self._lock:
            if self._has_current_snapshot(self._clock.now()):
                return None
        if isinstance(refresh_result, JwksUnavailable):
            return refresh_result
        return _unavailable("fetch_unavailable", "JWKS snapshot is no longer current")

    async def aclose(self) -> None:
        """Stop the shared refresh owner and reject every later lookup."""
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
            with suppress(asyncio.CancelledError):
                await task

    async def _settle_refresh(
        self,
        task: asyncio.Task[_Snapshot | JwksUnavailable],
        refresh_result: _Snapshot | JwksUnavailable,
        key_id: str,
    ) -> ActionsOidcJwkSet | JwksUnavailable:
        await self._publish_refresh(task, refresh_result)
        now = self._clock.now()
        async with self._lock:
            snapshot = self._matching_snapshot(key_id, now)
            if snapshot is not None:
                return snapshot.key_set
        if isinstance(refresh_result, JwksUnavailable):
            return refresh_result
        return _unavailable("signing_key_unavailable", "OIDC signing key is unavailable")

    async def _publish_refresh(
        self,
        task: asyncio.Task[_Snapshot | JwksUnavailable],
        refresh_result: _Snapshot | JwksUnavailable,
    ) -> None:
        now = self._clock.now()
        async with self._lock:
            if self._closed or self._refresh_task is not task:
                return
            self._refresh_task = None
            if isinstance(refresh_result, _Snapshot):
                self._snapshot = refresh_result
                self._refresh_allowed_at = (
                    refresh_result.obtained_at + self._config.minimum_refresh_interval_seconds
                )
            else:
                self._refresh_allowed_at = now + self._config.failure_backoff_seconds

    def _matching_snapshot(self, key_id: str, now: float) -> _Snapshot | None:
        snapshot = self._snapshot
        if snapshot is None or not (
            snapshot.obtained_at
            <= now
            < snapshot.obtained_at + self._config.maximum_cache_age_seconds
        ):
            return None
        return snapshot if any(key.get("kid") == key_id for key in snapshot.key_set.keys) else None

    def _has_current_snapshot(self, now: float) -> bool:
        snapshot = self._snapshot
        return (
            snapshot is not None
            and snapshot.obtained_at
            <= now
            < snapshot.obtained_at + self._config.maximum_cache_age_seconds
        )

    async def _refresh(self) -> _Snapshot | JwksUnavailable:
        try:
            result = await self._transport.fetch()
            obtained_at = self._clock.now()
        except asyncio.CancelledError:
            return _unavailable("fetch_cancelled", "JWKS fetch was cancelled")
        except Exception:
            return _unavailable("fetch_unavailable", "JWKS fetch is unavailable")
        if isinstance(result, JwksTransportFailure):
            return _transport_failure(result)
        key_set = _decode_key_set(result, self._config)
        if isinstance(key_set, JwksUnavailable):
            return key_set
        return _Snapshot(key_set, obtained_at)


def _decode_key_set(
    response: JwksFetchResponse,
    config: JwksProviderConfig,
) -> ActionsOidcJwkSet | JwksUnavailable:
    if 300 <= response.status < 400:
        return _unavailable("response_redirected", "JWKS response must not redirect")
    if response.status != 200:
        return _unavailable("fetch_unavailable", "JWKS endpoint returned a non-success status")
    if len(response.body) > config.maximum_response_bytes:
        return _unavailable("response_oversize", "JWKS response exceeds the byte bound")
    if response.content_type is None or not response.content_type.lower().startswith(
        config.required_content_type_prefix
    ):
        return _unavailable("response_invalid", "JWKS response content type is invalid")
    try:
        document = load_strict_json(
            response.body,
            max_bytes=config.maximum_response_bytes,
        )
    except StrictJsonError:
        return _unavailable("response_invalid", "JWKS response is not valid JSON")
    if type(document) is not dict:
        return _unavailable("response_invalid", "JWKS response has no key array")
    admitted_document = cast(dict[str, object], document)
    raw_keys_value = admitted_document.get("keys")
    if type(raw_keys_value) is not list:
        return _unavailable("response_invalid", "JWKS response has no key array")
    raw_keys = cast(list[object], raw_keys_value)
    if len(raw_keys) > MAXIMUM_ACTIONS_OIDC_JWKS_KEYS:
        return _unavailable("response_invalid", "JWKS response exceeds the key-count bound")
    object_keys = tuple(
        cast(Mapping[str, object], value) for value in raw_keys if type(value) is dict
    )
    try:
        return ActionsOidcJwkSet(object_keys)
    except ValueError:
        return _unavailable("response_invalid", "JWKS key entries are not admitted")


def _transport_failure(failure: JwksTransportFailure) -> JwksUnavailable:
    match failure.kind:
        case "cancelled":
            return _unavailable("fetch_cancelled", "JWKS fetch was cancelled")
        case "redirected":
            return _unavailable("response_redirected", "JWKS response must not redirect")
        case "timeout":
            return _unavailable("fetch_timeout", "JWKS fetch timed out")
        case "response_oversize":
            return _unavailable("response_oversize", "JWKS response exceeds the byte bound")
        case "unavailable":
            return _unavailable("fetch_unavailable", "JWKS fetch is unavailable")


def _unavailable(kind: JwksUnavailableKind, message: str) -> JwksUnavailable:
    return JwksUnavailable(kind=kind, message=message)
