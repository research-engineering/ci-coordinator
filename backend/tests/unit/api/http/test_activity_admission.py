import asyncio
from dataclasses import replace

import pytest
from starlette.types import Message, Receive, Scope, Send

from ci_coordinator.api.http.request_admission import RequestAdmissionMiddleware
from ci_coordinator.api.http.routers.activity import ACTIVITY_REQUEST_LIMITS


def _scope(path: str) -> Scope:
    return {"type": "http", "path": path, "method": "GET"}


@pytest.mark.parametrize(("global_limit", "route_limit"), [(10, 1), (1, 10)])
def test_activity_policy_can_only_shorten_the_runtime_deadline(
    global_limit: int, route_limit: int
) -> None:
    async def scenario() -> None:
        messages: list[Message] = []

        async def downstream(_: Scope, __: Receive, ___: Send) -> None:
            await asyncio.Event().wait()

        async def receive() -> Message:
            raise AssertionError("read admission must not buffer a body")

        async def send(message: Message) -> None:
            messages.append(message)

        policy = replace(ACTIVITY_REQUEST_LIMITS[0], timeout_seconds=route_limit)
        middleware = RequestAdmissionMiddleware(
            downstream,
            timeout_seconds=global_limit,
            policies=(policy,),
            liveness_paths=frozenset(),
            default_timeout_response={},
        )
        await asyncio.wait_for(middleware(_scope(policy.path), receive, send), 3)
        assert messages[0]["status"] == 503
        assert (b"cache-control", b"no-store") in messages[0]["headers"]
        assert messages[1]["body"] == b'{"ok":false,"error":"unavailable"}'

    asyncio.run(scenario())


def test_security_repository_and_export_share_one_nonqueuing_budget() -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        messages: list[Message] = []

        async def downstream(_: Scope, __: Receive, ___: Send) -> None:
            nonlocal calls
            calls += 1
            if calls == 4:
                entered.set()
            await release.wait()

        async def receive() -> Message:
            raise AssertionError("overload cannot enter body handling")

        async def send(message: Message) -> None:
            messages.append(message)

        assert len(ACTIVITY_REQUEST_LIMITS) == 4
        assert all(policy.timeout_seconds == 5 for policy in ACTIVITY_REQUEST_LIMITS)
        middleware = RequestAdmissionMiddleware(
            downstream,
            timeout_seconds=5,
            policies=ACTIVITY_REQUEST_LIMITS,
            liveness_paths=frozenset(),
            default_timeout_response={},
        )
        pending = [
            asyncio.create_task(
                middleware(
                    _scope(
                        policy.path.replace("{installation_id}", "1").replace(
                            "{repository_id}", "2"
                        )
                    ),
                    receive,
                    send,
                )
            )
            for policy in ACTIVITY_REQUEST_LIMITS
        ]
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await middleware(_scope("/api/v1/activity/security/export"), receive, send)
            assert calls == 4
            assert messages[0]["status"] == 503
        finally:
            release.set()
            await asyncio.gather(*pending)

    asyncio.run(scenario())
