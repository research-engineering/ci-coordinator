from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from io import StringIO

import pytest
from prometheus_support import prometheus_samples
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ci_coordinator.api.http.request_admission import (
    RequestAdmissionMiddleware,
    RequestAdmissionPolicy,
)
from ci_coordinator.observability import (
    HttpRequestObservationMiddleware,
    RuntimeMetrics,
    StructuredEventLogger,
    scope_request_observation,
)

_POLICY = RequestAdmissionPolicy(
    path="/bounded/{item_id}",
    methods=frozenset({"POST"}),
    overload_response={"code": "overloaded"},
    timeout_response={"code": "unavailable"},
    concurrency_limit=1,
    admission_key="bounded",
)


@pytest.mark.parametrize("stall", ["receive", "downstream"])
def test_one_deadline_bounds_receive_and_downstream_work(stall: str) -> None:
    async def scenario() -> tuple[int, list[Message]]:
        downstream_calls = 0
        sent: list[Message] = []

        async def downstream(_: Scope, receive: Receive, __: Send) -> None:
            nonlocal downstream_calls
            downstream_calls += 1
            if stall == "receive":
                await receive()
            else:
                await asyncio.Event().wait()

        async def receive() -> Message:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: Message) -> None:
            sent.append(message)

        await _middleware(downstream, timeout_seconds=1)(
            _scope(),
            receive,
            send,
        )
        return downstream_calls, sent

    downstream_calls, sent = asyncio.run(scenario())

    assert downstream_calls == 1
    assert sent[0]["status"] == 503
    assert sent[1]["body"] == b'{"code":"unavailable"}'


def test_bulkhead_rejects_without_reading_or_waiting() -> None:
    async def scenario() -> tuple[int, list[Message]]:
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        downstream_calls = 0
        rejected: list[Message] = []

        async def downstream(_: Scope, __: Receive, ___: Send) -> None:
            nonlocal downstream_calls
            downstream_calls += 1
            first_started.set()
            await release_first.wait()

        middleware = _middleware(downstream, timeout_seconds=2)

        async def receive() -> Message:
            raise AssertionError("admission rejection must not read the request")

        async def discard(_: Message) -> None:
            return None

        async def capture(message: Message) -> None:
            rejected.append(message)

        first = asyncio.create_task(middleware(_scope(), receive, discard))
        await first_started.wait()
        await middleware(_scope(), receive, capture)
        release_first.set()
        await first
        return downstream_calls, rejected

    downstream_calls, rejected = asyncio.run(scenario())

    assert downstream_calls == 1
    assert rejected[0]["status"] == 503
    assert rejected[1]["body"] == b'{"code":"overloaded"}'


@pytest.mark.parametrize("cause", ["overload", "timeout"])
@pytest.mark.parametrize("blocked_message", ["http.response.start", "http.response.body"])
def test_rejection_send_is_bounded_and_does_not_hold_the_work_permit(
    cause: str, blocked_message: str
) -> None:
    async def scenario() -> None:
        entered, release, stalled, send_cancelled = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        calls = 0
        rejected: list[Message] = []
        probe: list[Message] = []

        async def downstream(_: Scope, __: Receive, send: Send) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive() -> Message:
            raise AssertionError("this scenario never reads the request body")

        async def discard(_: Message) -> None:
            return None

        async def blocked_send(message: Message) -> None:
            rejected.append(message)
            if message["type"] == blocked_message:
                stalled.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    send_cancelled.set()

        async def probe_send(message: Message) -> None:
            probe.append(message)

        middleware = _middleware(downstream, timeout_seconds=1)
        first = asyncio.create_task(
            middleware(_scope(), receive, discard if cause == "overload" else blocked_send)
        )
        tasks = [first]
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if cause == "overload":
                rejected_task = asyncio.create_task(middleware(_scope(), receive, blocked_send))
                tasks.append(rejected_task)
            else:
                rejected_task = first
            await asyncio.wait_for(stalled.wait(), 2)
            if cause == "overload":
                release.set()
                await first
            await middleware(_scope(), receive, probe_send)
            assert calls == 2 and probe[0]["status"] == 200
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(rejected_task), 3)
            assert send_cancelled.is_set()
            assert rejected[0]["status"] == 503
            if blocked_message == "http.response.body":
                expected = (
                    b'{"code":"overloaded"}' if cause == "overload" else b'{"code":"unavailable"}'
                )
                assert rejected[1]["body"] == expected
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())


def test_cancellation_releases_bulkhead_capacity() -> None:
    async def scenario() -> int:
        first_started = asyncio.Event()
        downstream_calls = 0

        async def downstream(_: Scope, __: Receive, send: Send) -> None:
            nonlocal downstream_calls
            downstream_calls += 1
            if downstream_calls == 1:
                first_started.set()
                await asyncio.Event().wait()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = _middleware(downstream, timeout_seconds=2)

        async def receive() -> Message:
            raise AssertionError("request body is not read")

        async def send(_: Message) -> None:
            return None

        first = asyncio.create_task(middleware(_scope(), receive, send))
        await first_started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await middleware(_scope(), receive, send)
        return downstream_calls

    assert asyncio.run(scenario()) == 2


def test_timeout_after_response_start_is_not_rewritten() -> None:
    async def scenario() -> None:
        sent: list[Message] = []

        async def downstream(_: Scope, __: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await asyncio.Event().wait()

        async def receive() -> Message:
            raise AssertionError("request body is not read")

        async def send(message: Message) -> None:
            sent.append(message)

        with pytest.raises(TimeoutError):
            await _middleware(downstream, timeout_seconds=1)(_scope(), receive, send)
        assert sent == [{"type": "http.response.start", "status": 200, "headers": []}]

    asyncio.run(scenario())


def test_liveness_bypasses_deadline_and_bulkhead() -> None:
    downstream_calls = 0

    async def downstream(_: Scope, __: Receive, send: Send) -> None:
        nonlocal downstream_calls
        downstream_calls += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:
        raise AssertionError("request body is not read")

    async def send(_: Message) -> None:
        return None

    asyncio.run(
        _middleware(downstream, timeout_seconds=1)(
            {"type": "http", "method": "GET", "path": "/healthz", "headers": []},
            receive,
            send,
        )
    )

    assert downstream_calls == 1


def test_required_response_headers_replace_conflicting_downstream_values() -> None:
    async def scenario() -> list[Message]:
        sent: list[Message] = []

        async def downstream(_: Scope, __: Receive, send: Send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"Cache-Control", b"public"),
                        (b"x-retained", b"value"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})

        async def receive() -> Message:
            raise AssertionError("request body is not read")

        async def send(message: Message) -> None:
            sent.append(message)

        policy = replace(
            _POLICY,
            response_headers=((b"cache-control", b"no-store"),),
        )
        await RequestAdmissionMiddleware(
            downstream,
            timeout_seconds=1,
            policies=(policy,),
            liveness_paths=frozenset(),
            default_timeout_response={"code": "default_unavailable"},
        )(_scope(), receive, send)
        return sent

    sent = asyncio.run(scenario())

    assert sent[0]["headers"] == [
        (b"x-retained", b"value"),
        (b"cache-control", b"no-store"),
    ]


def _middleware(app: ASGIApp, *, timeout_seconds: int) -> RequestAdmissionMiddleware:
    return RequestAdmissionMiddleware(
        app,
        timeout_seconds=timeout_seconds,
        policies=(_POLICY,),
        liveness_paths=frozenset({"/healthz"}),
        default_timeout_response={"code": "default_unavailable"},
    )


def _scope() -> Scope:
    return {
        "type": "http",
        "method": "POST",
        "path": "/bounded/42",
        "headers": [],
    }


@pytest.mark.parametrize(
    ("cut", "status_code", "completed", "result", "termination"),
    [
        ("before", 503, True, "timed_out", "work_timeout"),
        ("after_start", 200, False, "timed_out", "work_timeout"),
        ("hard_start", None, False, "timed_out", "work_timeout"),
        ("hard_body", 503, False, "timed_out", "work_timeout"),
        ("suppressed", 200, True, "timed_out", "work_timeout"),
        ("cleanup", 200, True, "issued", "completed"),
        ("second_cancel", None, False, "timed_out", "work_timeout"),
    ],
)
def test_owner_expiry_cut_survives_send_unwind_and_cancellation_suppression(
    cut: str,
    status_code: int | None,
    completed: bool,
    result: str,
    termination: str,
) -> None:
    async def scenario() -> None:
        metrics = RuntimeMetrics()
        output = StringIO()
        logger = logging.Logger("owned-expiry")
        logger.addHandler(logging.StreamHandler(output))
        attempts: list[Message] = []
        calls = 0

        async def downstream(scope: Scope, _: Receive, send: Send) -> None:
            nonlocal calls
            calls += 1
            observation = scope_request_observation(scope)
            assert observation is not None
            observation.plan_authenticated = True
            observation.plan_route_result = "issued"
            if calls > 1:
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"probe"})
                return
            if cut in {"after_start", "cleanup"}:
                await send({"type": "http.response.start", "status": 200, "headers": []})
            if cut == "cleanup":
                await send({"type": "http.response.body", "body": b"complete"})
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if cut == "suppressed":
                    await send({"type": "http.response.start", "status": 200, "headers": []})
                    await send({"type": "http.response.body", "body": b"late"})
                    return
                if cut == "second_cancel":
                    task = asyncio.current_task()
                    assert task is not None
                    task.cancel("external during owned unwind")
                    await asyncio.Event().wait()
                raise

        async def receive() -> Message:
            raise AssertionError("no receive in this deadline-owner witness")

        async def send(message: Message) -> None:
            attempts.append(message)
            if (cut == "hard_start" and message["type"] == "http.response.start") or (
                cut == "hard_body" and message["type"] == "http.response.body"
            ):
                await asyncio.Event().wait()

        observed = HttpRequestObservationMiddleware(
            _middleware(downstream, timeout_seconds=1),
            metrics=metrics,
            plan_metrics=metrics,
            admitted_routes=(),
            logger=StructuredEventLogger(logger),
        )

        async def first() -> None:
            await observed(_scope(), receive, send)

        task = asyncio.create_task(first())
        if cut in {"before", "suppressed"}:
            await task
        else:
            with pytest.raises(
                asyncio.CancelledError
                if cut in {"hard_start", "hard_body", "second_cancel"}
                else TimeoutError
            ):
                await task
        record = json.loads(output.getvalue())
        assert record["statusCode"] == status_code
        assert record["responseCompleted"] is completed
        assert record["termination"] == termination
        assert len([m for m in attempts if m["type"] == "http.response.start"]) <= 1
        counts = {
            dict(labels)["result"]: value
            for (name, labels), value in prometheus_samples(metrics).items()
            if name == "ci_coordinator_plan_requests_total" and value
        }
        assert counts == {result: 1}
        probe: list[Message] = []

        async def probe_send(message: Message) -> None:
            probe.append(message)

        await observed(_scope(), receive, probe_send)
        assert calls == 2 and probe[0]["status"] == 200

    asyncio.run(scenario())


def test_overload_hard_expiry_is_not_misattributed_to_work_or_plan() -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        output = StringIO()
        logger = logging.Logger("hard-only")
        logger.addHandler(logging.StreamHandler(output))
        metrics = RuntimeMetrics()

        async def downstream(_: Scope, __: Receive, send: Send) -> None:
            entered.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive() -> Message:
            raise AssertionError("no body read")

        async def discard(_: Message) -> None:
            pass

        async def blocked(_: Message) -> None:
            release.set()
            await asyncio.Event().wait()

        observed = HttpRequestObservationMiddleware(
            _middleware(downstream, timeout_seconds=1),
            metrics=metrics,
            plan_metrics=metrics,
            admitted_routes=(),
            logger=StructuredEventLogger(logger),
        )
        first = asyncio.create_task(observed(_scope(), receive, discard))
        try:
            await entered.wait()
            with pytest.raises(asyncio.CancelledError):
                await observed(_scope(), receive, blocked)
            await first
        finally:
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        assert {(row["statusCode"], row["termination"]) for row in records} == {
            (204, "completed"),
            (None, "response_timeout"),
        }
        assert not any(
            value
            for (name, _), value in prometheus_samples(metrics).items()
            if name == "ci_coordinator_plan_requests_total"
        )

    asyncio.run(scenario())
