"""Bounded shared HTTP and Authlib lifecycle for the Keycloak integration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, Final, cast, override

import httpx2 as httpx
from authlib.integrations.httpx_client import (  # type: ignore[import-untyped]
    AsyncOAuth2Client,
    OAuthError,
)

from ci_coordinator.control_plane_identity import (
    KeycloakEvidenceRejected,
    KeycloakUnavailable,
)
from ci_coordinator.integrations.keycloak._text import bounded_text as _bounded_text
from ci_coordinator.integrations.keycloak.tokens import MAXIMUM_TOKEN_BYTES
from ci_coordinator.kernel import (
    JsonResourceLimits,
    NoQueueAdmission,
    StrictJsonError,
    load_strict_json,
)

KEYCLOAK_REQUEST_TIMEOUT_SECONDS = 10
_MAXIMUM_CONCURRENT_REQUESTS = 16
_MAXIMUM_RESPONSE_HEADERS = 64
_MAXIMUM_HEADER_NAME_BYTES = 128
_MAXIMUM_HEADER_VALUE_BYTES = 8_192
_MAXIMUM_RESPONSE_HEADER_BYTES = 32_768
_MAXIMUM_TOKEN_RESPONSE_BYTES = 65_536
_TOKEN_RESPONSE_LIMITS = JsonResourceLimits(max_depth=4, max_nodes=128)
_CLIENT_AUTH_METHOD: Final = "client_secret_basic"
_REJECTED_CODE_ERROR: Final = "invalid_grant"


class _HttpAdmissionFailure(RuntimeError):
    pass


class _LifecycleState(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class _SharedLifecycle:
    def __init__(self) -> None:
        self._state = _LifecycleState.OPEN
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

    @property
    def is_open(self) -> bool:
        return self._state is _LifecycleState.OPEN

    async def enter(self) -> bool:
        async with self._lock:
            if self._state is not _LifecycleState.OPEN:
                return False
            self._active += 1
            self._idle.clear()
            return True

    async def leave(self) -> None:
        async with self._lock:
            self._active -= 1
            if self._active < 0:
                raise RuntimeError("Keycloak HTTP lifecycle count is invalid")
            if self._active == 0:
                self._idle.set()

    async def close(self, close_resource: Callable[[], Awaitable[None]]) -> None:
        async with self._lock:
            if self._close_task is None:
                self._state = _LifecycleState.CLOSING
                self._close_task = asyncio.create_task(self._drain_and_close(close_resource))
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _drain_and_close(
        self,
        close_resource: Callable[[], Awaitable[None]],
    ) -> None:
        try:
            await self._idle.wait()
            await close_resource()
        except BaseException:
            async with self._lock:
                if self._close_task is asyncio.current_task():
                    self._close_task = None
                    self._state = _LifecycleState.OPEN
            raise
        async with self._lock:
            self._state = _LifecycleState.CLOSED


class _BoundedOAuth2Client(AsyncOAuth2Client):  # type: ignore[misc]
    @override
    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: Any = httpx.USE_CLIENT_DEFAULT,
        follow_redirects: Any = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        response = cast(
            httpx.Response,
            await super().send(
                request,
                stream=True,
                auth=auth,
                follow_redirects=follow_redirects,
            ),
        )
        if stream:
            return response
        try:
            _admit_response_headers(response)
            body = await _bounded_response_body(response, _MAXIMUM_TOKEN_RESPONSE_BYTES)
            if body is None:
                raise _HttpAdmissionFailure("Keycloak response is outside its bound")
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=body,
                request=response.request,
                extensions=response.extensions,
                history=response.history,
            )
        finally:
            await response.aclose()


class _KeycloakHttpClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        transport: httpx.AsyncBaseTransport | None,
        outbound_proxy_url: str | None,
    ) -> None:
        if transport is not None and outbound_proxy_url is not None:
            raise ValueError("Keycloak transport and outbound proxy are mutually exclusive")
        self._client = _BoundedOAuth2Client(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=_CLIENT_AUTH_METHOD,
            redirect_uri=redirect_uri,
            scope="openid",
            response_type="code",
            code_challenge_method="S256",
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
            timeout=KEYCLOAK_REQUEST_TIMEOUT_SECONDS,
            transport=transport,
            proxy=outbound_proxy_url,
            trust_env=False,
            verify=True,
        )
        self._client.register_compliance_hook(
            "access_token_response",
            _admit_token_response,
        )
        self._lifecycle = _SharedLifecycle()
        self._request_admission = NoQueueAdmission(_MAXIMUM_CONCURRENT_REQUESTS)
        self._oauth_admission = NoQueueAdmission(1)

    @property
    def oauth(self) -> AsyncOAuth2Client:
        return self._client

    @property
    def is_open(self) -> bool:
        return self._lifecycle.is_open

    async def get_document(self, url: str, *, maximum_bytes: int) -> bytes:
        async with self._operation():
            lease = self._request_admission.try_acquire()
            if lease is None:
                raise _unavailable()
            try:
                async with (
                    asyncio.timeout(KEYCLOAK_REQUEST_TIMEOUT_SECONDS),
                    self._client.stream(
                        "GET",
                        url,
                        headers={"Accept": "application/json"},
                        withhold_token=True,
                        follow_redirects=False,
                        timeout=KEYCLOAK_REQUEST_TIMEOUT_SECONDS,
                    ) as response,
                ):
                    _admit_json_response(response)
                    body = await _bounded_response_body(response, maximum_bytes)
                    if body is None:
                        raise _HttpAdmissionFailure("Keycloak response is outside its bound")
                    return body
            except asyncio.CancelledError:
                raise
            except (TimeoutError, httpx.TimeoutException, httpx.HTTPError):
                raise _unavailable() from None
            except _HttpAdmissionFailure:
                raise _unavailable() from None
            finally:
                lease.release()

    async def exchange_code(
        self,
        *,
        token_endpoint: str,
        code: str,
        code_verifier: str,
    ) -> str:
        token: object = None
        async with self._operation():
            request_lease = self._request_admission.try_acquire()
            oauth_lease = self._oauth_admission.try_acquire()
            if request_lease is None or oauth_lease is None:
                if request_lease is not None:
                    request_lease.release()
                if oauth_lease is not None:
                    oauth_lease.release()
                raise _unavailable()
            try:
                async with asyncio.timeout(KEYCLOAK_REQUEST_TIMEOUT_SECONDS):
                    token = await self._client.fetch_token(
                        token_endpoint,
                        grant_type="authorization_code",
                        code=code,
                        code_verifier=code_verifier,
                    )
                if not isinstance(token, dict):
                    raise _HttpAdmissionFailure("Keycloak token response is invalid")
                id_token = token.get("id_token")
                if not _bounded_token(id_token):
                    raise _HttpAdmissionFailure("Keycloak token response is invalid")
                return cast(str, id_token)
            except asyncio.CancelledError:
                raise
            except OAuthError as error:
                if error.error == _REJECTED_CODE_ERROR:
                    raise KeycloakEvidenceRejected(
                        "Keycloak authorization code is invalid"
                    ) from None
                raise _unavailable() from None
            except (TimeoutError, httpx.TimeoutException, httpx.HTTPError):
                raise _unavailable() from None
            except (_HttpAdmissionFailure, StrictJsonError, TypeError, ValueError):
                raise _unavailable() from None
            finally:
                if isinstance(token, dict):
                    token.clear()
                current = self._client.token
                if isinstance(current, dict):
                    current.clear()
                self._client.token = None
                oauth_lease.release()
                request_lease.release()

    async def aclose(self) -> None:
        await self._lifecycle.close(self._client.aclose)

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        if not await self._lifecycle.enter():
            raise _unavailable()
        try:
            yield
        finally:
            await self._lifecycle.leave()


def _admit_token_response(response: httpx.Response) -> httpx.Response:
    _admit_json_response(response, require_success=False)
    if response.status_code not in {200, 400, 401}:
        raise _HttpAdmissionFailure("Keycloak token endpoint status is invalid")
    if len(response.content) > _MAXIMUM_TOKEN_RESPONSE_BYTES:
        raise _HttpAdmissionFailure("Keycloak token response is outside its bound")
    value = load_strict_json(
        response.content,
        max_bytes=_MAXIMUM_TOKEN_RESPONSE_BYTES,
        resource_limits=_TOKEN_RESPONSE_LIMITS,
    )
    if type(value) is not dict or not 1 <= len(value) <= 32:
        raise _HttpAdmissionFailure("Keycloak token response is invalid")
    document = cast(dict[str, object], value)
    for key, item in document.items():
        if not _bounded_text(key, 64):
            raise _HttpAdmissionFailure("Keycloak token response is invalid")
        if type(item) is str:
            maximum = MAXIMUM_TOKEN_BYTES if key.endswith("token") else 4_096
            if not _bounded_text(item, maximum):
                raise _HttpAdmissionFailure("Keycloak token response is invalid")
        elif type(item) is not int or not 0 <= item <= 2**53 - 1:
            raise _HttpAdmissionFailure("Keycloak token response is invalid")
    error = document.get("error")
    if response.status_code == 200:
        if error is not None or not _bounded_token(document.get("id_token")):
            raise _HttpAdmissionFailure("Keycloak token response has no ID token")
    elif type(error) is not str or not _bounded_text(error, 64):
        raise _HttpAdmissionFailure("Keycloak token error response is invalid")
    return response


def _admit_json_response(
    response: httpx.Response,
    *,
    require_success: bool = True,
) -> None:
    _admit_response_headers(response)
    if require_success and response.status_code != 200:
        raise _HttpAdmissionFailure("Keycloak endpoint returned a non-success status")
    if 300 <= response.status_code < 400:
        raise _HttpAdmissionFailure("Keycloak endpoint returned a redirect")
    content_types = tuple(
        value for name, value in response.headers.multi_items() if name.lower() == "content-type"
    )
    if len(content_types) != 1 or not _json_content_type(content_types[0]):
        raise _HttpAdmissionFailure("Keycloak response content type is invalid")


def _admit_response_headers(response: httpx.Response) -> None:
    items = tuple(response.headers.multi_items())
    if len(items) > _MAXIMUM_RESPONSE_HEADERS:
        raise _HttpAdmissionFailure("Keycloak response headers are outside their bound")
    total = 0
    for name, value in items:
        try:
            name_bytes = name.encode("ascii")
            value_bytes = value.encode("latin-1")
        except UnicodeError:
            raise _HttpAdmissionFailure("Keycloak response headers are invalid") from None
        if (
            not name_bytes
            or len(name_bytes) > _MAXIMUM_HEADER_NAME_BYTES
            or len(value_bytes) > _MAXIMUM_HEADER_VALUE_BYTES
        ):
            raise _HttpAdmissionFailure("Keycloak response headers are outside their bound")
        total += len(name_bytes) + len(value_bytes)
    if total > _MAXIMUM_RESPONSE_HEADER_BYTES or any(
        name.lower() == "content-encoding" for name, _ in items
    ):
        raise _HttpAdmissionFailure("Keycloak response headers are outside their bound")


async def _bounded_response_body(
    response: httpx.Response,
    maximum_bytes: int,
) -> bytes | None:
    content_lengths = tuple(
        value for name, value in response.headers.multi_items() if name.lower() == "content-length"
    )
    if content_lengths and (
        len(content_lengths) != 1
        or not _content_length_is_admitted(content_lengths[0], maximum_bytes)
    ):
        return None
    if response.is_stream_consumed:
        try:
            content = response.content
        except httpx.ResponseNotRead:
            return None
        return content if len(content) <= maximum_bytes else None
    body = bytearray()
    async for chunk in response.aiter_raw(chunk_size=8_192):
        if len(body) + len(chunk) > maximum_bytes:
            return None
        body.extend(chunk)
    return bytes(body)


def _content_length_is_admitted(value: str, maximum_bytes: int) -> bool:
    maximum = str(maximum_bytes)
    return (
        len(value) <= len(maximum)
        and value.isascii()
        and value.isdecimal()
        and (value == "0" or not value.startswith("0"))
        and (len(value) < len(maximum) or value <= maximum)
    )


def _bounded_token(value: object) -> bool:
    return type(value) is str and _bounded_text(value, MAXIMUM_TOKEN_BYTES)


def _json_content_type(value: str) -> bool:
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type in {"application/json", "application/jwk-set+json"}


def _unavailable() -> KeycloakUnavailable:
    return KeycloakUnavailable("Keycloak dependency is unavailable")
