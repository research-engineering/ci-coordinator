"""Bounded no-queue admission for unauthenticated public HTTP routes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import ceil, isfinite
from time import monotonic

from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(frozen=True, slots=True)
class PublicRequestLimitPolicy:
    path: str
    methods: frozenset[str]
    burst: int
    refill_rate_per_second: float
    rejection_response: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.path) is not str or not self.path.startswith("/"):
            raise ValueError("public request-limit path must be absolute")
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
            raise ValueError("public request-limit methods must be uppercase ASCII tokens")
        if type(self.burst) is not int or self.burst < 1:
            raise ValueError("public request-limit burst must be positive")
        if (
            type(self.refill_rate_per_second) is not float
            or not isfinite(self.refill_rate_per_second)
            or self.refill_rate_per_second <= 0
        ):
            raise ValueError("public request-limit refill rate must be a finite positive float")


@dataclass(slots=True)
class _TokenBucket:
    tokens: float
    updated_at: float


class PublicRequestLimitMiddleware:
    """Apply fixed-cardinality process-local token buckets without waiter queues."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        policies: tuple[PublicRequestLimitPolicy, ...],
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if type(policies) is not tuple:
            raise TypeError("public request-limit policies must be an exact tuple")
        by_path = {policy.path: policy for policy in policies}
        if len(by_path) != len(policies):
            raise ValueError("public request-limit policies must not repeat a path")
        self._app = app
        self._matchers = tuple((compile_path(policy.path)[0], policy) for policy in policies)
        self._clock = clock
        now = clock()
        if not isfinite(now):
            raise ValueError("public request-limit clock must return a finite value")
        self._buckets = {policy.path: _TokenBucket(float(policy.burst), now) for policy in policies}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        policy = self._policy_for_scope(scope) if scope["type"] == "http" else None
        if policy is None:
            await self._app(scope, receive, send)
            return
        retry_after = self._consume(policy)
        if retry_after is not None:
            await _send_rejection(send, policy.rejection_response, retry_after)
            return
        await self._app(scope, receive, send)

    def _policy_for_scope(self, scope: Scope) -> PublicRequestLimitPolicy | None:
        path = scope["path"]
        method = str(scope.get("method", "")).upper()
        matches = tuple(
            policy
            for matcher, policy in self._matchers
            if method in policy.methods and matcher.fullmatch(path)
        )
        if len(matches) > 1:
            raise RuntimeError("public request-limit policies are ambiguous")
        return matches[0] if matches else None

    def _consume(self, policy: PublicRequestLimitPolicy) -> int | None:
        now = self._clock()
        bucket = self._buckets[policy.path]
        if not isfinite(now):
            return 1
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(
            float(policy.burst),
            bucket.tokens + elapsed * policy.refill_rate_per_second,
        )
        if now >= bucket.updated_at:
            bucket.updated_at = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None
        return max(1, ceil((1.0 - bucket.tokens) / policy.refill_rate_per_second))


async def _send_rejection(
    send: Send,
    payload: Mapping[str, object],
    retry_after_seconds: int,
) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    start: Message = {
        "type": "http.response.start",
        "status": 429,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"retry-after", str(retry_after_seconds).encode("ascii")),
        ],
    }
    await send(start)
    await send({"type": "http.response.body", "body": body})
