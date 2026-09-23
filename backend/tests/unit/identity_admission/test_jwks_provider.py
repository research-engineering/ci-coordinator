from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import Literal

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from ci_coordinator.identity_admission import (
    ActionsOidcJwkSet,
    CachingJwksProvider,
    JwksFetchResponse,
    JwksProviderConfig,
    JwksTransportFailure,
    JwksUnavailable,
)
from ci_coordinator.identity_admission import jwks_provider as jwks_module


@dataclass
class ManualMonotonicClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value


@dataclass
class ScriptedTransport:
    outcomes: deque[JwksFetchResponse | JwksTransportFailure]
    calls: int = 0

    async def fetch(self) -> JwksFetchResponse | JwksTransportFailure:
        self.calls += 1
        return self.outcomes.popleft()


@dataclass
class GatedTransport:
    result: JwksFetchResponse
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    calls: int = 0

    async def fetch(self) -> JwksFetchResponse:
        self.calls += 1
        task = asyncio.current_task()
        assert task is not None
        task.add_done_callback(lambda _completed: self.finished.set())
        self.started.set()
        await self.release.wait()
        return self.result


def test_matching_key_uses_one_unexpired_immutable_snapshot() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(deque([_response(_jwk("current"))]))
        provider = CachingJwksProvider(transport, ManualMonotonicClock(), _config())

        first = await provider.get_key_set("current")
        second = await provider.get_key_set("current")

        assert not isinstance(first, JwksUnavailable)
        assert second == first
        assert transport.calls == 1

    asyncio.run(scenario())


def test_availability_probe_reuses_a_current_snapshot_without_key_guessing() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(deque([_response(_jwk("current"))]))
        provider = CachingJwksProvider(transport, ManualMonotonicClock(), _config())

        assert await provider.probe() is None
        assert await provider.probe() is None
        assert not isinstance(await provider.get_key_set("current"), JwksUnavailable)
        assert transport.calls == 1

    asyncio.run(scenario())


def test_availability_probe_reports_refresh_failure_without_stale_success() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(
            deque([JwksTransportFailure(kind="timeout", message="deadline elapsed")])
        )
        provider = CachingJwksProvider(transport, ManualMonotonicClock(), _config())

        result = await provider.probe()

        _assert_unavailable(result, "fetch_timeout")
        assert transport.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["lookup", "probe"])
def test_failed_refresh_recovers_at_the_existing_backoff_boundary(entrypoint: str) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        transport = ScriptedTransport(
            deque(
                [
                    JwksTransportFailure(kind="timeout", message="deadline elapsed"),
                    _response(_jwk("current")),
                ]
            )
        )
        provider = CachingJwksProvider(transport, clock, _config())
        lookup = (
            provider.probe if entrypoint == "probe" else partial(provider.get_key_set, "current")
        )
        try:
            _assert_unavailable(await lookup(), "fetch_timeout")
            assert transport.calls == 1
            clock.value = 29.0
            _assert_unavailable(await lookup(), "refresh_throttled")
            assert transport.calls == 1
            clock.value = 30.0
            result = await lookup()
            assert (
                result is None if entrypoint == "probe" else isinstance(result, ActionsOidcJwkSet)
            )
            assert transport.calls == 2
        finally:
            await provider.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["lookup", "probe"])
def test_each_surviving_waiter_rechecks_response_age(entrypoint: str) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        transport = GatedTransport(_response(_jwk("current")))
        provider = CachingJwksProvider(transport, clock, _config())
        lookup = (
            provider.probe if entrypoint == "probe" else partial(provider.get_key_set, "current")
        )
        second_started = asyncio.Event()

        async def consume(*, second: bool = False) -> bool:
            if second:
                second_started.set()
            result = await lookup()
            current = not isinstance(result, JwksUnavailable)
            if current:
                clock.value = 300.0
            return current

        try:
            first = asyncio.create_task(consume())
            await asyncio.wait_for(transport.started.wait(), 5)
            second = asyncio.create_task(consume(second=True))
            await asyncio.wait_for(second_started.wait(), 5)
            transport.release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second), 5)
            assert sorted(results) == [False, True]
            assert transport.calls == 1
        finally:
            await provider.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["lookup", "probe"])
def test_decode_elapsed_time_cannot_renew_the_response(
    entrypoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        transport = ScriptedTransport(deque([_response(_jwk("current"))]))
        provider = CachingJwksProvider(transport, clock, _config())
        original = jwks_module._decode_key_set

        def decode(
            response: JwksFetchResponse, config: JwksProviderConfig
        ) -> ActionsOidcJwkSet | JwksUnavailable:
            result = original(response, config)
            assert isinstance(result, ActionsOidcJwkSet)
            clock.value = 300.0
            return result

        monkeypatch.setattr(jwks_module, "_decode_key_set", decode)
        lookup = (
            provider.probe if entrypoint == "probe" else partial(provider.get_key_set, "current")
        )
        try:
            assert isinstance(await lookup(), JwksUnavailable)
            assert transport.calls == 1
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_unknown_key_refreshes_once_then_uses_rotated_snapshot() -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        transport = ScriptedTransport(deque([_response(_jwk("old")), _response(_jwk("rotated"))]))
        provider = CachingJwksProvider(transport, clock, _config())

        assert not isinstance(await provider.get_key_set("old"), JwksUnavailable)
        clock.value = 30.0
        rotated = await provider.get_key_set("rotated")

        assert not isinstance(rotated, JwksUnavailable)
        assert tuple(key["kid"] for key in rotated.keys) == ("rotated",)
        assert transport.calls == 2

    asyncio.run(scenario())


def test_unknown_key_cannot_trigger_unbounded_refreshes() -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        transport = ScriptedTransport(deque([_response(_jwk("old")), _response(_jwk("old"))]))
        provider = CachingJwksProvider(transport, clock, _config())

        assert not isinstance(await provider.get_key_set("old"), JwksUnavailable)
        clock.value = 30.0
        first_unknown = await provider.get_key_set("random-one")
        second_unknown = await provider.get_key_set("random-two")

        _assert_unavailable(first_unknown, "signing_key_unavailable")
        _assert_unavailable(second_unknown, "refresh_throttled")
        assert transport.calls == 2

    asyncio.run(scenario())


def test_expired_snapshot_is_never_a_fallback_after_refresh_failure() -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        transport = ScriptedTransport(
            deque(
                [
                    _response(_jwk("current")),
                    JwksTransportFailure(kind="timeout", message="deadline elapsed"),
                ]
            )
        )
        provider = CachingJwksProvider(transport, clock, _config())

        assert not isinstance(await provider.get_key_set("current"), JwksUnavailable)
        clock.value = 301.0
        result = await provider.get_key_set("current")

        _assert_unavailable(result, "fetch_timeout")
        assert transport.calls == 2

    asyncio.run(scenario())


def test_concurrent_callers_share_one_refresh() -> None:
    async def scenario() -> None:
        transport = GatedTransport(_response(_jwk("current")))
        provider = CachingJwksProvider(transport, ManualMonotonicClock(), _config())

        first_task = asyncio.create_task(provider.get_key_set("current"))
        await transport.started.wait()
        second_task = asyncio.create_task(provider.get_key_set("current"))
        await asyncio.sleep(0)
        assert transport.calls == 1

        transport.release.set()
        first, second = await asyncio.gather(first_task, second_task)

        assert not isinstance(first, JwksUnavailable)
        assert second == first

    asyncio.run(scenario())


def test_cancelling_one_waiter_does_not_cancel_the_shared_refresh() -> None:
    async def scenario() -> None:
        transport = GatedTransport(_response(_jwk("current")))
        provider = CachingJwksProvider(transport, ManualMonotonicClock(), _config())

        cancelled_waiter = asyncio.create_task(provider.get_key_set("current"))
        await transport.started.wait()
        surviving_waiter = asyncio.create_task(provider.get_key_set("current"))
        cancelled_waiter.cancel()
        try:
            await cancelled_waiter
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled waiter unexpectedly completed")

        transport.release.set()
        result = await surviving_waiter

        assert not isinstance(result, JwksUnavailable)
        assert transport.calls == 1

    asyncio.run(scenario())


def test_close_cancels_and_drains_the_shared_refresh_then_rejects_new_work() -> None:
    async def scenario() -> None:
        transport = GatedTransport(_response(_jwk("current")))
        provider = CachingJwksProvider(transport, ManualMonotonicClock(), _config())
        lookup = asyncio.create_task(provider.get_key_set("current"))
        await transport.started.wait()

        await provider.aclose()
        result = await lookup
        after_close = await provider.get_key_set("current")
        probe = await provider.probe()
        await provider.aclose()

        _assert_unavailable(result, "fetch_cancelled")
        _assert_unavailable(after_close, "fetch_unavailable")
        _assert_unavailable(probe, "fetch_unavailable")
        assert transport.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["lookup", "probe"])
@pytest.mark.parametrize("age", [299.0, 300.0, 301.0])
def test_detached_refresh_retains_response_age_and_recovers(
    entrypoint: Literal["lookup", "probe"],
    age: float,
) -> None:
    async def scenario() -> None:
        clock = ManualMonotonicClock()
        transport = GatedTransport(_response(_jwk("current")))
        provider = CachingJwksProvider(transport, clock, _config())

        async def lookup() -> bool:
            if entrypoint == "probe":
                return await provider.probe() is None
            return not isinstance(await provider.get_key_set("current"), JwksUnavailable)

        try:
            waiter = asyncio.create_task(lookup())
            await transport.started.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            clock.value = 50.0
            transport.release.set()
            await transport.finished.wait()
            assert transport.calls == 1

            clock.value = 50.0 + age
            assert await lookup() is (age < 300.0)
            assert transport.calls == 1

            clock.value = max(clock.value, 350.0)
            assert await provider.probe() is None
            assert transport.calls == 2
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_incomplete_or_malformed_jwk_never_reaches_cache() -> None:
    async def scenario() -> None:
        incomplete: dict[str, object] = {
            "kid": "bad",
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
        }
        transport = ScriptedTransport(deque([_response(incomplete)]))
        provider = CachingJwksProvider(transport, ManualMonotonicClock(), _config())

        result = await provider.get_key_set("bad")

        _assert_unavailable(result, "response_invalid")

    asyncio.run(scenario())


def test_provider_admits_a_valid_key_despite_additive_metadata_and_unsupported_entries() -> None:
    async def scenario() -> None:
        valid = _jwk("current")
        provider_shaped = {
            **valid,
            "x5c": ["public-certificate-chain"],
            "x5t": "certificate-thumbprint",
        }
        unsupported = {**_jwk("unsupported"), "kty": "EC"}
        body = json.dumps({"keys": [unsupported, None, provider_shaped]}).encode()
        transport = ScriptedTransport(deque([_response_body(body)]))
        provider = CachingJwksProvider(transport, ManualMonotonicClock(), _config())

        result = await provider.get_key_set("current")

        assert not isinstance(result, JwksUnavailable)
        assert tuple(key["kid"] for key in result.keys) == ("current",)
        assert "x5c" not in result.keys[0]
        assert "x5t" not in result.keys[0]

    asyncio.run(scenario())


@pytest.mark.parametrize(("raw_key_count", "is_admitted"), [(64, True), (65, False)])
def test_raw_key_count_bound_is_enforced_before_entry_admission(
    raw_key_count: int,
    is_admitted: bool,
) -> None:
    seed = _jwk("seed")
    keys = [{**seed, "kid": f"key-{index}"} for index in range(raw_key_count)]

    async def scenario() -> None:
        provider = CachingJwksProvider(
            ScriptedTransport(deque([_response(*keys)])),
            ManualMonotonicClock(),
            _config(),
        )

        result = await provider.get_key_set("key-0")

        assert (not isinstance(result, JwksUnavailable)) is is_admitted
        if is_admitted:
            assert not isinstance(result, JwksUnavailable)
            assert len(result.keys) == raw_key_count
        else:
            _assert_unavailable(result, "response_invalid")

    asyncio.run(scenario())


def test_non_json_content_type_and_transport_cancellation_are_typed_unavailability() -> None:
    async def scenario() -> None:
        cases: tuple[tuple[JwksFetchResponse | JwksTransportFailure, str], ...] = (
            (
                JwksFetchResponse(status=200, content_type="text/plain", body=b'{"keys": []}'),
                "response_invalid",
            ),
            (
                JwksFetchResponse(status=200, content_type="application/json", body=b"not json"),
                "response_invalid",
            ),
            (
                JwksTransportFailure(kind="cancelled", message="transport stopped"),
                "fetch_cancelled",
            ),
        )
        for outcome, expected_kind in cases:
            provider = CachingJwksProvider(
                ScriptedTransport(deque([outcome])),
                ManualMonotonicClock(),
                _config(),
            )
            _assert_unavailable(await provider.get_key_set("current"), expected_kind)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "counterexample",
    ["nan", "positive-infinity", "negative-infinity", "duplicate-key", "utf16-json"],
)
def test_jwks_response_rejects_non_strict_json(
    counterexample: str,
    admitted_jwk_json: bytes,
) -> None:
    key_array = b"[" + admitted_jwk_json + b"]"
    valid_body = b'{"keys":' + key_array + b"}"
    bodies = {
        "nan": valid_body[:-1] + b',"metadata":NaN}',
        "positive-infinity": valid_body[:-1] + b',"metadata":Infinity}',
        "negative-infinity": valid_body[:-1] + b',"metadata":-Infinity}',
        "duplicate-key": b'{"keys":' + key_array + b',"keys":' + key_array + b"}",
        "utf16-json": valid_body.decode("utf-8").encode("utf-16"),
    }

    async def scenario() -> None:
        response = JwksFetchResponse(
            status=200,
            content_type="application/json",
            body=bodies[counterexample],
        )
        provider = CachingJwksProvider(
            ScriptedTransport(deque([response])),
            ManualMonotonicClock(),
            _config(),
        )

        _assert_unavailable(await provider.get_key_set("current"), "response_invalid")

    asyncio.run(scenario())


def test_jwks_response_preserves_oversize_status_at_json_boundary(
    admitted_jwk_json: bytes,
) -> None:
    body = b'{"keys":[' + admitted_jwk_json + b"]}"

    async def scenario() -> None:
        response = JwksFetchResponse(
            status=200,
            content_type="application/json",
            body=body,
        )
        provider = CachingJwksProvider(
            ScriptedTransport(deque([response])),
            ManualMonotonicClock(),
            _config(maximum_response_bytes=len(body) - 1),
        )

        _assert_unavailable(await provider.get_key_set("current"), "response_oversize")

    asyncio.run(scenario())


def test_jwks_parser_recursion_is_typed_unavailability(
    admitted_jwk_json: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_recursion(*_args: object, **_kwargs: object) -> object:
        raise RecursionError

    monkeypatch.setattr(
        "ci_coordinator.kernel.strict_json.json.loads",
        raise_recursion,
    )

    async def scenario() -> None:
        provider = CachingJwksProvider(
            ScriptedTransport(deque([_response_body(b'{"keys":[' + admitted_jwk_json + b"]}")])),
            ManualMonotonicClock(),
            _config(),
        )

        _assert_unavailable(await provider.get_key_set("current"), "response_invalid")

    asyncio.run(scenario())


def test_transport_task_cancellation_becomes_typed_provider_unavailability() -> None:
    @dataclass
    class CancelledTransport:
        async def fetch(self) -> JwksFetchResponse:
            raise asyncio.CancelledError("transport cancelled")

    async def scenario() -> None:
        provider = CachingJwksProvider(CancelledTransport(), ManualMonotonicClock(), _config())
        _assert_unavailable(await provider.get_key_set("current"), "fetch_cancelled")

    asyncio.run(scenario())


def test_provider_contracts_reject_unadmitted_runtime_values() -> None:
    for kwargs in (
        {"maximum_cache_age_seconds": True},
        {"maximum_response_bytes": True},
        {"required_content_type_prefix": 1},
    ):
        values: dict[str, object] = {
            "maximum_cache_age_seconds": 300.0,
            "minimum_refresh_interval_seconds": 30.0,
            "failure_backoff_seconds": 30.0,
            "maximum_response_bytes": 1024,
            "required_content_type_prefix": "application/json",
        }
        values.update(kwargs)
        try:
            JwksProviderConfig(**values)  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError("unadmitted provider configuration was accepted")

    try:
        JwksTransportFailure(kind="unknown", message="invalid")  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        raise AssertionError("unadmitted transport failure kind was accepted")


@pytest.fixture(scope="module")
def admitted_jwk_json() -> bytes:
    return json.dumps(_jwk("current"), separators=(",", ":")).encode("utf-8")


def _config(*, maximum_response_bytes: int = 1024 * 1024) -> JwksProviderConfig:
    return JwksProviderConfig(
        maximum_cache_age_seconds=300,
        minimum_refresh_interval_seconds=30,
        failure_backoff_seconds=30,
        maximum_response_bytes=maximum_response_bytes,
    )


def _response(*keys: dict[str, object]) -> JwksFetchResponse:
    return _response_body(json.dumps({"keys": keys}).encode())


def _response_body(body: bytes) -> JwksFetchResponse:
    return JwksFetchResponse(
        status=200,
        content_type="application/json; charset=utf-8",
        body=body,
    )


def _jwk(key_id: str) -> dict[str, object]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    assert type(public) is dict
    return {**public, "kid": key_id, "use": "sig", "alg": "RS256"}


def _assert_unavailable(result: object, kind: str) -> None:
    assert isinstance(result, JwksUnavailable)
    assert result.kind == kind
