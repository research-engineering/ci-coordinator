"""Bounded HTTPX exchange owned by the GitHub App transport factory."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx2 as httpx

from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_ACCEPT_ENCODING,
    GITHUB_API_BASE_URL,
    GITHUB_MAXIMUM_CONCURRENT_EXCHANGES,
    GITHUB_MAXIMUM_RESPONSE_BODY_BYTES,
    GITHUB_REQUEST_TIMEOUT_SECONDS,
)


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass(frozen=True, slots=True)
class _HttpFailure:
    kind: str


class _GitHubAppHttpClient:
    """One non-redirecting GitHub.com client with a bounded body reader."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None,
        *,
        outbound_proxy_url: str | None = None,
    ) -> None:
        if transport is not None and outbound_proxy_url is not None:
            raise ValueError("GitHub App transport and outbound proxy are mutually exclusive")
        self._admission = asyncio.Semaphore(GITHUB_MAXIMUM_CONCURRENT_EXCHANGES)
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API_BASE_URL,
            follow_redirects=False,
            headers={"Accept-Encoding": GITHUB_API_ACCEPT_ENCODING},
            timeout=GITHUB_REQUEST_TIMEOUT_SECONDS,
            transport=transport,
            proxy=outbound_proxy_url,
            trust_env=False,
            verify=True,
        )

    async def exchange(
        self,
        *,
        method: str,
        path: str,
        headers: tuple[tuple[str, str], ...],
        body: bytes | None,
        query: tuple[tuple[str, str], ...] = (),
    ) -> _HttpResponse | _HttpFailure:
        try:
            async with asyncio.timeout(GITHUB_REQUEST_TIMEOUT_SECONDS):
                async with (
                    self._admission,
                    self._client.stream(
                        method,
                        path,
                        params=query,
                        headers=headers,
                        content=body,
                        follow_redirects=False,
                        timeout=GITHUB_REQUEST_TIMEOUT_SECONDS,
                    ) as response,
                ):
                    response_failure = response_admission_failure(
                        response,
                        maximum_bytes=GITHUB_MAXIMUM_RESPONSE_BODY_BYTES,
                    )
                    if response_failure is not None:
                        return response_failure
                    response_body = await bounded_raw_response_body(
                        response,
                        maximum_bytes=GITHUB_MAXIMUM_RESPONSE_BODY_BYTES,
                    )
                    if response_body is None:
                        return _HttpFailure("response_oversize")
                    return _HttpResponse(
                        status=response.status_code,
                        headers=tuple(response.headers.multi_items()),
                        body=response_body,
                    )
        except TimeoutError:
            return _HttpFailure("timeout")
        except httpx.TimeoutException:
            return _HttpFailure("timeout")
        except httpx.HTTPError:
            return _HttpFailure("unavailable")

    async def aclose(self) -> None:
        await self._client.aclose()


async def bounded_raw_response_body(
    response: httpx.Response,
    *,
    maximum_bytes: int,
) -> bytes | None:
    if response.is_stream_consumed:
        return response.content if len(response.content) <= maximum_bytes else None
    body = bytearray()
    async for chunk in response.aiter_raw(chunk_size=8192):
        if len(body) + len(chunk) > maximum_bytes:
            return None
        body.extend(chunk)
    return bytes(body)


def response_admission_failure(
    response: httpx.Response,
    *,
    maximum_bytes: int,
) -> _HttpFailure | None:
    if any(name.lower() == "content-encoding" for name, _ in response.headers.multi_items()):
        return _HttpFailure("response_encoding")
    content_lengths = tuple(
        value for name, value in response.headers.multi_items() if name.lower() == "content-length"
    )
    if not content_lengths:
        return None
    if len(content_lengths) != 1 or not _content_length_is_admitted(
        content_lengths[0],
        maximum_bytes=maximum_bytes,
    ):
        return _HttpFailure("response_oversize")
    return None


def _content_length_is_admitted(value: str, *, maximum_bytes: int) -> bool:
    maximum = str(maximum_bytes)
    return (
        len(value) <= len(maximum)
        and value.isascii()
        and value.isdecimal()
        and (value == "0" or not value.startswith("0"))
        and (len(value) < len(maximum) or (len(value) == len(maximum) and value <= maximum))
    )
