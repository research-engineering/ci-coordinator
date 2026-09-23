from __future__ import annotations

import asyncio

import pytest
from starlette.types import Message, Receive, Scope, Send

from ci_coordinator.api.http.public_request_limits import (
    PublicRequestLimitMiddleware,
    PublicRequestLimitPolicy,
)


@pytest.mark.parametrize("refill_rate", [float("nan"), float("inf"), float("-inf")])
def test_public_request_limit_rejects_nonfinite_refill_rates(refill_rate: float) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        PublicRequestLimitPolicy(
            path="/oauth/start",
            methods=frozenset({"GET"}),
            burst=1,
            refill_rate_per_second=refill_rate,
            rejection_response={"ok": False},
        )


@pytest.mark.parametrize("clock_value", [float("nan"), float("inf"), float("-inf")])
def test_public_request_limit_rejects_a_nonfinite_initial_clock(clock_value: float) -> None:
    policy = PublicRequestLimitPolicy(
        path="/oauth/start",
        methods=frozenset({"GET"}),
        burst=1,
        refill_rate_per_second=1.0,
        rejection_response={"ok": False},
    )

    with pytest.raises(ValueError, match="clock must return a finite"):
        PublicRequestLimitMiddleware(
            _unreachable_downstream,
            policies=(policy,),
            clock=lambda: clock_value,
        )


def test_public_request_limit_rejects_without_queueing_and_refills() -> None:
    now = 10.0
    calls = 0
    sent: list[Message] = []

    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        nonlocal calls
        calls += 1

    async def receive() -> Message:
        raise AssertionError("rate admission must not read the request")

    async def send(message: Message) -> None:
        sent.append(message)

    policy = PublicRequestLimitPolicy(
        path="/oauth/start",
        methods=frozenset({"GET"}),
        burst=1,
        refill_rate_per_second=2.0,
        rejection_response={"ok": False, "error": "rate_limited"},
    )
    callback_policy = PublicRequestLimitPolicy(
        path="/oauth/callback",
        methods=frozenset({"GET"}),
        burst=1,
        refill_rate_per_second=2.0,
        rejection_response={"ok": False, "error": "rate_limited"},
    )
    middleware = PublicRequestLimitMiddleware(
        downstream,
        policies=(policy, callback_policy),
        clock=lambda: now,
    )
    start: Scope = {"type": "http", "method": "GET", "path": "/oauth/start"}
    callback: Scope = {"type": "http", "method": "GET", "path": "/oauth/callback"}

    asyncio.run(middleware(start, receive, send))
    asyncio.run(middleware(start, receive, send))
    asyncio.run(middleware(callback, receive, send))
    now += 0.5
    asyncio.run(middleware(start, receive, send))

    assert calls == 3
    assert sent[0]["status"] == 429
    assert (b"retry-after", b"1") in sent[0]["headers"]
    assert sent[1]["body"] == b'{"ok":false,"error":"rate_limited"}'


def test_public_request_limit_is_method_aware_and_has_bounded_state() -> None:
    calls = 0

    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        nonlocal calls
        calls += 1

    async def receive() -> Message:
        raise AssertionError("downstream does not read")

    async def send(_: Message) -> None:
        return None

    policy = PublicRequestLimitPolicy(
        path="/oauth/callback",
        methods=frozenset({"GET"}),
        burst=1,
        refill_rate_per_second=1.0,
        rejection_response={"code": "limited"},
    )
    middleware = PublicRequestLimitMiddleware(
        downstream,
        policies=(policy,),
        clock=lambda: 0.0,
    )
    scope: Scope = {"type": "http", "method": "POST", "path": "/oauth/callback"}

    asyncio.run(middleware(scope, receive, send))

    assert calls == 1
    assert len(middleware._buckets) == 1


def test_nonfinite_and_backward_clock_samples_cannot_refill_a_bucket() -> None:
    now = 10.0
    calls = 0
    sent: list[Message] = []

    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        nonlocal calls
        calls += 1

    async def receive() -> Message:
        raise AssertionError("rate admission must not read the request")

    async def send(message: Message) -> None:
        sent.append(message)

    policy = PublicRequestLimitPolicy(
        path="/oauth/start",
        methods=frozenset({"GET"}),
        burst=1,
        refill_rate_per_second=1.0,
        rejection_response={"ok": False},
    )
    middleware = PublicRequestLimitMiddleware(
        downstream,
        policies=(policy,),
        clock=lambda: now,
    )
    scope: Scope = {"type": "http", "method": "GET", "path": "/oauth/start"}

    asyncio.run(middleware(scope, receive, send))
    now = float("nan")
    asyncio.run(middleware(scope, receive, send))
    now = 9.0
    asyncio.run(middleware(scope, receive, send))
    now = 11.0
    asyncio.run(middleware(scope, receive, send))

    assert calls == 2
    assert [message["status"] for message in sent if message["type"] == "http.response.start"] == [
        429,
        429,
    ]


async def _unreachable_downstream(_: Scope, __: Receive, ___: Send) -> None:
    raise AssertionError("middleware construction must not call downstream")
