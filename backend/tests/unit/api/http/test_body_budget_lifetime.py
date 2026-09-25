from __future__ import annotations

import asyncio
from typing import Never

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

from ci_coordinator.api.http import body_limits
from ci_coordinator.api.http.body_limits import RequestBodyLimitMiddleware
from ci_coordinator.api.http.request_admission import (
    RequestAdmissionMiddleware,
    RequestAdmissionPolicy,
)
from ci_coordinator.kernel import admission as admission_module

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "stage",
    (
        "receive-empty",
        "receive-prefix",
        "before-replay",
        "after-replay",
        "response-start",
        "response-body",
    ),
)
async def test_cancellation_preserves_ownership_until_unwind_and_refunds_both_permits(
    stage: str,
) -> None:
    entered, parked = asyncio.Event(), asyncio.Event()
    initial = BodyExchange((body_message(b"12345678"),))
    partial_reads = 0

    async def receive() -> Message:
        nonlocal partial_reads
        if stage == "receive-prefix" and partial_reads == 0:
            partial_reads += 1
            return body_message(b"ab", more=True)
        if stage in {"receive-empty", "receive-prefix"}:
            entered.set()
            await parked.wait()
            raise AssertionError("cancelled receiver must not resume")
        return await initial.receive()

    async def downstream(scope: Scope, replay: Receive, send: Send) -> None:
        if scope["path"] == "/held" and stage == "before-replay":
            entered.set()
            await parked.wait()
        assert (await replay())["body"] == b"12345678"
        if scope["path"] == "/held" and stage == "after-replay":
            entered.set()
            await parked.wait()
        await respond_empty(send)

    async def send(message: Message) -> None:
        if message["type"] == f"http.{stage.replace('-', '.')}":
            entered.set()
            await parked.wait()

    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/held"), body_policy("/probe")),
        maximum_retained_body_bytes=8,
        max_concurrent_requests=1,
    )
    async with running_request(middleware, body_scope("/held"), receive, send) as task:
        await wait_until(entered)
        same_path = BodyExchange(())
        await middleware(body_scope("/held"), same_path.receive, same_path.send)
        same_path.assert_response(503, b'{"code":"overload"}')
        other_path = BodyExchange((body_message(b"12345678"),))
        await middleware(body_scope("/probe"), other_path.receive, other_path.send)
        if stage == "receive-empty":
            other_path.assert_response(204, b"")
        else:
            other_path.assert_response(503, b'{"code":"overload"}')
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    parked.set()
    restored = BodyExchange((body_message(b"12345678"),))
    await middleware(body_scope("/held", b"8"), restored.receive, restored.send)
    restored.assert_response(204, b"")


@pytest.mark.parametrize("cause", ("disconnect", "receive-error", "downstream-error", "send-error"))
async def test_disconnect_and_unexpected_errors_do_not_orphan_resources(cause: str) -> None:
    failing = True
    initial = BodyExchange((body_message(b"ab", more=True), {"type": "http.disconnect"}))
    complete = BodyExchange((body_message(b"12345678"),))
    downstream_calls = 0

    async def receive() -> Message:
        if cause in {"disconnect", "receive-error"}:
            if cause == "receive-error" and initial.reads == 1:
                raise OSError("receive defect")
            return await initial.receive()
        return await complete.receive()

    async def downstream(_: Scope, replay: Receive, send: Send) -> None:
        nonlocal downstream_calls
        downstream_calls += 1
        await replay()
        if failing and cause == "downstream-error":
            raise TimeoutError("downstream defect")
        await respond_empty(send)

    async def send(_: Message) -> None:
        raise ConnectionError("send defect")

    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/body"),),
        max_concurrent_requests=1,
        maximum_retained_body_bytes=8,
    )
    if cause == "disconnect":
        await middleware(body_scope("/body"), receive, send)
        assert downstream_calls == 0
    else:
        error: type[Exception] = {
            "receive-error": OSError,
            "downstream-error": TimeoutError,
            "send-error": ConnectionError,
        }[cause]
        with pytest.raises(error, match="defect"):
            await middleware(body_scope("/body"), receive, send)
        assert downstream_calls == (0 if cause == "receive-error" else 1)
    failing = False
    restored = BodyExchange((body_message(b"12345678"),))
    await middleware(body_scope("/body"), restored.receive, restored.send)
    restored.assert_response(204, b"")


@pytest.mark.parametrize("blocked_message", ("http.response.start", "http.response.body"))
async def test_cancelled_overload_send_refunds_only_its_own_partial_body(
    blocked_message: str,
) -> None:
    held_entered, send_entered, parked = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
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

    async def blocked_send(message: Message) -> None:
        await partial.send(message)
        if message["type"] == blocked_message:
            send_entered.set()
            await parked.wait()

    async with running_request(middleware, body_scope("/held"), held.receive):
        await wait_until(held_entered)
        async with running_request(
            middleware, body_scope("/partial"), partial.receive, blocked_send
        ) as task:
            await wait_until(send_entered)
            assert partial.reads == 2
            assert partial.sent[0]["status"] == 503
            if blocked_message == "http.response.body":
                partial.assert_response(503, b'{"code":"overload"}')
            blocked = BodyExchange((body_message(b"x"),))
            await middleware(body_scope("/probe"), blocked.receive, blocked.send)
            blocked.assert_response(503, b'{"code":"overload"}')
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        too_much = BodyExchange((body_message(b"abc"),))
        await middleware(body_scope("/probe"), too_much.receive, too_much.send)
        too_much.assert_response(503, b'{"code":"overload"}')
        exact = BodyExchange((body_message(b"ab"),))
        await middleware(body_scope("/partial"), exact.receive, exact.send)
        exact.assert_response(204, b"")
    full = BodyExchange((body_message(b"12345678"),))
    await middleware(body_scope("/partial"), full.receive, full.send)
    full.assert_response(204, b"")


@pytest.mark.parametrize("allocation", ("lease", "buffer"))
async def test_allocation_failure_cannot_orphan_a_path_or_byte_lease(
    monkeypatch: pytest.MonkeyPatch, allocation: str
) -> None:
    calls = 0

    async def downstream(_: Scope, receive: Receive, send: Send) -> None:
        nonlocal calls
        calls += 1
        await receive()
        await respond_empty(send)

    def fail(*_: object, **__: object) -> Never:
        raise MemoryError("allocation defect")

    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/body"),),
        max_concurrent_requests=1,
        maximum_retained_body_bytes=8,
    )
    initial = BodyExchange((body_message(b"1234", more=True), body_message(b"5678")))
    with monkeypatch.context() as patch:
        if allocation == "lease":
            patch.setattr(admission_module, "GrowingAdmissionLease", fail)
        else:
            patch.setattr(body_limits, "bytearray", fail, raising=False)
        with pytest.raises(MemoryError, match="allocation defect"):
            await middleware(body_scope("/body"), initial.receive, initial.send)
    assert calls == 0 and initial.sent == []
    assert initial.reads == (0 if allocation == "lease" else 2)
    restored = BodyExchange((body_message(b"12345678"),))
    await middleware(body_scope("/body", b"8"), restored.receive, restored.send)
    restored.assert_response(204, b"")
    assert calls == 1


@pytest.mark.parametrize("stage", ("receive", "downstream"))
async def test_owner_deadline_unwinds_body_before_timeout_delivery_and_bounds_that_delivery(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    entered, sending_timeout, parked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    timeouts: list[asyncio.Timeout] = []
    timeout_at = asyncio.timeout_at
    stall = True
    reads = 0

    def record_timeout(deadline: float | None) -> asyncio.Timeout:
        timeout = timeout_at(deadline)
        timeouts.append(timeout)
        return timeout

    monkeypatch.setattr(asyncio, "timeout_at", record_timeout)

    async def downstream(_: Scope, receive: Receive, send: Send) -> None:
        await receive()
        if stall:
            entered.set()
            await parked.wait()
        await respond_empty(send)

    async def initial_receive() -> Message:
        nonlocal reads
        reads += 1
        if stage == "receive":
            if reads == 1:
                return body_message(b"abcd", more=True)
            entered.set()
            await parked.wait()
            raise AssertionError("deadline must cancel the pending receive")
        return body_message(b"12345678")

    observed: list[Message] = []

    async def timeout_send(message: Message) -> None:
        observed.append(message)
        assert message["type"] == "http.response.start" and message["status"] == 503
        sending_timeout.set()
        await parked.wait()

    body = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/body"),),
        maximum_retained_body_bytes=8,
        max_concurrent_requests=1,
    )
    middleware = RequestAdmissionMiddleware(
        body,
        timeout_seconds=30,
        policies=(
            RequestAdmissionPolicy(
                path="/body",
                methods=frozenset({"POST"}),
                overload_response={"code": "request-overload"},
                timeout_response={"code": "owner-timeout"},
                concurrency_limit=1,
                admission_key="body",
            ),
        ),
        liveness_paths=frozenset(),
        default_timeout_response={"code": "default-timeout"},
    )
    async with running_request(
        middleware, body_scope("/body"), initial_receive, timeout_send
    ) as task:
        await wait_until(entered)
        assert len(timeouts) == 2
        hard, work = timeouts
        work.reschedule(asyncio.get_running_loop().time())
        await wait_until(sending_timeout)
        assert work.expired() and not hard.expired()
        stall = False
        restored = BodyExchange((body_message(b"12345678"),))
        await middleware(body_scope("/body"), restored.receive, restored.send)
        restored.assert_response(204, b"")
        hard.reschedule(asyncio.get_running_loop().time())
        with pytest.raises(asyncio.CancelledError, match="request response deadline expired"):
            await task
        assert len(observed) == 1
