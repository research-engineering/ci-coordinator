from __future__ import annotations

import asyncio
from typing import cast

import pytest
from prometheus_support import prometheus_samples
from starlette.types import Message, Receive, Scope, Send

from ci_coordinator.api.http.public_request_limits import (
    PublicRequestLimitMiddleware,
    PublicRequestLimitPolicy,
    TokenBucket,
    try_take_tokens,
)
from ci_coordinator.api.http.routers.control_plane_identity import (
    BACK_CHANNEL_LOGOUT_REFILL_PER_SECOND,
    BACK_CHANNEL_LOGOUT_REQUEST_BURST,
    KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
    KEYCLOAK_LOGIN_START_PATH,
    KEYCLOAK_PUBLIC_REQUEST_LIMITS,
)
from ci_coordinator.observability import RuntimeMetrics


@pytest.mark.parametrize("refill_rate", [float("nan"), float("inf"), float("-inf")])
def test_public_request_limit_rejects_nonfinite_refill_rates(refill_rate: float) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        PublicRequestLimitPolicy(
            path="/oauth/start",
            methods=frozenset({"GET"}),
            burst=1,
            refill_rate_per_second=refill_rate,
            rejection_response={"ok": False},
            route_class="login_start",
        )


@pytest.mark.parametrize("clock_value", [float("nan"), float("inf"), float("-inf")])
def test_public_request_limit_rejects_a_nonfinite_initial_clock(clock_value: float) -> None:
    policy = PublicRequestLimitPolicy(
        path="/oauth/start",
        methods=frozenset({"GET"}),
        burst=1,
        refill_rate_per_second=1.0,
        rejection_response={"ok": False},
        route_class="login_start",
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
        route_class="login_start",
    )
    callback_policy = PublicRequestLimitPolicy(
        path="/oauth/callback",
        methods=frozenset({"GET"}),
        burst=1,
        refill_rate_per_second=2.0,
        rejection_response={"ok": False, "error": "rate_limited"},
        route_class="login_callback",
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
        route_class="login_callback",
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
        route_class="login_start",
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


def test_back_channel_logout_envelope_is_separate_and_counts_its_route_class() -> None:
    """L2: kills the 16/4 logout bucket and a refusal that reaches verification."""
    now = 0.0
    downstream_paths: list[str] = []
    statuses: list[int] = []
    metrics = RuntimeMetrics()

    async def downstream(scope: Scope, _: Receive, __: Send) -> None:
        downstream_paths.append(scope["path"])

    async def receive() -> Message:
        raise AssertionError("an exhausted envelope must not read the logout token")

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    middleware = PublicRequestLimitMiddleware(
        downstream,
        policies=KEYCLOAK_PUBLIC_REQUEST_LIMITS,
        clock=lambda: now,
        metrics=metrics,
    )
    logout: Scope = {"type": "http", "method": "POST", "path": KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH}
    login: Scope = {"type": "http", "method": "GET", "path": KEYCLOAK_LOGIN_START_PATH}

    for _ in range(BACK_CHANNEL_LOGOUT_REQUEST_BURST + 1):
        asyncio.run(middleware(logout, receive, send))
    asyncio.run(middleware(login, receive, send))
    now = 1.0
    for _ in range(int(BACK_CHANNEL_LOGOUT_REFILL_PER_SECOND) + 1):
        asyncio.run(middleware(logout, receive, send))

    assert BACK_CHANNEL_LOGOUT_REQUEST_BURST == 64
    assert BACK_CHANNEL_LOGOUT_REFILL_PER_SECOND == 32.0
    assert downstream_paths.count(KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH) == 64 + 32
    assert downstream_paths.count(KEYCLOAK_LOGIN_START_PATH) == 1
    assert statuses == [429, 429]
    samples = prometheus_samples(metrics)
    assert {
        dict(labels)["route_class"]: value
        for (name, labels), value in samples.items()
        if name == "ci_coordinator_public_request_limited_total"
    } == {
        "back_channel_logout": 2,
        "login_callback": 0,
        "login_start": 0,
        "repository_attestation_callback": 0,
        "webhook": 0,
    }


def test_identity_login_paths_keep_their_separate_sixteen_by_four_buckets() -> None:
    by_class = {policy.route_class: policy for policy in KEYCLOAK_PUBLIC_REQUEST_LIMITS}

    assert set(by_class) == {"login_start", "login_callback", "back_channel_logout"}
    assert {
        (by_class[name].burst, by_class[name].refill_rate_per_second)
        for name in (
            "login_start",
            "login_callback",
        )
    } == {(16, 4.0)}
    assert by_class["back_channel_logout"].path == KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH


@pytest.mark.parametrize("route_class", ["", "Login", "login-start", 7, "x" * 65])
def test_public_request_limit_rejects_an_unbounded_route_class(route_class: object) -> None:
    with pytest.raises(ValueError, match="route class"):
        PublicRequestLimitPolicy(
            path="/oauth/start",
            methods=frozenset({"GET"}),
            burst=1,
            refill_rate_per_second=1.0,
            rejection_response={"ok": False},
            route_class=cast(str, route_class),
        )


def test_joint_token_charge_takes_every_bucket_or_none() -> None:
    requests = TokenBucket(2.0, 1.0, 2.0, 0.0)
    payload = TokenBucket(10.0, 5.0, 10.0, 0.0)

    assert try_take_tokens(0.0, ((requests, 1.0), (payload, 8.0))) is None
    assert try_take_tokens(0.0, ((requests, 1.0), (payload, 8.0))) == 2
    assert (requests.tokens, payload.tokens) == (1.0, 2.0)
    assert try_take_tokens(float("nan"), ((requests, 1.0),)) == 1
    assert requests.tokens == 1.0
