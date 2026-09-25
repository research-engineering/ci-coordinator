"""Credential-plane-bound GitHub protocol transports over one shared factory."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final, Protocol
from urllib.parse import unquote, urlsplit

import httpx2 as httpx

from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.integrations.github.app_credentials import (
    _AppToken,
    _CredentialUnavailable,
    _GitHubAppCredentialProvider,
    _InstallationToken,
    _validate_github_app_identity,
)
from ci_coordinator.integrations.github.app_http import _GitHubAppHttpClient, _HttpFailure
from ci_coordinator.integrations.github.app_lifecycle import _SharedClientLifecycle
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_ACCEPT,
    GITHUB_API_USER_AGENT,
    GITHUB_API_VERSION,
    GITHUB_MAXIMUM_INSTALLATION_ID,
    GITHUB_MAXIMUM_RATE_LIMIT_INTEGER_DIGITS,
    GITHUB_MAXIMUM_REQUEST_BODY_BYTES,
    GITHUB_REQUEST_TIMEOUT_SECONDS,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubHeader,
    GitHubPaginationEvidence,
    GitHubRateLimitEvidence,
    GitHubRequest,
    GitHubResponse,
    GitHubTransportFailure,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.installation_request_admission import (
    installation_request_is_admitted,
)
from ci_coordinator.integrations.github.request_admission import (
    bounded_ascii_path_is_admitted,
    canonical_path_segment_is_admitted,
    parse_canonical_page_query,
    parse_canonical_positive_integer,
)
from ci_coordinator.kernel import Clock


class GitHubUnavailableObserver(Protocol):
    def github_unavailable(self, operation: str, reason: str) -> None: ...


_CONTROLLED_REQUEST_HEADERS: Final = frozenset(
    {"accept", "authorization", "user-agent", "x-github-api-version"}
)
_FORBIDDEN_CALLER_HEADERS: Final = frozenset(
    {"connection", "content-length", "cookie", "host", "proxy-authorization", "transfer-encoding"}
)
_PUBLIC_RESPONSE_HEADERS: Final = frozenset(
    {
        "content-type",
        "link",
        "retry-after",
        "x-github-api-version-selected",
        "x-github-request-id",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
    }
)


@dataclass(frozen=True, slots=True)
class _ReviewerToken:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or not self.value
            or "\0" in self.value
            or len(self.value.encode("utf-8")) > 4_096
        ):
            raise ValueError("GitHub reviewer token must be bounded non-empty text")


type _Credential = _AppToken | _InstallationToken | _ReviewerToken | _CredentialUnavailable
type _CredentialLoader = Callable[[], Awaitable[_Credential]]


def _request_deadline(seconds: float) -> asyncio.Timeout:
    return asyncio.timeout(seconds)


class GitHubAppTransportFactory:
    """Own one HTTP lifecycle and one credential cache for all installations."""

    def __init__(
        self,
        *,
        app_id: str,
        private_key_pem: str,
        clock: Clock,
        transport: httpx.AsyncBaseTransport | None = None,
        outbound_proxy_url: str | None = None,
        unavailable_observer: GitHubUnavailableObserver | None = None,
    ) -> None:
        _validate_github_app_identity(app_id, private_key_pem)
        self._clock = clock
        self._http_client = _GitHubAppHttpClient(
            transport,
            outbound_proxy_url=outbound_proxy_url,
        )
        self._credentials = _GitHubAppCredentialProvider(
            app_id=app_id,
            private_key_pem=private_key_pem,
            clock=clock,
            http_client=self._http_client,
        )
        self._lifecycle = _SharedClientLifecycle()
        self._unavailable_observer = unavailable_observer

    def for_installation(self, installation_id: int) -> GitHubAppInstallationTransport:
        """Bind a GitHub protocol transport to one admitted installation identity."""
        if (
            type(installation_id) is not int
            or not 1 <= installation_id <= GITHUB_MAXIMUM_INSTALLATION_ID
        ):
            raise ValueError("GitHub installation id must be a positive safe integer")
        if not self._lifecycle.is_open:
            raise RuntimeError("GitHub App transport factory is closed")
        return GitHubAppInstallationTransport(self, installation_id)

    def for_app(self) -> GitHubAppIdentityTransport:
        """Bind a transport to authenticated-app endpoints without installation authority."""
        if not self._lifecycle.is_open:
            raise RuntimeError("GitHub App transport factory is closed")
        return GitHubAppIdentityTransport(self)

    def for_reviewer(self, access_token: str) -> GitHubReviewerTransport:
        """Bind one request-plane transport to an admitted ephemeral reviewer token."""
        credential = _ReviewerToken(access_token)
        if not self._lifecycle.is_open:
            raise RuntimeError("GitHub App transport factory is closed")
        return GitHubReviewerTransport(self, credential)

    async def aclose(self) -> None:
        """Close the sole shared client after the composition owner drains callers."""
        await self._lifecycle.close(self._http_client.aclose)

    async def _send_installation(
        self,
        installation_id: int,
        request: GitHubRequest,
    ) -> GitHubTransportResult:
        if not installation_request_is_admitted(request):
            return _unavailable("GitHub App installation request is not admitted")

        async def load_credential() -> _Credential:
            return await self._credentials.get(installation_id)

        return await self._send(request, load_credential)

    async def _send_app(self, request: GitHubRequest) -> GitHubTransportResult:
        admission_failure = _app_identity_request_admission_failure(request)
        if admission_failure is not None:
            return admission_failure

        async def load_credential() -> _Credential:
            return self._credentials.get_app()

        return await self._send(request, load_credential)

    async def _send_reviewer(
        self,
        credential: _ReviewerToken,
        request: GitHubRequest,
    ) -> GitHubTransportResult:
        admission_failure = _reviewer_request_admission_failure(request)
        if admission_failure is not None:
            return admission_failure

        async def load_credential() -> _Credential:
            return credential

        return await self._send(request, load_credential)

    async def _send(
        self,
        request: GitHubRequest,
        load_credential: _CredentialLoader,
    ) -> GitHubTransportResult:
        if not await self._lifecycle.enter_send():
            return _unavailable("GitHub App transport is closed")
        deadline = _request_deadline(GITHUB_REQUEST_TIMEOUT_SECONDS)
        try:
            async with deadline:
                return await self._send_before_deadline(request, load_credential)
        except TimeoutError:
            if not deadline.expired():
                raise
            return GitHubTransportFailure(
                kind="timeout",
                message="GitHub API request timed out",
            )
        finally:
            await self._lifecycle.leave_send()

    async def _send_before_deadline(
        self,
        request: GitHubRequest,
        load_credential: _CredentialLoader,
    ) -> GitHubTransportResult:
        admission_failure = _request_admission_failure(request)
        if admission_failure is not None:
            return admission_failure
        credential = await load_credential()
        if isinstance(credential, _CredentialUnavailable):
            return _unavailable("GitHub App credential is unavailable")
        exchange = await self._http_client.exchange(
            method=request.method,
            path=request.path,
            headers=_request_headers(request, credential.value),
            body=request.body,
            query=tuple((item.name, item.value) for item in request.query),
        )
        if isinstance(exchange, _HttpFailure):
            if exchange.kind == "timeout":
                return GitHubTransportFailure(
                    kind="timeout",
                    message="GitHub API request timed out",
                )
            return _unavailable("GitHub API request is unavailable")
        if 300 <= exchange.status < 400:
            return _unavailable("GitHub API response redirected")
        if _contains_credential(exchange.headers, exchange.body, credential.value):
            return _unavailable("GitHub API response contained credential material")
        return GitHubResponse(
            status=exchange.status,
            api_version=_single_header(exchange.headers, "x-github-api-version-selected"),
            headers=_public_response_headers(exchange.headers),
            body=exchange.body,
            pagination=_pagination_evidence(_header_values(exchange.headers, "link")),
            rate_limit=_rate_limit_evidence(exchange.headers),
            received_at=self._clock.now() if exchange.status in {403, 429} else None,
        )

    def _observe_result(self, request: GitHubRequest, result: GitHubTransportResult) -> None:
        if self._unavailable_observer is None:
            return
        if isinstance(result, GitHubTransportFailure):
            reason = "timeout" if result.kind == "timeout" else "unavailable"
        elif not 200 <= result.status < 300:
            reason = "http"
        else:
            return
        self._unavailable_observer.github_unavailable(request.operation, reason)


class GitHubAppInstallationTransport:
    """One lightweight installation binding over a shared factory lifecycle."""

    def __init__(self, factory: GitHubAppTransportFactory, installation_id: int) -> None:
        self._factory = factory
        self._installation_id = installation_id

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        result = await self._factory._send_installation(self._installation_id, request)
        self._factory._observe_result(request, result)
        return result


class GitHubAppIdentityTransport:
    """Authenticated-app binding restricted to exact installation identity reads."""

    def __init__(self, factory: GitHubAppTransportFactory) -> None:
        self._factory = factory

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        result = await self._factory._send_app(request)
        self._factory._observe_result(request, result)
        return result


class GitHubReviewerTransport:
    """One expiring reviewer-token binding over the shared bounded API lifecycle."""

    def __init__(self, factory: GitHubAppTransportFactory, credential: _ReviewerToken) -> None:
        self._factory = factory
        self._credential = credential

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        result = await self._factory._send_reviewer(self._credential, request)
        self._factory._observe_result(request, result)
        return result


def _app_identity_request_admission_failure(
    request: GitHubRequest,
) -> GitHubTransportFailure | None:
    if not bounded_ascii_path_is_admitted(request.path):
        return _unavailable("GitHub App identity request is not admitted")
    if (
        request.operation == "app_identity.list_installations"
        and request.method == "GET"
        and request.path == "/app/installations"
        and request.body is None
        and _page_query_is_admitted(request)
    ):
        return None
    if _repository_installation_request_is_admitted(request):
        return None
    prefix = "/app/installations/"
    raw_identity = request.path.removeprefix(prefix)
    if (
        request.operation != "app_identity.get_installation"
        or request.method != "GET"
        or not request.path.startswith(prefix)
        or not raw_identity.isascii()
        or not raw_identity.isdecimal()
        or raw_identity.startswith("0")
        or len(raw_identity) > 16
        or not 1 <= int(raw_identity) <= GITHUB_MAXIMUM_INSTALLATION_ID
        or request.query
        or request.body is not None
    ):
        return _unavailable("GitHub App identity request is not admitted")
    return None


def _repository_installation_request_is_admitted(request: GitHubRequest) -> bool:
    segments = request.path.split("/")
    if (
        request.operation != "app_identity.get_repository_installation"
        or request.method != "GET"
        or len(segments) != 5
        or segments[0] != ""
        or segments[1] != "repos"
        or segments[4] != "installation"
        or not canonical_path_segment_is_admitted(segments[2])
        or not canonical_path_segment_is_admitted(segments[3])
        or request.query
        or request.body is not None
    ):
        return False
    try:
        repository = GitHubRepository(unquote(segments[2]), unquote(segments[3]))
    except ValueError:
        return False
    return f"{repository.path}/installation" == request.path


def _reviewer_request_admission_failure(
    request: GitHubRequest,
) -> GitHubTransportFailure | None:
    if request.method != "GET" or request.body is not None:
        return _unavailable("GitHub reviewer request is not admitted")
    if request.operation == "reviewer_attestation.get_user":
        admitted = request.path == "/user" and not request.query
    elif request.operation == "reviewer_attestation.get_repository":
        admitted = _positive_path_request(request.path, prefix="/repositories/", suffix="")
        admitted = admitted and not request.query
    else:
        admitted = False
    return None if admitted else _unavailable("GitHub reviewer request is not admitted")


def _positive_path_request(path: str, *, prefix: str, suffix: str) -> bool:
    if not path.startswith(prefix) or (suffix and not path.endswith(suffix)):
        return False
    end = -len(suffix) if suffix else None
    value = path[len(prefix) : end]
    return (
        parse_canonical_positive_integer(
            value,
            maximum=GITHUB_MAXIMUM_INSTALLATION_ID,
        )
        is not None
    )


def _page_query_is_admitted(
    request: GitHubRequest,
    *,
    exact_page: int | None = None,
    exact_size: int | None = None,
) -> bool:
    page_query = parse_canonical_page_query(request.query)
    if page_query is None:
        return False
    page, size = page_query
    return (exact_page is None or page == exact_page) and (exact_size is None or size == exact_size)


def _request_admission_failure(request: GitHubRequest) -> GitHubTransportFailure | None:
    if request.api_version != GITHUB_API_VERSION:
        return _unavailable("GitHub API version is not admitted")
    path = urlsplit(request.path)
    if path.scheme or path.netloc or path.query or path.fragment or "\\" in request.path:
        return _unavailable("GitHub API path is not admitted")
    if request.body is not None and len(request.body) > GITHUB_MAXIMUM_REQUEST_BODY_BYTES:
        return _unavailable("GitHub API request exceeds the byte bound")
    seen_names: set[str] = set()
    for header in request.headers:
        name = header.name.casefold()
        if name in seen_names:
            return _unavailable("GitHub API request contains duplicate headers")
        seen_names.add(name)
        if name in _FORBIDDEN_CALLER_HEADERS:
            return _unavailable("GitHub API transport framing is factory-owned")
        if name not in _CONTROLLED_REQUEST_HEADERS:
            continue
        if name == "authorization":
            return _unavailable("GitHub API authorization is factory-owned")
        if name == "accept" and header.value != GITHUB_API_ACCEPT:
            return _unavailable("GitHub API accept header is not admitted")
        if name == "user-agent" and header.value != GITHUB_API_USER_AGENT:
            return _unavailable("GitHub API user agent is not admitted")
        if name == "x-github-api-version" and header.value != GITHUB_API_VERSION:
            return _unavailable("GitHub API version header is not admitted")
    return None


def _request_headers(request: GitHubRequest, credential: str) -> tuple[tuple[str, str], ...]:
    passthrough = tuple(
        (header.name, header.value)
        for header in request.headers
        if header.name.casefold() not in _CONTROLLED_REQUEST_HEADERS
    )
    return (
        *passthrough,
        ("Accept", GITHUB_API_ACCEPT),
        ("Authorization", f"Bearer {credential}"),
        ("User-Agent", GITHUB_API_USER_AGENT),
        ("X-GitHub-Api-Version", GITHUB_API_VERSION),
    )


def _public_response_headers(
    headers: tuple[tuple[str, str], ...],
) -> tuple[GitHubHeader, ...]:
    return tuple(
        GitHubHeader(name=name, value=value)
        for name, value in headers
        if name.casefold() in _PUBLIC_RESPONSE_HEADERS
    )


def _contains_credential(
    headers: tuple[tuple[str, str], ...],
    body: bytes,
    credential: str,
) -> bool:
    credential_bytes = credential.encode("utf-8")
    return credential_bytes in body or any(
        credential in name or credential in value for name, value in headers
    )


def _pagination_evidence(link_headers: tuple[str, ...]) -> GitHubPaginationEvidence:
    if not link_headers:
        return GitHubPaginationEvidence.not_paginated()
    relations = _link_relations(link_headers)
    if relations is not None:
        next_page = relations.get("next")
        if next_page is not None:
            return GitHubPaginationEvidence(
                complete=False,
                pages_observed=1,
                item_count=None,
                next_page=next_page,
                termination="next_page",
            )
        return GitHubPaginationEvidence(
            complete=True,
            pages_observed=1,
            item_count=None,
            next_page=None,
            termination="exhausted",
        )
    return GitHubPaginationEvidence(
        complete=False,
        pages_observed=1,
        item_count=None,
        next_page=None,
        termination="unknown",
    )


def _link_relations(values: tuple[str, ...]) -> dict[str, str] | None:
    """Parse the bounded GitHub Link header without following provider URLs."""

    relations: dict[str, str] = {}
    segments: list[str] = []
    for value in values:
        parsed_segments = _split_link_values(value)
        if parsed_segments is None:
            return None
        segments.extend(parsed_segments)
    for segment in segments:
        stripped = segment.strip()
        if not stripped.startswith("<"):
            return None
        target_end = stripped.find(">")
        if target_end < 2:
            return None
        url = stripped[1:target_end]
        if not url:
            return None
        parameters = stripped[target_end + 1 :]
        if not parameters.startswith(";"):
            return None
        relation_values: tuple[str, ...] | None = None
        for parameter in parameters[1:].split(";"):
            name, equals, raw_value = parameter.strip().partition("=")
            if not name or not equals or not raw_value:
                return None
            if name.casefold() != "rel":
                continue
            if len(raw_value) < 3 or not raw_value.startswith('"') or not raw_value.endswith('"'):
                return None
            if relation_values is not None:
                return None
            relation_values = tuple(raw_value[1:-1].split(" "))
        if (
            relation_values is None
            or not relation_values
            or any(not item for item in relation_values)
        ):
            return None
        for relation in relation_values:
            normalized_relation = relation.casefold()
            if normalized_relation in relations:
                return None
            relations[normalized_relation] = url
    return relations or None


def _split_link_values(value: str) -> tuple[str, ...] | None:
    if not value:
        return None
    segments: list[str] = []
    start = 0
    in_target = False
    in_quote = False
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if in_quote and character == "\\":
            escaped = True
            continue
        if not in_quote and character == "<":
            if in_target:
                return None
            in_target = True
            continue
        if not in_quote and character == ">":
            if not in_target:
                return None
            in_target = False
            continue
        if not in_target and character == '"':
            in_quote = not in_quote
            continue
        if not in_target and not in_quote and character == ",":
            segment = value[start:index].strip()
            if not segment:
                return None
            segments.append(segment)
            start = index + 1
    if in_target or in_quote or escaped:
        return None
    final_segment = value[start:].strip()
    if not final_segment:
        return None
    segments.append(final_segment)
    return tuple(segments)


def _rate_limit_evidence(headers: tuple[tuple[str, str], ...]) -> GitHubRateLimitEvidence | None:
    limit = _optional_non_negative_int(_header(headers, "x-ratelimit-limit"))
    remaining = _optional_non_negative_int(_header(headers, "x-ratelimit-remaining"))
    reset_at = _header(headers, "x-ratelimit-reset")
    retry_after = _header(headers, "retry-after")
    if all(value is None for value in (limit, remaining, reset_at, retry_after)):
        return None
    return GitHubRateLimitEvidence(limit, remaining, reset_at, retry_after)


def _header(headers: tuple[tuple[str, str], ...], name: str) -> str | None:
    lowered_name = name.casefold()
    for header_name, value in headers:
        if header_name.casefold() == lowered_name:
            return value
    return None


def _header_values(headers: tuple[tuple[str, str], ...], name: str) -> tuple[str, ...]:
    lowered_name = name.casefold()
    return tuple(value for header_name, value in headers if header_name.casefold() == lowered_name)


def _single_header(headers: tuple[tuple[str, str], ...], name: str) -> str | None:
    values = tuple(
        value for header_name, value in headers if header_name.casefold() == name.casefold()
    )
    return values[0] if len(values) == 1 else None


def _optional_non_negative_int(value: str | None) -> int | None:
    if (
        value is None
        or len(value) > GITHUB_MAXIMUM_RATE_LIMIT_INTEGER_DIGITS
        or not value.isascii()
        or not value.isdecimal()
    ):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _unavailable(message: str) -> GitHubTransportFailure:
    return GitHubTransportFailure(kind="unavailable", message=message)
