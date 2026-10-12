"""Bounded no-queue admission for unauthenticated public HTTP routes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import ceil, isfinite
from time import monotonic

from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ci_coordinator.observability import RuntimeMetrics


@dataclass(frozen=True, slots=True)
class PublicRequestLimitPolicy:
    path: str
    methods: frozenset[str]
    burst: int
    refill_rate_per_second: float
    rejection_response: Mapping[str, object]
    route_class: str

    def __post_init__(self) -> None:
        if type(self.path) is not str or not self.path.startswith("/"):
            raise ValueError("public request-limit path must be absolute")
        if not _is_route_class_token(self.route_class):
            raise ValueError("public request-limit route class must be a lowercase token")
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
class TokenBucket:
    """One finite process-local refill budget whose callers never wait."""

    burst: float
    refill_rate_per_second: float
    tokens: float
    updated_at: float

    def refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.burst, self.tokens + elapsed * self.refill_rate_per_second)
        if now >= self.updated_at:
            self.updated_at = now


def try_take_tokens(now: float, charges: tuple[tuple[TokenBucket, float], ...]) -> int | None:
    """Take every charge or none; a refusal returns a whole-second retry hint."""
    if not isfinite(now):
        return 1
    for bucket, _ in charges:
        bucket.refill(now)
    shortfalls = tuple(
        (cost - bucket.tokens) / bucket.refill_rate_per_second
        for bucket, cost in charges
        if bucket.tokens < cost
    )
    if shortfalls:
        return max(1, ceil(max(shortfalls)))
    for bucket, cost in charges:
        bucket.tokens -= cost
    return None


def _is_route_class_token(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 64
        and all(character in "abcdefghijklmnopqrstuvwxyz_" for character in value)
    )


class PublicRequestLimitMiddleware:
    """Apply fixed-cardinality process-local token buckets without waiter queues."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        policies: tuple[PublicRequestLimitPolicy, ...],
        clock: Callable[[], float] = monotonic,
        metrics: RuntimeMetrics | None = None,
    ) -> None:
        if type(policies) is not tuple:
            raise TypeError("public request-limit policies must be an exact tuple")
        by_path = {policy.path: policy for policy in policies}
        if len(by_path) != len(policies):
            raise ValueError("public request-limit policies must not repeat a path")
        self._app = app
        self._matchers = tuple((compile_path(policy.path)[0], policy) for policy in policies)
        self._clock = clock
        self._metrics = metrics
        now = clock()
        if not isfinite(now):
            raise ValueError("public request-limit clock must return a finite value")
        self._buckets = {
            policy.path: TokenBucket(
                float(policy.burst), policy.refill_rate_per_second, float(policy.burst), now
            )
            for policy in policies
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        policy = self._policy_for_scope(scope) if scope["type"] == "http" else None
        if policy is None:
            await self._app(scope, receive, send)
            return
        retry_after = try_take_tokens(self._clock(), ((self._buckets[policy.path], 1.0),))
        if retry_after is not None:
            if self._metrics is not None:
                self._metrics.public_request_limited(policy.route_class)
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
