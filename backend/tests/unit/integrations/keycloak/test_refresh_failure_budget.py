from __future__ import annotations

import asyncio
import gc
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

import pytest

from ci_coordinator.control_plane_identity import KeycloakUnavailable
from ci_coordinator.integrations.keycloak.discovery import KeycloakDiscovery
from ci_coordinator.integrations.keycloak.jwks import KeycloakJwksCache

from ._support import (
    DISCOVERY_URL,
    ISSUER,
    JWKS_URI,
    KID,
    DocumentClient,
    ManualMonotonicClock,
    discovery_document,
    json_bytes,
    public_jwk,
)

CacheKind = Literal["discovery", "jwks"]
pytestmark = pytest.mark.parametrize("kind", ["discovery", "jwks"])


@dataclass(frozen=True)
class Harness:
    cache: KeycloakDiscovery | KeycloakJwksCache
    discovery: KeycloakDiscovery
    client: DocumentClient
    clock: ManualMonotonicClock


@asynccontextmanager
async def _harness(kind: CacheKind) -> AsyncIterator[Harness]:
    clock = ManualMonotonicClock()
    client = DocumentClient(
        {DISCOVERY_URL: discovery_document(), JWKS_URI: json_bytes({"keys": [public_jwk()]})}
    )
    discovery = KeycloakDiscovery(issuer=ISSUER, http=client, clock=clock)
    cache: KeycloakDiscovery | KeycloakJwksCache = discovery
    try:
        if kind == "jwks":
            await discovery.get()
            cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)
        yield Harness(cache, discovery, client, clock)
    finally:
        await cache.aclose()
        await discovery.aclose()


async def _read(cache: KeycloakDiscovery | KeycloakJwksCache) -> object:
    if isinstance(cache, KeycloakDiscovery):
        return await cache.get()
    return await cache.get_key(KID)


def test_failed_proactive_refresh_preserves_only_current_positive_authority(
    kind: CacheKind,
) -> None:
    async def scenario() -> None:
        async with _harness(kind) as h:
            await h.cache.refresh()
            admitted = await _read(h.cache)
            h.clock.value = 299.0
            if kind == "jwks":
                await h.discovery.refresh()
            h.client.failure = KeycloakUnavailable("offline")
            with pytest.raises(KeycloakUnavailable):
                await h.cache.refresh()
            calls = len(h.client.calls)
            assert await _read(h.cache) is admitted
            h.clock.value = 300.0
            if kind == "jwks":
                assert h.discovery.current().jwks_uri == JWKS_URI
            with pytest.raises(KeycloakUnavailable):
                await _read(h.cache)
            assert len(h.client.calls) == calls

    asyncio.run(scenario())


async def _complete_and_close_orphan(
    h: Harness, release: asyncio.Event, finished: asyncio.Event
) -> None:
    producer = h.cache._refresh_task
    assert producer is not None and not producer.done()
    producer_id = id(producer)
    producer_ref = weakref.ref(producer)
    del producer
    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler()
    unhandled: list[str] = []

    def exception_handler(event_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if id(context.get("future")) == producer_id:
            unhandled.append(str(context.get("message")))
        elif original_handler is None:
            event_loop.default_exception_handler(context)
        else:
            original_handler(event_loop, context)

    loop.set_exception_handler(exception_handler)
    try:
        release.set()
        await finished.wait()
        h.client.failure = None
        assert h.cache._refresh_task is not None and h.cache._refresh_task.done()
        await h.cache.aclose()
        gc.collect()
        assert producer_ref() is None
        assert not unhandled
    finally:
        loop.set_exception_handler(original_handler)


@pytest.mark.parametrize("exit_mode", ["retry", "close"])
def test_all_cancelled_waiters_do_not_erase_the_producer_failure_budget(
    kind: CacheKind, exit_mode: str
) -> None:
    async def scenario() -> None:
        async with asyncio.timeout(10), _harness(kind) as h:
            started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
            h.client.started, h.client.release, h.client.finished = started, release, finished
            h.client.failure = RuntimeError("offline")
            waiter: asyncio.Task[object] | None = asyncio.create_task(h.cache.refresh())
            try:
                await started.wait()
                assert waiter is not None
                waiter.cancel()
                cancelled = await asyncio.gather(waiter, return_exceptions=True)
                assert isinstance(cancelled[0], asyncio.CancelledError)
                waiter = None
                del cancelled
                h.clock.value = 100.0
                calls = len(h.client.calls)
                if exit_mode == "close":
                    await _complete_and_close_orphan(h, release, finished)
                    h.clock.value = 130.0
                    with pytest.raises(KeycloakUnavailable):
                        await h.cache.refresh()
                    assert len(h.client.calls) == calls
                else:
                    release.set()
                    await finished.wait()
                    h.client.failure = None
                    h.clock.value = 129.999
                    with pytest.raises(KeycloakUnavailable):
                        await h.cache.refresh()
                    assert len(h.client.calls) == calls
                    h.clock.value = 130.0
                    await h.cache.refresh()
                    assert len(h.client.calls) == calls + 1
            finally:
                release.set()
                if waiter is not None:
                    if not waiter.done():
                        waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(scenario())


def test_late_failed_waiter_cannot_renew_budget_or_clear_a_new_retry(
    kind: CacheKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async with asyncio.timeout(10), _harness(kind) as h:
            failed_waiter, settle = asyncio.Event(), asyncio.Event()
            original = cast(
                Callable[[asyncio.Task[object]], Awaitable[None]], h.cache._clear_refresh
            )

            async def delayed_clear(task: asyncio.Task[object]) -> None:
                failed_waiter.set()
                await settle.wait()
                await original(task)

            # noinspection PyUnresolvedReferences
            monkeypatch.setattr(h.cache, "_clear_refresh", delayed_clear)
            h.client.failure = RuntimeError("offline")
            first = asyncio.create_task(h.cache.refresh())
            retry: asyncio.Task[object] | None = None
            peer: asyncio.Task[object] | None = None
            release = asyncio.Event()
            try:
                await failed_waiter.wait()
                assert not first.done()
                h.clock.value = 30.0
                h.client.failure = None
                h.client.started, h.client.release = asyncio.Event(), release
                retry = asyncio.create_task(h.cache.refresh())
                await h.client.started.wait()
                calls = len(h.client.calls)
                settle.set()
                with pytest.raises(KeycloakUnavailable):
                    await first
                peer_started = asyncio.Event()

                async def consume_retry() -> object:
                    peer_started.set()
                    return await h.cache.refresh()

                peer = asyncio.create_task(consume_retry())
                await peer_started.wait()
                release.set()
                await asyncio.gather(retry, peer)
                assert len(h.client.calls) == calls
                assert await _read(h.cache) is not None
                await h.cache.refresh()
                assert len(h.client.calls) == calls + 1
            finally:
                settle.set()
                release.set()
                tasks = [task for task in (first, retry, peer) if task is not None]
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
