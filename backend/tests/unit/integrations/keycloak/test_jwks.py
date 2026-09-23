from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from math import inf, nan
from types import MappingProxyType
from typing import Literal

import pytest

from ci_coordinator.control_plane_identity import KeycloakUnavailable
from ci_coordinator.integrations.keycloak import jwks as jwks_module
from ci_coordinator.integrations.keycloak.discovery import (
    FAILED_REFRESH_RETRY_SECONDS,
    KeycloakDiscovery,
)
from ci_coordinator.integrations.keycloak.jwks import (
    JWKS_CACHE_MAXIMUM_SECONDS,
    JWKS_MAXIMUM_BYTES,
    JWKS_MAXIMUM_KEYS,
    UNKNOWN_KID_REFRESH_MINIMUM_SECONDS,
    KeycloakJwksCache,
    KeycloakSigningKey,
    _decode_key_set,
)

from ._support import (
    DISCOVERY_URL,
    JWKS_URI,
    KID,
    OTHER_PRIVATE_KEY,
    DocumentClient,
    ManualMonotonicClock,
    discovery_document,
    json_bytes,
    metadata,
    private_jwk_member,
    public_jwk,
)


async def _lookup(
    cache: KeycloakJwksCache, entrypoint: Literal["get_key", "prepare", "refresh"]
) -> None:
    if entrypoint == "get_key":
        assert await cache.get_key(KID) is not None
    elif entrypoint == "prepare":
        await cache.prepare()
    else:
        await cache.refresh()


@pytest.mark.parametrize("entrypoint", ["get_key", "prepare", "refresh"])
@pytest.mark.parametrize("recovery_delay", [30.0, 30.001])
def test_jwks_recovers_after_a_failed_refresh(
    entrypoint: Literal["get_key", "prepare", "refresh"],
    recovery_delay: float,
) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        client = DocumentClient(
            {DISCOVERY_URL: discovery_document(), JWKS_URI: json_bytes({"keys": [public_jwk()]})}
        )
        discovery = KeycloakDiscovery(issuer=discovery_document_issuer(), http=client, clock=clock)
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)
        try:
            await discovery.get()
            client.failure = RuntimeError("offline")
            with pytest.raises(KeycloakUnavailable):
                await _lookup(cache, entrypoint)
            assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 1
            client.failure = None
            for now in (0.0, FAILED_REFRESH_RETRY_SECONDS - 0.001):
                clock.value = now
                with pytest.raises(KeycloakUnavailable):
                    await _lookup(cache, entrypoint)
                assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 1
            clock.value = recovery_delay
            await _lookup(cache, entrypoint)
            assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 2
        finally:
            await cache.aclose()
            await discovery.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["get_key", "prepare", "refresh"])
def test_jwks_rechecks_age_for_each_surviving_waiter(
    entrypoint: Literal["get_key", "prepare", "refresh"], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        client = DocumentClient(
            {DISCOVERY_URL: discovery_document(), JWKS_URI: json_bytes({"keys": [public_jwk()]})}
        )
        discovery = KeycloakDiscovery(issuer=discovery_document_issuer(), http=client, clock=clock)
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)
        second_started = asyncio.Event()

        async def consume(*, second: bool = False) -> bool:
            if second:
                second_started.set()
            try:
                await _lookup(cache, entrypoint)
            except KeycloakUnavailable:
                return False
            clock.value = 300.0
            return True

        try:
            await discovery.get()
            monkeypatch.setattr(discovery, "current", metadata)
            started, release = asyncio.Event(), asyncio.Event()
            client.started, client.release = started, release
            first = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 5)
            second = asyncio.create_task(consume(second=True))
            await asyncio.wait_for(second_started.wait(), 5)
            release.set()
            assert sorted(await asyncio.wait_for(asyncio.gather(first, second), 5)) == [False, True]
            assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 1
        finally:
            await cache.aclose()
            await discovery.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["get_key", "prepare", "refresh"])
def test_jwks_decode_elapsed_time_consumes_response_lifetime(
    entrypoint: Literal["get_key", "prepare", "refresh"], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        client = DocumentClient(
            {DISCOVERY_URL: discovery_document(), JWKS_URI: json_bytes({"keys": [public_jwk()]})}
        )
        discovery = KeycloakDiscovery(issuer=discovery_document_issuer(), http=client, clock=clock)
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)
        original = jwks_module._decode_key_set

        def decode(body: bytes) -> MappingProxyType[str, KeycloakSigningKey]:
            result = original(body)
            clock.value = 300.0
            return result

        try:
            await discovery.get()
            monkeypatch.setattr(discovery, "current", metadata)
            monkeypatch.setattr(jwks_module, "_decode_key_set", decode)
            with pytest.raises(KeycloakUnavailable):
                await _lookup(cache, entrypoint)
            assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 1
        finally:
            await cache.aclose()
            await discovery.aclose()

    asyncio.run(scenario())


def _decode(keys: list[object]) -> MappingProxyType[str, KeycloakSigningKey]:
    return _decode_key_set(json_bytes({"keys": keys}))


def test_jwks_admits_one_canonical_public_rsa_verification_key() -> None:
    admitted = _decode([public_jwk()])

    assert tuple(admitted) == (KID,)
    key = admitted[KID]
    assert key.kid == KID
    assert key.value.is_private is False
    assert key.value.public_key.key_size == 2048


@pytest.mark.parametrize("member", ["d", "p", "q", "dp", "dq", "qi", "oth"])
def test_jwks_rejects_any_private_key_member(member: str) -> None:
    value = public_jwk()
    value[member] = private_jwk_member(member)

    with pytest.raises(KeycloakUnavailable):
        _decode([value])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kty", "EC"),
        ("alg", "PS256"),
        ("use", "enc"),
        ("key_ops", ["sign"]),
        ("key_ops", ["verify", "sign"]),
        ("kid", ""),
        ("kid", "x" * 257),
        ("n", "AQ"),
        ("n", "AQ=="),
        ("n", "+/8"),
        ("e", "Ag"),
        ("e", "AQ"),
        ("e", "AQAB="),
        ("e", "x" * 17),
    ],
)
def test_jwks_discards_unsupported_or_noncanonical_keys(field: str, value: object) -> None:
    key = public_jwk()
    key[field] = value

    with pytest.raises(KeycloakUnavailable):
        _decode([key])


def test_jwks_rejects_duplicate_admitted_kid_even_for_distinct_members() -> None:
    first = public_jwk()
    second = deepcopy(first)
    second.pop("key_ops")

    with pytest.raises(KeycloakUnavailable):
        _decode([first, second])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kty", "EC"),
        ("alg", "PS256"),
        ("use", "enc"),
        ("key_ops", ["sign"]),
        ("n", "AQ"),
    ],
)
def test_jwks_ignores_unsupported_public_entries_when_one_valid_key_remains(
    field: str,
    value: object,
) -> None:
    unsupported = public_jwk(kid="unsupported")
    unsupported[field] = value

    assert tuple(_decode([unsupported, public_jwk()])) == (KID,)


@pytest.mark.parametrize(
    "keys",
    [
        [],
        [public_jwk(kid=str(index)) for index in range(JWKS_MAXIMUM_KEYS + 1)],
        [1],
        [{"kty": "EC"}],
    ],
    ids=["empty", "over-cardinality", "non-object", "no-admitted-key"],
)
def test_jwks_rejects_unbounded_or_empty_admitted_sets(keys: list[object]) -> None:
    with pytest.raises(KeycloakUnavailable):
        _decode(keys)


def test_unknown_kid_refresh_is_throttled_and_then_rotates_once() -> None:
    clock = ManualMonotonicClock()
    client = DocumentClient(
        {
            DISCOVERY_URL: discovery_document(),
            JWKS_URI: json_bytes({"keys": [public_jwk(kid="old")]}),
        }
    )
    discovery = KeycloakDiscovery(issuer=discovery_document_issuer(), http=client, clock=clock)
    cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)

    async def scenario() -> None:
        await cache.prepare()
        calls_after_prepare = len(client.calls)
        assert await cache.get_key("new") is None
        assert len(client.calls) == calls_after_prepare

        clock.value = UNKNOWN_KID_REFRESH_MINIMUM_SECONDS
        client.documents[JWKS_URI] = json_bytes({"keys": [public_jwk(kid="new")]})
        selected = await cache.get_key("new")
        assert selected is not None and selected.kid == "new"
        calls_after_rotation = len(client.calls)
        assert await cache.get_key("missing") is None
        assert len(client.calls) == calls_after_rotation

        await cache.aclose()
        await discovery.aclose()

    asyncio.run(scenario())

    assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 2


def test_jwks_refresh_is_single_flight_for_concurrent_unknown_kids() -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        client = DocumentClient(
            {
                DISCOVERY_URL: discovery_document(),
                JWKS_URI: json_bytes({"keys": [public_jwk(kid="old")]}),
            }
        )
        discovery = KeycloakDiscovery(
            issuer=discovery_document_issuer(),
            http=client,
            clock=clock,
        )
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)
        await cache.prepare()
        clock.value = UNKNOWN_KID_REFRESH_MINIMUM_SECONDS
        client.documents[JWKS_URI] = json_bytes({"keys": [public_jwk(kid="new")]})
        client.started = asyncio.Event()
        client.release = asyncio.Event()

        first = asyncio.create_task(cache.get_key("new"))
        await client.started.wait()
        second = asyncio.create_task(cache.get_key("new"))
        client.release.set()
        first_key = await first
        second_key = await second
        assert first_key is not None and first_key.kid == "new"
        assert second_key is not None and second_key.kid == "new"
        assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 2
        await cache.aclose()
        await discovery.aclose()

    asyncio.run(scenario())


def test_jwks_single_flight_survives_one_waiter_cancellation() -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        started = asyncio.Event()
        release = asyncio.Event()
        client = DocumentClient(
            {
                DISCOVERY_URL: discovery_document(),
                JWKS_URI: json_bytes({"keys": [public_jwk()]}),
            },
            started=started,
            release=release,
        )
        discovery = KeycloakDiscovery(
            issuer=discovery_document_issuer(),
            http=client,
            clock=clock,
        )
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)

        first = asyncio.create_task(cache.prepare())
        await started.wait()
        second = asyncio.create_task(cache.prepare())
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        await second
        assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 1
        await cache.aclose()
        await discovery.aclose()

    asyncio.run(scenario())


def test_jwks_close_cancels_refresh_and_invalidates_snapshot() -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        client = DocumentClient(
            {
                DISCOVERY_URL: discovery_document(),
                JWKS_URI: json_bytes({"keys": [public_jwk()]}),
            }
        )
        discovery = KeycloakDiscovery(
            issuer=discovery_document_issuer(),
            http=client,
            clock=clock,
        )
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)
        await cache.prepare()
        client.started = asyncio.Event()
        client.release = asyncio.Event()
        refresh = asyncio.create_task(cache.refresh())
        await client.started.wait()
        await cache.aclose()
        with pytest.raises(KeycloakUnavailable):
            await refresh
        with pytest.raises(KeycloakUnavailable):
            await cache.get_key(KID)
        await discovery.aclose()

    asyncio.run(scenario())


def test_jwks_expiry_and_invalid_clock_fail_closed() -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        client = DocumentClient(
            {
                DISCOVERY_URL: discovery_document(),
                JWKS_URI: json_bytes({"keys": [public_jwk()]}),
            }
        )
        discovery = KeycloakDiscovery(
            issuer=discovery_document_issuer(),
            http=client,
            clock=clock,
        )
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)
        await cache.prepare()

        clock.value = JWKS_CACHE_MAXIMUM_SECONDS
        client.failure = KeycloakUnavailable("offline")
        with pytest.raises(KeycloakUnavailable):
            await cache.get_key(KID)

        for invalid in (-1.0, inf, -inf, nan):
            clock.value = invalid
            with pytest.raises(KeycloakUnavailable):
                await cache.get_key(KID)

        await cache.aclose()
        await discovery.aclose()

    asyncio.run(scenario())


def test_discovery_jwks_uri_change_invalidates_the_old_key_snapshot() -> None:
    async def scenario() -> None:
        new_uri = f"{JWKS_URI}-v2"
        clock = ManualMonotonicClock()
        client = DocumentClient(
            {
                DISCOVERY_URL: discovery_document(),
                JWKS_URI: json_bytes({"keys": [public_jwk(kid="old")]}),
                new_uri: json_bytes({"keys": [public_jwk(kid="new")]}),
            }
        )
        discovery = KeycloakDiscovery(
            issuer=discovery_document_issuer(),
            http=client,
            clock=clock,
        )
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)
        await cache.prepare()
        client.documents[DISCOVERY_URL] = discovery_document(jwks_uri=new_uri)
        await discovery.refresh()

        selected = await cache.get_key("new")
        assert selected is not None and selected.kid == "new"
        assert await cache.get_key("old") is None

        await cache.aclose()
        await discovery.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["get_key", "prepare", "refresh"])
@pytest.mark.parametrize("age", [299.0, 300.0, 301.0])
def test_detached_jwks_retains_response_age_and_recovers(
    entrypoint: Literal["get_key", "prepare", "refresh"],
    age: float,
) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        client = DocumentClient(
            {
                DISCOVERY_URL: discovery_document(),
                JWKS_URI: json_bytes({"keys": [public_jwk()]}),
            }
        )
        discovery = KeycloakDiscovery(issuer=discovery_document_issuer(), http=client, clock=clock)
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)

        try:
            await discovery.get()
            started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
            client.started, client.release, client.finished = started, release, finished
            waiter = asyncio.create_task(_lookup(cache, entrypoint))
            await started.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            clock.value = 50.0
            release.set()
            await finished.wait()
            client.started = client.release = client.finished = None

            clock.value = 50.0 + age
            await discovery.refresh()
            assert discovery.current().jwks_uri == JWKS_URI
            if age < 300.0:
                await _lookup(cache, entrypoint)
            else:
                with pytest.raises(KeycloakUnavailable):
                    await _lookup(cache, entrypoint)
            assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 1

            clock.value = max(clock.value, 350.0)
            assert await cache.get_key(KID) is not None
            assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 2
        finally:
            await cache.aclose()
            await discovery.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("discovery_change", ["uri", "expiry"])
def test_jwks_revalidates_discovery_after_in_flight_load(
    discovery_change: Literal["uri", "expiry"],
) -> None:
    async def scenario() -> None:
        new_uri = f"{JWKS_URI}-v2"
        clock = ManualMonotonicClock()
        client = DocumentClient(
            {
                DISCOVERY_URL: discovery_document(),
                JWKS_URI: json_bytes({"keys": [public_jwk()]}),
                new_uri: json_bytes({"keys": [public_jwk(key=OTHER_PRIVATE_KEY)]}),
            }
        )
        discovery = KeycloakDiscovery(issuer=discovery_document_issuer(), http=client, clock=clock)
        cache = KeycloakJwksCache(discovery=discovery, http=client, clock=clock)
        try:
            await discovery.get()
            started, release = asyncio.Event(), asyncio.Event()
            client.started, client.release = started, release
            pending = asyncio.create_task(cache.get_key(KID))
            await started.wait()
            client.started = client.release = None
            if discovery_change == "uri":
                client.documents[DISCOVERY_URL] = discovery_document(jwks_uri=new_uri)
                await discovery.refresh()
            else:
                clock.value = 300.0
            release.set()
            with pytest.raises(KeycloakUnavailable):
                await pending

            selected = await cache.get_key(KID)
            assert selected is not None
            if discovery_change == "uri":
                assert selected.value.public_key.public_numbers() == (
                    OTHER_PRIVATE_KEY.public_key().public_numbers()
                )
                assert client.calls.count((new_uri, JWKS_MAXIMUM_BYTES)) == 1
            else:
                assert client.calls.count((JWKS_URI, JWKS_MAXIMUM_BYTES)) == 2
        finally:
            await cache.aclose()
            await discovery.aclose()

    asyncio.run(scenario())


def discovery_document_issuer() -> str:
    value = json.loads(discovery_document())
    assert type(value) is dict and type(value["issuer"]) is str
    return value["issuer"]
