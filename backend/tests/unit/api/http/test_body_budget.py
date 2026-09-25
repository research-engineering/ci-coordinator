from __future__ import annotations

import asyncio

import pytest
from api.http.body_budget_support import (
    BodyExchange,
    body_message,
    body_policy,
    body_scope,
    respond_empty,
    running_request,
    wait_until,
)
from starlette.types import Message, Receive, Scope, Send

from ci_coordinator.api.http.body_limits import RequestBodyLimitMiddleware
from ci_coordinator.api.http.routers.config_management import (
    CONFIG_MANAGEMENT_BODY_LIMITS,
    CONFIG_VALIDATIONS_PATH,
)
from ci_coordinator.api.http.routers.github_webhooks import (
    GITHUB_WEBHOOK_PATH,
    github_webhook_body_limit,
)
from ci_coordinator.api.http.routers.plan_requests import PLAN_REQUEST_BODY_LIMIT, PLAN_REQUEST_PATH

pytestmark = pytest.mark.anyio


async def test_two_idle_unauthenticated_bodies_do_not_reserve_the_shared_byte_budget() -> None:
    webhook_entered, config_entered, parked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope["path"])
        assert (await receive())["body"] == b"{}"
        await respond_empty(send)

    async def webhook_receive() -> Message:
        webhook_entered.set()
        await parked.wait()
        raise AssertionError("idle webhook must not produce a body")

    async def config_receive() -> Message:
        config_entered.set()
        await parked.wait()
        raise AssertionError("idle config request must not produce a body")

    webhook = github_webhook_body_limit()
    config = next(
        policy for policy in CONFIG_MANAGEMENT_BODY_LIMITS if policy.path == CONFIG_VALIDATIONS_PATH
    )
    assert webhook.maximum_body_bytes == 26_214_400
    assert config.maximum_body_bytes >= 7_340_032
    middleware = RequestBodyLimitMiddleware(
        downstream, policies=(webhook, config, PLAN_REQUEST_BODY_LIMIT)
    )
    async with (
        running_request(middleware, body_scope(GITHUB_WEBHOOK_PATH), webhook_receive) as first,
        running_request(
            middleware, body_scope(CONFIG_VALIDATIONS_PATH, b"7340032"), config_receive
        ) as second,
    ):
        await wait_until(webhook_entered)
        await wait_until(config_entered)
        assert not first.done() and not second.done()
        small = BodyExchange((body_message(b"{}"),))
        await middleware(body_scope(PLAN_REQUEST_PATH, b"2"), small.receive, small.send)
        small.assert_response(204, b"")
        assert calls == [PLAN_REQUEST_PATH]
        assert not first.done() and not second.done()


@pytest.mark.parametrize(
    ("declared", "chunk", "status", "payload", "reads"),
    (
        (None, b"x", 503, b'{"code":"overload"}', 1),
        (None, b"", 204, b"", 1),
        (b"0", b"", 204, b"", 1),
        (b"0000", b"", 204, b"", 1),
        (b"2", b"x", 400, b'{"code":"invalid"}', 1),
        (b"1", b"xx", 400, b'{"code":"invalid"}', 1),
        (b"1", b"123456789", 413, b'{"code":"large"}', 1),
        (None, bytearray(b"123456789"), 400, b'{"code":"invalid"}', 1),
        (None, None, 400, b'{"code":"invalid"}', 1),
        (b"invalid", b"", 400, b'{"code":"invalid"}', 0),
        (b"9", b"", 413, b'{"code":"large"}', 0),
        (b"9" * 5_000, b"", 413, b'{"code":"large"}', 0),
    ),
)
async def test_body_input_errors_and_zero_weight_precede_growth_under_full_pressure(
    declared: bytes | None, chunk: object, status: int, payload: bytes, reads: int
) -> None:
    entered, parked = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope["path"])
        await receive()
        if scope["path"] == "/held":
            entered.set()
            await parked.wait()
        await respond_empty(send)

    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/held"), body_policy("/probe")),
        maximum_retained_body_bytes=8,
        max_concurrent_requests=1,
    )
    held = BodyExchange((body_message(b"12345678"),))
    async with running_request(middleware, body_scope("/held", b"8"), held.receive):
        await wait_until(entered)
        probe = BodyExchange((body_message(chunk),))
        await middleware(body_scope("/probe", declared), probe.receive, probe.send)
        probe.assert_response(status, payload)
        assert probe.reads == reads
        assert calls == (["/held", "/probe"] if status == 204 else ["/held"])
    restored = BodyExchange((body_message(b"12345678"),))
    await middleware(body_scope("/probe", b"8"), restored.receive, restored.send)
    restored.assert_response(204, b"")


async def test_partial_prefix_charge_is_retained_and_failed_growth_is_fully_refunded() -> None:
    held_entered, prefix_entered, finish_prefix, parked = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    calls: list[bytes] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append((await receive())["body"])
        if scope["path"] == "/held":
            held_entered.set()
            await parked.wait()
        await respond_empty(send)

    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=tuple(body_policy(path) for path in ("/held", "/partial", "/probe")),
        maximum_retained_body_bytes=8,
        max_concurrent_requests=1,
    )
    held = BodyExchange((body_message(b"123456"),))
    partial = BodyExchange((body_message(b"ab", more=True), body_message(b"c")))

    async def partial_receive() -> Message:
        if partial.reads == 1:
            prefix_entered.set()
            await finish_prefix.wait()
        return await partial.receive()

    async with running_request(middleware, body_scope("/held"), held.receive):
        await wait_until(held_entered)
        async with running_request(
            middleware, body_scope("/partial"), partial_receive, partial.send
        ) as partial_task:
            await wait_until(prefix_entered)
            blocked = BodyExchange((body_message(b"x"),))
            await middleware(body_scope("/probe"), blocked.receive, blocked.send)
            blocked.assert_response(503, b'{"code":"overload"}')
            finish_prefix.set()
            await partial_task
            partial.assert_response(503, b'{"code":"overload"}')
            assert partial.reads == 2
            assert calls == [b"123456"]
        reclaimed = BodyExchange((body_message(b"ab"),))
        await middleware(body_scope("/partial"), reclaimed.receive, reclaimed.send)
        reclaimed.assert_response(204, b"")
    full = BodyExchange((body_message(b"12345678"),))
    await middleware(body_scope("/partial"), full.receive, full.send)
    full.assert_response(204, b"")


async def test_invalid_header_precedence_is_preserved_when_the_path_permit_is_full() -> None:
    entered, parked = asyncio.Event(), asyncio.Event()

    async def downstream(_: Scope, receive: Receive, send: Send) -> None:
        await receive()
        entered.set()
        await parked.wait()
        await respond_empty(send)

    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/body"),),
        max_concurrent_requests=1,
        maximum_retained_body_bytes=8,
    )
    held = BodyExchange((body_message(b"12345678"),))
    async with running_request(middleware, body_scope("/body"), held.receive):
        await wait_until(entered)
        duplicate = BodyExchange(())
        scope = body_scope("/body", b"1")
        scope["headers"].append((b"content-length", b"1"))
        await middleware(scope, duplicate.receive, duplicate.send)
        duplicate.assert_response(400, b'{"code":"invalid"}')
        overloaded = BodyExchange(())
        await middleware(body_scope("/body", b"1"), overloaded.receive, overloaded.send)
        overloaded.assert_response(503, b'{"code":"overload"}')
        assert overloaded.reads == duplicate.reads == 0
