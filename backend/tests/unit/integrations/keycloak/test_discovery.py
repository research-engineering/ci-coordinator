from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from math import inf, nan
from typing import Literal

import pytest

from ci_coordinator.control_plane_identity import KeycloakUnavailable
from ci_coordinator.integrations.keycloak import discovery as discovery_module
from ci_coordinator.integrations.keycloak.discovery import (
    DISCOVERY_CACHE_MAXIMUM_SECONDS,
    DISCOVERY_MAXIMUM_BYTES,
    FAILED_REFRESH_RETRY_SECONDS,
    KeycloakDiscovery,
    KeycloakProviderMetadata,
)

from ._support import (
    AUTHORIZATION_ENDPOINT,
    DISCOVERY_URL,
    END_SESSION_ENDPOINT,
    ISSUER,
    JWKS_URI,
    TOKEN_ENDPOINT,
    DocumentClient,
    ManualMonotonicClock,
    discovery_document,
    json_bytes,
)

DocumentMutation = Callable[[dict[str, object]], None]


@pytest.mark.parametrize("entrypoint", ["get", "refresh"])
@pytest.mark.parametrize("recovery_delay", [30.0, 30.001])
def test_discovery_recovers_after_a_failed_refresh(entrypoint: str, recovery_delay: float) -> None:
    async def scenario() -> None:
        client = DocumentClient(
            {DISCOVERY_URL: discovery_document()}, failure=RuntimeError("offline")
        )
        clock = ManualMonotonicClock()
        discovery = KeycloakDiscovery(issuer=ISSUER, http=client, clock=clock)
        lookup = discovery.get if entrypoint == "get" else discovery.refresh
        try:
            with pytest.raises(KeycloakUnavailable):
                await lookup()
            assert len(client.calls) == 1
            client.failure = None
            for now in (0.0, FAILED_REFRESH_RETRY_SECONDS - 0.001):
                clock.value = now
                with pytest.raises(KeycloakUnavailable):
                    await lookup()
                assert len(client.calls) == 1
            clock.value = recovery_delay
            assert (await lookup()).issuer == ISSUER
            assert len(client.calls) == 2
        finally:
            await discovery.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["get", "refresh"])
def test_discovery_rechecks_age_for_each_surviving_waiter(entrypoint: str) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        started, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        client = DocumentClient(
            {DISCOVERY_URL: discovery_document()}, started=started, release=release
        )
        discovery = KeycloakDiscovery(issuer=ISSUER, http=client, clock=clock)
        lookup = discovery.get if entrypoint == "get" else discovery.refresh

        async def consume(*, second: bool = False) -> bool:
            if second:
                second_started.set()
            try:
                await lookup()
            except KeycloakUnavailable:
                return False
            clock.value = 300.0
            return True

        try:
            first = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 5)
            second = asyncio.create_task(consume(second=True))
            await asyncio.wait_for(second_started.wait(), 5)
            release.set()
            assert sorted(await asyncio.wait_for(asyncio.gather(first, second), 5)) == [False, True]
            assert len(client.calls) == 1
        finally:
            await discovery.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["get", "refresh"])
def test_discovery_decode_elapsed_time_consumes_response_lifetime(
    entrypoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        client = DocumentClient({DISCOVERY_URL: discovery_document()})
        discovery = KeycloakDiscovery(issuer=ISSUER, http=client, clock=clock)
        original = discovery_module._decode_metadata

        def decode(body: bytes, issuer: str) -> KeycloakProviderMetadata:
            result = original(body, issuer)
            clock.value = 300.0
            return result

        monkeypatch.setattr(discovery_module, "_decode_metadata", decode)
        lookup = discovery.get if entrypoint == "get" else discovery.refresh
        try:
            with pytest.raises(KeycloakUnavailable):
                await lookup()
            assert len(client.calls) == 1
        finally:
            await discovery.aclose()

    asyncio.run(scenario())


def _set(field: str, value: object) -> DocumentMutation:
    def mutate(document: dict[str, object]) -> None:
        document[field] = value

    return mutate


def _remove(field: str) -> DocumentMutation:
    def mutate(document: dict[str, object]) -> None:
        document.pop(field, None)

    return mutate


@pytest.mark.parametrize(
    ("mutation", "case"),
    [
        (_set("issuer", f"{ISSUER}/"), "issuer-not-exact"),
        (_set("issuer", "https://auth.example.test/realms/other"), "issuer-other-realm"),
        (_remove("response_types_supported"), "required-capability-absent"),
        (_set("response_types_supported", ["code", "code"]), "capability-duplicate"),
        (_set("response_types_supported", ["token"]), "response-type-confusion"),
        (_set("grant_types_supported", ["implicit"]), "grant-type-confusion"),
        (_set("code_challenge_methods_supported", ["plain"]), "pkce-downgrade"),
        (
            _set("token_endpoint_auth_methods_supported", ["client_secret_post"]),
            "client-auth-downgrade",
        ),
        (_set("id_token_signing_alg_values_supported", ["HS256"]), "algorithm-confusion"),
        (_set("response_types_supported", ["code", 1]), "capability-type-confusion"),
        (_set("response_types_supported", ["code", *(str(i) for i in range(64))]), "cap"),
        (_set("authorization_endpoint", "http://auth.example.test/auth"), "plaintext"),
        (_set("authorization_endpoint", "https://evil.example.test/auth"), "cross-origin"),
        (_set("authorization_endpoint", f"{AUTHORIZATION_ENDPOINT}?next=evil"), "query"),
        (_set("authorization_endpoint", f"{AUTHORIZATION_ENDPOINT}#fragment"), "fragment"),
        (
            _set("authorization_endpoint", "https://user@auth.example.test/auth"),
            "credentials",
        ),
        (_set("authorization_endpoint", "https://AUTH.example.test/auth"), "host-case"),
        (_set("authorization_endpoint", "https://auth.example.test:443/auth"), "default-port"),
        (_set("authorization_endpoint", f"{ISSUER}/a%0ab"), "decoded-control"),
        (_set("authorization_endpoint", f"{ISSUER}/a\\b"), "backslash"),
        (_set("authorization_endpoint", "x" * 2_049), "endpoint-bound"),
        (_set("end_session_endpoint", 1), "optional-endpoint-type"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_discovery_rejects_metadata_countermodels(
    mutation: DocumentMutation,
    case: str,
) -> None:
    del case
    value = json.loads(discovery_document())
    assert type(value) is dict
    mutation(value)
    client = DocumentClient({DISCOVERY_URL: json_bytes(value)})
    discovery = KeycloakDiscovery(issuer=ISSUER, http=client, clock=ManualMonotonicClock())

    with pytest.raises(KeycloakUnavailable):
        asyncio.run(discovery.get())


def test_discovery_admits_only_the_fixed_url_exact_profile_and_fresh_cache() -> None:
    clock = ManualMonotonicClock()
    client = DocumentClient({DISCOVERY_URL: discovery_document()})
    discovery = KeycloakDiscovery(issuer=ISSUER, http=client, clock=clock)

    async def scenario() -> None:
        loaded = await discovery.get()
        assert await discovery.get() is loaded
        assert discovery.current() is loaded
        assert loaded.authorization_endpoint == AUTHORIZATION_ENDPOINT
        assert loaded.token_endpoint == TOKEN_ENDPOINT
        assert loaded.jwks_uri == JWKS_URI
        assert loaded.end_session_endpoint == END_SESSION_ENDPOINT

    asyncio.run(scenario())

    assert client.calls == [(DISCOVERY_URL, DISCOVERY_MAXIMUM_BYTES)]


def test_discovery_decoder_enforces_the_body_bound_even_for_nonconforming_clients() -> None:
    oversized = b'{"padding":"' + b"x" * DISCOVERY_MAXIMUM_BYTES + b'"}'
    discovery = KeycloakDiscovery(
        issuer=ISSUER,
        http=DocumentClient({DISCOVERY_URL: oversized}),
        clock=ManualMonotonicClock(),
    )

    with pytest.raises(KeycloakUnavailable):
        asyncio.run(discovery.get())


def test_discovery_expiry_and_invalid_clock_fail_closed() -> None:
    clock = ManualMonotonicClock()
    client = DocumentClient({DISCOVERY_URL: discovery_document()})
    discovery = KeycloakDiscovery(issuer=ISSUER, http=client, clock=clock)
    asyncio.run(discovery.get())

    clock.value = DISCOVERY_CACHE_MAXIMUM_SECONDS
    with pytest.raises(KeycloakUnavailable):
        discovery.current()

    for invalid in (-1.0, inf, -inf, nan):
        clock.value = invalid
        with pytest.raises(KeycloakUnavailable):
            discovery.current()


def test_discovery_single_flight_survives_one_waiter_cancellation() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        client = DocumentClient(
            {DISCOVERY_URL: discovery_document()},
            started=started,
            release=release,
        )
        discovery = KeycloakDiscovery(
            issuer=ISSUER,
            http=client,
            clock=ManualMonotonicClock(),
        )
        first = asyncio.create_task(discovery.get())
        await started.wait()
        second = asyncio.create_task(discovery.get())
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second).issuer == ISSUER
        assert len(client.calls) == 1
        await discovery.aclose()

    asyncio.run(scenario())


def test_discovery_close_cancels_refresh_and_rejects_future_use() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        client = DocumentClient(
            {DISCOVERY_URL: discovery_document()},
            started=started,
            release=release,
        )
        discovery = KeycloakDiscovery(
            issuer=ISSUER,
            http=client,
            clock=ManualMonotonicClock(),
        )
        load = asyncio.create_task(discovery.refresh())
        await started.wait()
        await discovery.aclose()
        with pytest.raises(KeycloakUnavailable):
            await load
        with pytest.raises(KeycloakUnavailable):
            await discovery.get()
        with pytest.raises(KeycloakUnavailable):
            discovery.current()

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["get", "refresh"])
@pytest.mark.parametrize("age", [299.0, 300.0, 301.0])
def test_detached_discovery_retains_response_age_and_recovers(
    entrypoint: Literal["get", "refresh"],
    age: float,
) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        client = DocumentClient(
            {DISCOVERY_URL: discovery_document()},
            started=started,
            release=release,
            finished=finished,
        )
        discovery = KeycloakDiscovery(issuer=ISSUER, http=client, clock=clock)
        lookup = discovery.get if entrypoint == "get" else discovery.refresh
        try:
            waiter = asyncio.create_task(lookup())
            await started.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            clock.value = 50.0
            release.set()
            await finished.wait()

            clock.value = 50.0 + age
            if age < 300.0:
                assert (await lookup()).issuer == ISSUER
                assert discovery.current().issuer == ISSUER
            else:
                with pytest.raises(KeycloakUnavailable):
                    await lookup()
                with pytest.raises(KeycloakUnavailable):
                    discovery.current()
            assert len(client.calls) == 1

            clock.value = max(clock.value, 350.0)
            assert (await discovery.get()).issuer == ISSUER
            assert len(client.calls) == 2
        finally:
            await discovery.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "issuer",
    [
        "http://auth.example.test/realms/coordinator",
        "https://auth.example.test/realms/coordinator/",
        "https://user@auth.example.test/realms/coordinator",
        "https://AUTH.example.test/realms/coordinator",
    ],
)
def test_discovery_constructor_rejects_noncanonical_issuer(issuer: str) -> None:
    with pytest.raises(ValueError, match="canonical HTTPS"):
        KeycloakDiscovery(
            issuer=issuer,
            http=DocumentClient({}),
            clock=ManualMonotonicClock(),
        )
