from __future__ import annotations

import asyncio

import pytest
from starlette.types import Message, Receive, Scope, Send

from ci_coordinator.api.http.body_limits import (
    BodyLimitPolicy,
    RequestBodyLimitMiddleware,
)
from ci_coordinator.api.http.routers.operator_controls import (
    MAX_OPERATOR_OVERRIDE_BODY_BYTES,
    OPERATOR_OVERRIDE_PATH,
)
from ci_coordinator.api.http.routers.plan_requests import (
    MAX_PLAN_REQUEST_BODY_BYTES,
    PLAN_REQUEST_BODY_LIMIT,
    PLAN_REQUEST_PATH,
)


def test_chunked_plan_body_is_bounded_before_the_downstream_app() -> None:
    downstream_calls = 0
    sent: list[Message] = []
    messages: list[Message] = [
        {"type": "http.request", "body": b"x" * MAX_PLAN_REQUEST_BODY_BYTES, "more_body": True},
        {"type": "http.request", "body": b"x", "more_body": False},
    ]

    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        nonlocal downstream_calls
        downstream_calls += 1

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": PLAN_REQUEST_PATH,
        "headers": [],
    }

    asyncio.run(
        RequestBodyLimitMiddleware(
            downstream,
            policies=(PLAN_REQUEST_BODY_LIMIT,),
        )(scope, receive, send)
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 413
    assert (b"cache-control", b"no-store") in sent[0]["headers"]
    assert sent[1]["body"] == b'{"code":"request_too_large"}'


def test_ambiguous_content_length_is_rejected_before_the_downstream_app() -> None:
    downstream_calls = 0
    sent: list[Message] = []

    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        nonlocal downstream_calls
        downstream_calls += 1

    async def receive() -> Message:
        raise AssertionError("ambiguous request must not be read")

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": PLAN_REQUEST_PATH,
        "headers": [(b"content-length", b"1"), (b"content-length", b"1")],
    }

    asyncio.run(
        RequestBodyLimitMiddleware(
            downstream,
            policies=(PLAN_REQUEST_BODY_LIMIT,),
        )(scope, receive, send)
    )

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert sent[1]["body"] == b'{"code":"invalid_request"}'


def test_webhook_policy_retains_its_own_typed_oversize_response() -> None:
    downstream_calls = 0
    sent: list[Message] = []
    messages: list[Message] = [{"type": "http.request", "body": b"xxx", "more_body": False}]

    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        nonlocal downstream_calls
        downstream_calls += 1

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    policy = BodyLimitPolicy(
        path="/webhooks/github",
        maximum_body_bytes=2,
        invalid_content_length_response={"ok": False, "error": "invalid webhook"},
        too_large_response={"ok": False, "error": "request body too large"},
        overload_response={"ok": False, "error": "webhook ingestion overloaded"},
        timeout_response={"ok": False, "error": "webhook ingestion unavailable"},
    )
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/webhooks/github",
        "headers": [],
    }

    asyncio.run(RequestBodyLimitMiddleware(downstream, policies=(policy,))(scope, receive, send))

    assert downstream_calls == 0
    assert sent[0]["status"] == 413
    assert sent[1]["body"] == b'{"ok":false,"error":"request body too large"}'


def test_operator_body_is_bounded_before_authentication_or_parsing() -> None:
    downstream_calls = 0
    sent: list[Message] = []

    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        nonlocal downstream_calls
        downstream_calls += 1

    async def receive() -> Message:
        raise AssertionError("declared oversized request must not be read")

    async def send(message: Message) -> None:
        sent.append(message)

    policy = BodyLimitPolicy(
        path=OPERATOR_OVERRIDE_PATH,
        maximum_body_bytes=MAX_OPERATOR_OVERRIDE_BODY_BYTES,
        invalid_content_length_response={"ok": False, "error": "invalid_override"},
        too_large_response={"ok": False, "error": "invalid_override"},
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
    )
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": OPERATOR_OVERRIDE_PATH,
        "headers": [(b"content-length", str(MAX_OPERATOR_OVERRIDE_BODY_BYTES + 1).encode())],
    }

    asyncio.run(RequestBodyLimitMiddleware(downstream, policies=(policy,))(scope, receive, send))

    assert downstream_calls == 0
    assert sent[0]["status"] == 413
    assert sent[1]["body"] == b'{"ok":false,"error":"invalid_override"}'


def test_retained_body_admission_is_isolated_by_route() -> None:
    async def scenario() -> tuple[bool, bool]:
        slow_downstream_started = asyncio.Event()
        release_slow_downstream = asyncio.Event()
        fast_completed = asyncio.Event()

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            assert (await receive())["type"] == "http.request"
            if scope["path"] == "/slow":
                slow_downstream_started.set()
                await release_slow_downstream.wait()
            else:
                fast_completed.set()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        policies = tuple(
            BodyLimitPolicy(
                path=path,
                maximum_body_bytes=16,
                invalid_content_length_response={"code": "invalid"},
                too_large_response={"code": "large"},
                overload_response={"code": "overload"},
                timeout_response={"code": "timeout"},
            )
            for path in ("/slow", "/fast")
        )
        middleware = RequestBodyLimitMiddleware(
            downstream,
            policies=policies,
            max_concurrent_requests=1,
        )

        async def invoke(path: str) -> None:
            messages: list[Message] = [{"type": "http.request", "body": b"{}", "more_body": False}]

            async def receive() -> Message:
                if messages:
                    return messages.pop(0)
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            async def send(_: Message) -> None:
                return None

            scope: Scope = {"type": "http", "method": "POST", "path": path, "headers": []}
            await middleware(scope, receive, send)

        slow = asyncio.create_task(invoke("/slow"))
        await slow_downstream_started.wait()
        fast = asyncio.create_task(invoke("/fast"))
        await asyncio.wait_for(fast_completed.wait(), timeout=1)
        independent = fast_completed.is_set()
        release_slow_downstream.set()
        await asyncio.gather(slow, fast)
        return independent, slow_downstream_started.is_set()

    assert asyncio.run(scenario()) == (True, True)


def test_body_lane_rejects_without_queue_while_bodies_are_retained() -> None:
    async def scenario() -> tuple[int, int, int, bytes]:
        two_requests_admitted = asyncio.Event()
        release_downstream = asyncio.Event()
        downstream_calls = 0
        body_reads = 0
        rejected = 0
        rejection_body = b""

        async def downstream(_: Scope, receive: Receive, __: Send) -> None:
            nonlocal downstream_calls
            assert (await receive())["body"] == b"{}"
            downstream_calls += 1
            if downstream_calls == 2:
                two_requests_admitted.set()
            await release_downstream.wait()

        policy = BodyLimitPolicy(
            path="/body",
            maximum_body_bytes=16,
            invalid_content_length_response={"code": "invalid"},
            too_large_response={"code": "large"},
            overload_response={"code": "overload"},
            timeout_response={"code": "timeout"},
        )
        middleware = RequestBodyLimitMiddleware(
            downstream,
            policies=(policy,),
            max_concurrent_requests=2,
        )

        async def invoke() -> None:
            nonlocal rejected, rejection_body
            delivered = False

            async def receive() -> Message:
                nonlocal body_reads, delivered
                if not delivered:
                    delivered = True
                    body_reads += 1
                    return {"type": "http.request", "body": b"{}", "more_body": False}
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            async def send(message: Message) -> None:
                nonlocal rejected, rejection_body
                if message["type"] == "http.response.start" and message["status"] == 503:
                    rejected += 1
                if message["type"] == "http.response.body":
                    rejection_body = message.get("body", b"")

            scope: Scope = {
                "type": "http",
                "method": "POST",
                "path": "/body",
                "headers": [],
            }
            await middleware(scope, receive, send)

        requests = tuple(asyncio.create_task(invoke()) for _ in range(3))
        await asyncio.wait_for(two_requests_admitted.wait(), timeout=1)
        await asyncio.sleep(0)
        reads_while_two_bodies_are_retained = body_reads
        release_downstream.set()
        await asyncio.gather(*requests)
        return reads_while_two_bodies_are_retained, body_reads, rejected, rejection_body

    assert asyncio.run(scenario()) == (2, 2, 1, b'{"code":"overload"}')


def test_process_byte_budget_is_shared_across_route_lanes() -> None:
    async def scenario() -> tuple[int, int, bytes]:
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        downstream_calls = 0
        second_body_reads = 0
        rejected: list[Message] = []

        async def downstream(scope: Scope, receive: Receive, _: Send) -> None:
            nonlocal downstream_calls
            assert (await receive())["body"] == b"12345678"
            downstream_calls += 1
            if scope["path"] == "/first":
                first_started.set()
                await release_first.wait()

        policies = tuple(
            BodyLimitPolicy(
                path=path,
                maximum_body_bytes=8,
                invalid_content_length_response={"code": "invalid"},
                too_large_response={"code": "large"},
                overload_response={"code": "overload"},
                timeout_response={"code": "timeout"},
            )
            for path in ("/first", "/second")
        )
        middleware = RequestBodyLimitMiddleware(
            downstream,
            policies=policies,
            maximum_retained_body_bytes=8,
        )

        async def invoke(path: str, send: Send) -> None:
            delivered = False

            async def receive() -> Message:
                nonlocal delivered, second_body_reads
                if path == "/second":
                    second_body_reads += 1
                if delivered:
                    raise AssertionError("body must be read once")
                delivered = True
                return {"type": "http.request", "body": b"12345678", "more_body": False}

            await middleware(
                {
                    "type": "http",
                    "method": "POST",
                    "path": path,
                    "headers": [(b"content-length", b"8")],
                },
                receive,
                send,
            )

        async def discard(_: Message) -> None:
            return None

        async def capture(message: Message) -> None:
            rejected.append(message)

        first = asyncio.create_task(invoke("/first", discard))
        await first_started.wait()
        await invoke("/second", capture)
        release_first.set()
        await first
        return downstream_calls, second_body_reads, rejected[-1]["body"]

    assert asyncio.run(scenario()) == (1, 0, b'{"code":"overload"}')


@pytest.mark.parametrize(
    ("declared", "body"),
    [(b"1", b""), (b"1", b"12"), (b"8", b"1234")],
)
def test_declared_body_length_must_equal_retained_bytes(declared: bytes, body: bytes) -> None:
    downstream_calls = 0
    sent: list[Message] = []

    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        nonlocal downstream_calls
        downstream_calls += 1

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    policy = BodyLimitPolicy(
        path="/body",
        maximum_body_bytes=8,
        invalid_content_length_response={"code": "invalid"},
        too_large_response={"code": "large"},
        overload_response={"code": "overload"},
        timeout_response={"code": "timeout"},
    )
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/body",
        "headers": [(b"content-length", declared)],
    }

    asyncio.run(RequestBodyLimitMiddleware(downstream, policies=(policy,))(scope, receive, send))

    assert downstream_calls == 0
    assert sent[0]["status"] == 400
    assert sent[1]["body"] == b'{"code":"invalid"}'


def test_body_policy_does_not_intercept_an_unlisted_method() -> None:
    downstream_calls = 0

    async def downstream(_: Scope, __: Receive, send: Send) -> None:
        nonlocal downstream_calls
        downstream_calls += 1
        await send({"type": "http.response.start", "status": 405, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:
        raise AssertionError("method-ineligible body must not be read")

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": PLAN_REQUEST_PATH,
        "headers": [(b"content-length", b"999999")],
    }

    asyncio.run(
        RequestBodyLimitMiddleware(
            downstream,
            policies=(PLAN_REQUEST_BODY_LIMIT,),
        )(scope, receive, send)
    )

    assert downstream_calls == 1
    assert sent[0]["status"] == 405


def test_downstream_timeout_error_is_not_a_middleware_deadline() -> None:
    async def downstream(_: Scope, __: Receive, ___: Send) -> None:
        raise TimeoutError("downstream defect")

    messages: list[Message] = [{"type": "http.request", "body": b"{}", "more_body": False}]

    async def receive() -> Message:
        return messages.pop(0)

    async def send(_: Message) -> None:
        return None

    policy = BodyLimitPolicy(
        path="/body",
        maximum_body_bytes=16,
        invalid_content_length_response={"code": "invalid"},
        too_large_response={"code": "large"},
        overload_response={"code": "overload"},
        timeout_response={"code": "timeout"},
    )
    scope: Scope = {"type": "http", "method": "POST", "path": "/body", "headers": []}

    with pytest.raises(TimeoutError, match="downstream defect"):
        asyncio.run(
            RequestBodyLimitMiddleware(downstream, policies=(policy,))(scope, receive, send)
        )


def test_replayed_body_delegates_subsequent_receive_to_the_client() -> None:
    observed: list[str] = []
    client_messages: list[Message] = [
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def downstream(_: Scope, receive: Receive, send: Send) -> None:
        observed.append((await receive())["type"])
        observed.append((await receive())["type"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:
        return client_messages.pop(0)

    async def send(_: Message) -> None:
        return None

    policy = BodyLimitPolicy(
        path="/body",
        maximum_body_bytes=16,
        invalid_content_length_response={"code": "invalid"},
        too_large_response={"code": "large"},
        overload_response={"code": "overload"},
        timeout_response={"code": "timeout"},
    )
    scope: Scope = {"type": "http", "method": "POST", "path": "/body", "headers": []}

    asyncio.run(RequestBodyLimitMiddleware(downstream, policies=(policy,))(scope, receive, send))

    assert observed == ["http.request", "http.disconnect"]
