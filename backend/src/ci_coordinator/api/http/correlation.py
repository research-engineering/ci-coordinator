"""Service-owned request correlation for HTTP diagnostics."""

from __future__ import annotations

import re
import secrets
from contextvars import ContextVar, Token
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_ID_HEADER: Final = b"x-correlation-id"
_CORRELATION_ID: ContextVar[str | None] = ContextVar("ci_correlation_id", default=None)
_CORRELATION_ID_PATTERN: Final = re.compile(r"^[0-9a-f]{32}$")


def current_correlation_id() -> str | None:
    """Return the service-owned identity for the current request context."""
    return _CORRELATION_ID.get()


def scope_correlation_id(scope: Scope) -> str | None:
    """Return only a service-shaped correlation identity from ASGI state."""
    value = scope.get("state", {}).get("correlation_id")
    return value if type(value) is str and _CORRELATION_ID_PATTERN.fullmatch(value) else None


class CorrelationIdMiddleware:
    """Bind one unpredictable identity to one HTTP request and response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        correlation_id = secrets.token_hex(16)
        state = scope.setdefault("state", {})
        state["correlation_id"] = correlation_id
        token = _CORRELATION_ID.set(correlation_id)

        async def correlated_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    item
                    for item in message.get("headers", ())
                    if item[0].lower() != CORRELATION_ID_HEADER
                ]
                message = {
                    **message,
                    "headers": [
                        *headers,
                        (CORRELATION_ID_HEADER, correlation_id.encode("ascii")),
                    ],
                }
            await send(message)

        try:
            await self._app(scope, receive, correlated_send)
        finally:
            _reset_correlation_id(token)


def _reset_correlation_id(token: Token[str | None]) -> None:
    _CORRELATION_ID.reset(token)
