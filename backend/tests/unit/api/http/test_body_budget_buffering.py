from __future__ import annotations

import weakref

import anyio
import pytest
from api.http.body_budget_support import (
    BodyExchange,
    body_message,
    body_policy,
    body_scope,
    respond_empty,
)
from starlette.types import Message, Receive, Scope, Send

from ci_coordinator.api.http import body_limits
from ci_coordinator.api.http.body_limits import RequestBodyLimitMiddleware
from ci_coordinator.kernel import GrowingAdmissionLease, WeightedNoQueueAdmission

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("empty_padding", (0, 1, 65))
async def test_one_nonempty_chunk_is_replayed_by_identity(empty_padding: int) -> None:
    payload = bytes(range(1, 9))

    async def downstream(_: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        assert message["body"] is payload
        assert message["more_body"] is False
        assert (await receive())["type"] == "http.disconnect"
        await respond_empty(send)

    frames = (
        *(body_message(more=True) for _ in range(empty_padding)),
        body_message(payload, more=bool(empty_padding)),
        *(body_message(more=index < empty_padding - 1) for index in range(empty_padding)),
        {"type": "http.disconnect"},
    )
    exchange = BodyExchange(frames)
    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/body"),),
        maximum_retained_body_bytes=8,
    )
    await middleware(body_scope("/body", b"8"), exchange.receive, exchange.send)
    exchange.assert_response(204, b"")
    assert exchange.reads == len(frames)


@pytest.mark.parametrize("chunks", (2, 4_097))
async def test_many_chunks_use_one_accumulator_and_release_it_before_downstream(
    monkeypatch: pytest.MonkeyPatch, chunks: int
) -> None:
    class ObservedBuffer(bytearray):
        pass

    buffers: list[weakref.ReferenceType[ObservedBuffer]] = []
    lease_count = 0
    acquire = WeightedNoQueueAdmission.try_acquire_growing

    def buffer(initial: bytes) -> ObservedBuffer:
        value = ObservedBuffer(initial)
        buffers.append(weakref.ref(value))
        return value

    def counted_acquire(
        owner: WeightedNoQueueAdmission, weight: int = 0
    ) -> GrowingAdmissionLease | None:
        nonlocal lease_count
        lease_count += 1
        return acquire(owner, weight)

    monkeypatch.setattr(body_limits, "bytearray", buffer, raising=False)
    monkeypatch.setattr(WeightedNoQueueAdmission, "try_acquire_growing", counted_acquire)

    async def downstream(_: Scope, receive: Receive, send: Send) -> None:
        assert len(buffers) == 1 and buffers[0]() is None
        assert (await receive())["body"] == b"x" * chunks
        await respond_empty(send)

    exchange = BodyExchange(
        tuple(body_message(b"x", more=index < chunks - 1) for index in range(chunks))
    )
    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/body", chunks),),
        maximum_retained_body_bytes=chunks,
    )
    await middleware(body_scope("/body"), exchange.receive, exchange.send)
    exchange.assert_response(204, b"")
    assert lease_count == 1
    assert len(buffers) == 1 and buffers[0]() is None


@pytest.mark.parametrize("invalid_chunk", (b"12345678", bytearray(b"12345678")))
async def test_an_invalid_captured_chunk_is_not_charged_or_copied(
    monkeypatch: pytest.MonkeyPatch, invalid_chunk: object
) -> None:
    deltas: list[int] = []
    grow = GrowingAdmissionLease.try_grow

    def counted_grow(lease: GrowingAdmissionLease, delta: int) -> bool:
        deltas.append(delta)
        return grow(lease, delta)

    def unexpected_buffer(_: bytes) -> bytearray:
        raise AssertionError("invalid chunk must not trigger a copy")

    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        raise AssertionError("invalid chunk must not reach downstream")

    monkeypatch.setattr(body_limits, "bytearray", unexpected_buffer, raising=False)
    monkeypatch.setattr(GrowingAdmissionLease, "try_grow", counted_grow)
    exchange = BodyExchange((body_message(b"x", more=True), body_message(invalid_chunk)))
    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/body"),),
        maximum_retained_body_bytes=8,
    )
    await middleware(body_scope("/body"), exchange.receive, exchange.send)
    if type(invalid_chunk) is bytes:
        exchange.assert_response(413, b'{"code":"large"}')
    else:
        exchange.assert_response(400, b'{"code":"invalid"}')
    assert deltas == [1]


@pytest.mark.parametrize("kind", ("empty", "tiny", "ignored"))
async def test_ready_message_stream_reaches_a_cancellable_anyio_checkpoint(kind: str) -> None:
    reads = 0
    calls = 0

    async def downstream(_: Scope, receive: Receive, send: Send) -> None:
        nonlocal calls
        calls += 1
        assert (await receive())["body"] == b"x" * 128
        await respond_empty(send)

    middleware = RequestBodyLimitMiddleware(
        downstream,
        policies=(body_policy("/body", 128),),
        maximum_retained_body_bytes=128,
        max_concurrent_requests=1,
    )
    with anyio.CancelScope() as cancellation:

        async def receive() -> Message:
            nonlocal reads
            reads += 1
            assert reads <= 128, "always-ready receive never reached a checkpoint"
            cancellation.cancel()
            if kind == "ignored":
                return {"type": "http.other"}
            return body_message(b"x" if kind == "tiny" else b"", more=True)

        async def send(_: Message) -> None:
            raise AssertionError("cancellation must not send an overload/timeout response")

        await middleware(body_scope("/body"), receive, send)
        raise AssertionError("cancelled body read completed")
    assert cancellation.cancelled_caught
    assert reads == 64 and calls == 0
    restored = BodyExchange((body_message(b"x" * 128),))
    await middleware(body_scope("/body"), restored.receive, restored.send)
    restored.assert_response(204, b"")
    assert calls == 1
