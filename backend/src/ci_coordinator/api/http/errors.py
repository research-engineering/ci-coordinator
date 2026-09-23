"""Redacted transport errors with stable public codes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException
from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ci_coordinator.api.http.correlation import scope_correlation_id
from ci_coordinator.api.http.request_admission import RequestAdmissionPolicy
from ci_coordinator.api.http.security import WWW_AUTHENTICATE_HEADER
from ci_coordinator.observability import RuntimeDiagnosticObserver
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable


@dataclass(frozen=True, slots=True)
class ResponseCookieCleanupPolicy:
    """Delete one path-scoped cookie on every response from an exact HTTP route."""

    path: str
    methods: frozenset[str]
    name: str
    cookie_path: str
    secure: bool
    httponly: bool
    samesite: Literal["lax", "strict", "none"]

    def __post_init__(self) -> None:
        if type(self.path) is not str or not self.path.startswith("/"):
            raise ValueError("cookie-cleanup route path must be absolute")
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
            raise ValueError("cookie-cleanup methods must be uppercase ASCII tokens")
        if type(self.name) is not str or not self.name or not self.name.isascii():
            raise ValueError("cookie-cleanup name must be non-empty ASCII text")
        if type(self.cookie_path) is not str or not self.cookie_path.startswith("/"):
            raise ValueError("cookie-cleanup cookie path must be absolute")
        if type(self.secure) is not bool or type(self.httponly) is not bool:
            raise TypeError("cookie-cleanup flags must be exact booleans")


class TransportError(Exception):
    """A public, redacted error whose HTTP mapping is owned by this module."""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


async def request_validation_error(_: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise TypeError("validation error handler received an unexpected exception")
    return JSONResponse(
        status_code=422,
        content={"code": "invalid_request"},
        headers={"Cache-Control": "no-store"},
    )


async def framework_http_error(request: Request, error: Exception) -> Response:
    if not isinstance(error, HTTPException):
        raise TypeError("HTTP error handler received an unexpected exception")
    if error.status_code == 400 and isinstance(error.__cause__, UnicodeDecodeError):
        return JSONResponse(
            status_code=422,
            content={"code": "invalid_request"},
            headers={"Cache-Control": "no-store"},
        )
    return await http_exception_handler(request, error)


async def transport_error_response(_: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, TransportError):
        raise TypeError("transport error handler received an unexpected exception")
    headers = {"Cache-Control": "no-store"}
    if error.status_code == 401:
        headers.update(WWW_AUTHENTICATE_HEADER)
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code},
        headers=headers,
    )


def repository_access_error_handler(
    policies: tuple[RequestAdmissionPolicy, ...],
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    matchers = tuple((compile_path(policy.path)[0], policy) for policy in policies)

    async def respond(request: Request, error: Exception) -> JSONResponse:
        if not isinstance(error, RepositoryAccessUnavailable):
            raise TypeError("repository access handler received an unexpected exception")
        matches = tuple(
            policy
            for matcher, policy in matchers
            if request.method in policy.methods and matcher.fullmatch(request.scope["path"])
        )
        if len(matches) > 1:
            raise RuntimeError("repository access error policies are ambiguous")
        payload = matches[0].timeout_response if matches else {"ok": False, "error": "unavailable"}
        return JSONResponse(
            status_code=503, content=dict(payload), headers={"Cache-Control": "no-store"}
        )

    return respond


class UnexpectedErrorMiddleware:
    """Redact failures and close route-owned transient cookie authority."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        diagnostics: RuntimeDiagnosticObserver | None = None,
        cookie_cleanups: tuple[ResponseCookieCleanupPolicy, ...] = (),
    ) -> None:
        if type(cookie_cleanups) is not tuple:
            raise TypeError("cookie-cleanup policies must be an exact tuple")
        self._app = app
        self._diagnostics = diagnostics
        self._cookie_cleanups = _cookie_cleanup_headers(cookie_cleanups)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        response_started = False
        cleanup_header = self._cookie_cleanups.get(
            (str(scope.get("path", "")), str(scope.get("method", "")).upper())
        )

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                if cleanup_header is not None:
                    headers = list(message.get("headers", ()))
                    if (b"set-cookie", cleanup_header) not in headers:
                        message = {
                            **message,
                            "headers": [*headers, (b"set-cookie", cleanup_header)],
                        }
            await send(message)

        try:
            await self._app(scope, receive, tracked_send)
        except Exception as error:
            if response_started:
                raise
            correlation_id = scope_correlation_id(scope)
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure(
                    "http_request",
                    error,
                    correlation_id=correlation_id,
                )
            headers = {"Cache-Control": "no-store"}
            if correlation_id is not None:
                headers["X-Correlation-ID"] = correlation_id
            response = JSONResponse(
                status_code=500,
                content={"code": "internal_error"},
                headers=headers,
            )
            await response(scope, receive, tracked_send)


def _cookie_cleanup_headers(
    policies: tuple[ResponseCookieCleanupPolicy, ...],
) -> dict[tuple[str, str], bytes]:
    headers: dict[tuple[str, str], bytes] = {}
    for policy in policies:
        response = Response()
        response.delete_cookie(
            policy.name,
            path=policy.cookie_path,
            secure=policy.secure,
            httponly=policy.httponly,
            samesite=policy.samesite,
        )
        values = tuple(
            value for header_name, value in response.raw_headers if header_name == b"set-cookie"
        )
        if len(values) != 1:
            raise RuntimeError("cookie cleanup did not produce one response header")
        for method in policy.methods:
            key = (policy.path, method)
            if key in headers:
                raise ValueError("cookie-cleanup route and method policies must be unique")
            headers[key] = values[0]
    return headers
