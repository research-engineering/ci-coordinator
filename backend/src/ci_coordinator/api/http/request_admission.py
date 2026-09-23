"""One absolute deadline and optional no-queue bulkheads for HTTP requests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass

from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ci_coordinator.kernel import NoQueueAdmission


@dataclass(frozen=True, slots=True)
class RequestAdmissionPolicy:
    path: str
    methods: frozenset[str]
    overload_response: Mapping[str, object]
    timeout_response: Mapping[str, object]
    concurrency_limit: int | None = None
    admission_key: str | None = None
    response_headers: tuple[tuple[bytes, bytes], ...] = ()
    timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and (
            type(self.timeout_seconds) is not int or self.timeout_seconds < 1
        ):
            raise ValueError("request policy timeout must be positive")
        if type(self.path) is not str or not self.path.startswith("/"):
            raise ValueError("request-admission path must be absolute")
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
            raise ValueError("request-admission methods must be uppercase ASCII tokens")
        if (self.concurrency_limit is None) != (self.admission_key is None):
            raise ValueError("request bulkhead limit and identity must be supplied together")
        if self.concurrency_limit is not None and (
            type(self.concurrency_limit) is not int or self.concurrency_limit < 1
        ):
            raise ValueError("request bulkhead concurrency must be positive")
        if self.admission_key is not None and (
            type(self.admission_key) is not str or not self.admission_key
        ):
            raise ValueError("request bulkhead identity must be non-empty text")
        if type(self.response_headers) is not tuple or any(
            type(header) is not tuple
            or len(header) != 2
            or type(header[0]) is not bytes
            or type(header[1]) is not bytes
            or not header[0]
            or header[0] != header[0].lower()
            or not header[0].isascii()
            or any(character in header[1] for character in (b"\r", b"\n"))
            for header in self.response_headers
        ):
            raise ValueError("request-admission response headers are invalid")
        names = tuple(name for name, _ in self.response_headers)
        if len(names) != len(set(names)):
            raise ValueError("request-admission response headers must be unique")


class RequestAdmissionMiddleware:
    """Apply one process-owned deadline and exact class bulkheads."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        timeout_seconds: int,
        policies: tuple[RequestAdmissionPolicy, ...],
        liveness_paths: frozenset[str],
        default_timeout_response: Mapping[str, object],
    ) -> None:
        if type(timeout_seconds) is not int or timeout_seconds < 1:
            raise ValueError("request timeout must be a positive integer")
        if type(policies) is not tuple:
            raise TypeError("request-admission policies must be an exact tuple")
        identities = tuple((policy.path, policy.methods) for policy in policies)
        if len(identities) != len(set(identities)):
            raise ValueError("request-admission path and method policies must be unique")
        if type(liveness_paths) is not frozenset or any(
            type(path) is not str or not path.startswith("/") for path in liveness_paths
        ):
            raise ValueError("liveness paths must be an exact set of absolute paths")
        limits: dict[str, int] = {}
        for policy in policies:
            if policy.admission_key is None or policy.concurrency_limit is None:
                continue
            previous = limits.setdefault(policy.admission_key, policy.concurrency_limit)
            if previous != policy.concurrency_limit:
                raise ValueError("one request bulkhead identity must have one limit")
        self._app = app
        self._timeout_seconds = timeout_seconds
        self._matchers = tuple((compile_path(policy.path)[0], policy) for policy in policies)
        self._liveness_paths = liveness_paths
        self._default_timeout_response = default_timeout_response
        self._admissions = {key: NoQueueAdmission(limit) for key, limit in limits.items()}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self._liveness_paths:
            await self._app(scope, receive, send)
            return
        policy = self._policy_for_scope(scope)
        timeout_seconds = min(
            self._timeout_seconds,
            self._timeout_seconds
            if policy is None or policy.timeout_seconds is None
            else policy.timeout_seconds,
        )
        hard_deadline = asyncio.get_running_loop().time() + timeout_seconds
        send_reserve = min(1.0, timeout_seconds / 10)
        hard_timeout = asyncio.timeout_at(hard_deadline)
        try:
            async with hard_timeout:
                lease = None
                if policy is not None and policy.admission_key is not None:
                    lease = self._admissions[policy.admission_key].try_acquire()
                    if lease is None:
                        await _send_json(send, 503, policy.overload_response)
                        return
                response_started = False

                async def tracked_send(message: Message) -> None:
                    nonlocal response_started
                    if message["type"] == "http.response.start":
                        response_started = True
                        if policy is not None and policy.response_headers:
                            message = _with_response_headers(message, policy.response_headers)
                    await send(message)

                work_timeout = asyncio.timeout_at(hard_deadline - send_reserve)
                timed_out = False
                try:
                    async with work_timeout:
                        await self._app(scope, receive, tracked_send)
                except TimeoutError:
                    if not work_timeout.expired() or response_started:
                        raise
                    timed_out = True
                finally:
                    if lease is not None:
                        lease.release()
                if timed_out:
                    payload = (
                        self._default_timeout_response
                        if policy is None
                        else policy.timeout_response
                    )
                    await _send_json(send, 503, payload)
        except TimeoutError:
            if hard_timeout.expired():
                raise asyncio.CancelledError("request response deadline expired") from None
            raise

    def _policy_for_scope(self, scope: Scope) -> RequestAdmissionPolicy | None:
        path = scope["path"]
        method = str(scope.get("method", "")).upper()
        matches = tuple(
            policy
            for matcher, policy in self._matchers
            if method in policy.methods and matcher.fullmatch(path)
        )
        if len(matches) > 1:
            raise RuntimeError("request-admission policies are ambiguous")
        return matches[0] if matches else None


def _with_response_headers(
    message: Message,
    required: tuple[tuple[bytes, bytes], ...],
) -> Message:
    names = {name for name, _ in required}
    headers = [
        (name, value) for name, value in message.get("headers", ()) if name.lower() not in names
    ]
    return {**message, "headers": [*headers, *required]}


async def _send_json(send: Send, status_code: int, payload: Mapping[str, object]) -> None:
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
