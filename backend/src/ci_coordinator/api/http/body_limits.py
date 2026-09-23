"""Path-owned raw-body limits applied before FastAPI parsing or authentication."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ci_coordinator.kernel import NoQueueAdmission, WeightedNoQueueAdmission

MAX_CONCURRENT_BODY_REQUESTS: Final = 8
DEFAULT_MAXIMUM_RETAINED_BODY_BYTES: Final = 32 * 1_024 * 1_024
_ASCII_ZERO: Final = ord("0")
_INVALID_CONTENT_LENGTH: Final = -1


@dataclass(frozen=True, slots=True)
class BodyLimitPolicy:
    path: str
    maximum_body_bytes: int
    invalid_content_length_response: Mapping[str, object]
    too_large_response: Mapping[str, object]
    overload_response: Mapping[str, object]
    timeout_response: Mapping[str, object]
    methods: frozenset[str] = frozenset({"POST"})

    def __post_init__(self) -> None:
        if type(self.path) is not str or not self.path.startswith("/"):
            raise ValueError("body-limit path must be an absolute path")
        if type(self.maximum_body_bytes) is not int or self.maximum_body_bytes < 1:
            raise ValueError("body-limit maximum must be positive")
        if (
            type(self.methods) is not frozenset
            or not self.methods
            or any(
                type(method) is not str
                or not method
                or not method.isascii()
                or method != method.upper()
                for method in self.methods
            )
        ):
            raise ValueError("body-limit methods must be uppercase ASCII tokens")


class RequestBodyLimitMiddleware:
    """Bound body bytes and process-retained body concurrency."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        policies: tuple[BodyLimitPolicy, ...],
        max_concurrent_requests: int = MAX_CONCURRENT_BODY_REQUESTS,
        maximum_retained_body_bytes: int = DEFAULT_MAXIMUM_RETAINED_BODY_BYTES,
    ) -> None:
        self._app = app
        self._policies = {policy.path: policy for policy in policies}
        if len(self._policies) != len(policies):
            raise ValueError("body-limit policies must not repeat a path")
        self._path_matchers = tuple((compile_path(policy.path)[0], policy) for policy in policies)
        if type(max_concurrent_requests) is not int or max_concurrent_requests < 1:
            raise ValueError("body admission concurrency must be positive")
        if (
            type(maximum_retained_body_bytes) is not int
            or maximum_retained_body_bytes < 1
            or any(policy.maximum_body_bytes > maximum_retained_body_bytes for policy in policies)
        ):
            raise ValueError("retained-body budget must admit every route maximum")
        self._admissions = {
            path: NoQueueAdmission(max_concurrent_requests) for path in self._policies
        }
        self._retained_body_budget = WeightedNoQueueAdmission(maximum_retained_body_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        policy = self._policy_for_scope(scope) if scope["type"] == "http" else None
        if policy is None:
            await self._app(scope, receive, send)
            return
        content_length = _bounded_content_length(
            scope.get("headers", ()),
            policy.maximum_body_bytes,
        )
        if content_length == _INVALID_CONTENT_LENGTH:
            await _send_json(send, 400, policy.invalid_content_length_response)
            return
        if content_length is not None and content_length > policy.maximum_body_bytes:
            await _send_json(send, 413, policy.too_large_response)
            return
        path_lease = self._admissions[policy.path].try_acquire()
        if path_lease is None:
            await _send_json(send, 503, policy.overload_response)
            return
        reservation = policy.maximum_body_bytes if content_length is None else content_length
        body_lease = self._retained_body_budget.try_acquire(reservation)
        if body_lease is None:
            path_lease.release()
            await _send_json(send, 503, policy.overload_response)
            return
        try:
            # Both leases remain owned while downstream can retain the replayed
            # body; releasing after reading would not bound retained bytes.
            body = await _read_bounded_body(
                receive,
                policy.maximum_body_bytes,
                expected_body_bytes=content_length,
            )
            if body is None:
                return
            await self._app(scope, _replay_body(body, receive), send)
        except _BodyLengthMismatch:
            await _send_json(send, 400, policy.invalid_content_length_response)
        except _BodyTooLarge:
            await _send_json(send, 413, policy.too_large_response)
        finally:
            body_lease.release()
            path_lease.release()

    def _policy_for_scope(self, scope: Scope) -> BodyLimitPolicy | None:
        path = scope["path"]
        method = str(scope.get("method", "")).upper()
        matches = tuple(
            policy
            for matcher, policy in self._path_matchers
            if method in policy.methods and matcher.fullmatch(path)
        )
        if len(matches) > 1:
            raise RuntimeError("body-limit path policies are ambiguous")
        return matches[0] if matches else None


def _bounded_content_length(
    headers: Sequence[tuple[bytes, bytes]],
    maximum_body_bytes: int,
) -> int | None:
    """Return absent, invalid, admitted, or one bounded oversized sentinel."""
    values = [value for name, value in headers if name.lower() == b"content-length"]
    if not values:
        return None
    if len(values) != 1 or not values[0].isdigit():
        return _INVALID_CONTENT_LENGTH
    declared = 0
    for digit in values[0]:
        declared = declared * 10 + digit - _ASCII_ZERO
        if declared > maximum_body_bytes:
            return maximum_body_bytes + 1
    return declared


async def _read_bounded_body(
    receive: Receive,
    maximum_body_bytes: int,
    *,
    expected_body_bytes: int | None,
) -> bytes | None:
    chunks: list[bytes] = []
    body_bytes = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        if type(chunk) is not bytes:
            raise _BodyLengthMismatch
        body_bytes += len(chunk)
        if body_bytes > maximum_body_bytes:
            raise _BodyTooLarge
        if expected_body_bytes is not None and body_bytes > expected_body_bytes:
            raise _BodyLengthMismatch
        if chunk:
            chunks.append(chunk)
        if not message.get("more_body", False):
            if expected_body_bytes is not None and body_bytes != expected_body_bytes:
                raise _BodyLengthMismatch
            if not chunks:
                return b""
            if len(chunks) == 1:
                return chunks[0]
            return b"".join(chunks)


def _replay_body(body: bytes, receive_after_body: Receive) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return await receive_after_body()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class _BodyTooLarge(Exception):
    pass


class _BodyLengthMismatch(Exception):
    pass


async def _send_json(send: Send, status_code: int, payload: Mapping[str, object]) -> None:
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    start: Message = {
        "type": "http.response.start",
        "status": status_code,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ],
    }
    await send(start)
    await send({"type": "http.response.body", "body": body})
