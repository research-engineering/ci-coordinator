import asyncio

from starlette.types import Message, Receive, Scope, Send

from ci_coordinator.api.http.body_limits import BodyLimitPolicy, RequestBodyLimitMiddleware


def test_extreme_decimal_content_length_is_rejected_without_integer_materialization() -> None:
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
        path="/bounded",
        maximum_body_bytes=1024,
        invalid_content_length_response={"code": "invalid_request"},
        too_large_response={"code": "request_too_large"},
        overload_response={"code": "overloaded"},
        timeout_response={"code": "timeout"},
    )
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": policy.path,
        "headers": [(b"content-length", b"9" * 5_000)],
    }
    asyncio.run(RequestBodyLimitMiddleware(downstream, policies=(policy,))(scope, receive, send))
    assert downstream_calls == 0
    assert sent[0]["status"] == 413
    assert sent[1]["body"] == b'{"code":"request_too_large"}'
