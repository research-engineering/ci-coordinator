from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ci_coordinator.api.http.body_limits import BodyLimitPolicy


def body_policy(path: str, maximum: int = 8) -> BodyLimitPolicy:
    return BodyLimitPolicy(
        path=path,
        maximum_body_bytes=maximum,
        invalid_content_length_response={"code": "invalid"},
        too_large_response={"code": "large"},
        overload_response={"code": "overload"},
        timeout_response={"code": "timeout"},
    )


def body_message(body: object = b"", *, more: bool = False) -> Message:
    return {"type": "http.request", "body": body, "more_body": more}


def body_scope(path: str, declared: bytes | None = None) -> Scope:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [] if declared is None else [(b"content-length", declared)],
    }


@dataclass
class BodyExchange:
    messages: tuple[Message, ...]
    sent: list[Message] = field(default_factory=list)
    reads: int = 0

    async def receive(self) -> Message:
        assert self.reads < len(self.messages), "unexpected receive beyond the supplied body"
        message = self.messages[self.reads]
        self.reads += 1
        return message

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    def assert_response(self, status: int, body: bytes) -> None:
        assert len(self.sent) == 2
        assert self.sent[0]["type"] == "http.response.start"
        assert self.sent[0]["status"] == status
        assert (b"cache-control", b"no-store") in self.sent[0]["headers"]
        assert self.sent[1]["type"] == "http.response.body"
        assert self.sent[1]["body"] == body


async def discard(_: Message) -> None:
    return None


async def respond_empty(send: Send) -> None:
    await send(
        {"type": "http.response.start", "status": 204, "headers": [(b"cache-control", b"no-store")]}
    )
    await send({"type": "http.response.body", "body": b""})


async def wait_until(event: asyncio.Event) -> None:
    # A deadlock guard only; elapsed time is never a correctness oracle.
    await asyncio.wait_for(event.wait(), timeout=10)


@asynccontextmanager
async def running_request(
    app: ASGIApp, scope: Scope, receive: Receive, send: Send = discard
) -> AsyncIterator[asyncio.Task[None]]:
    async def invoke() -> None:
        await app(scope, receive, send)

    task = asyncio.create_task(invoke())
    try:
        yield task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
