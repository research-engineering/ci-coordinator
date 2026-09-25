from __future__ import annotations

from ci_coordinator.integrations.github._rate_limits import has_secondary_rate_limit_message
from ci_coordinator.integrations.github.contracts import (
    GitHubFailure,
    GitHubFailureKind,
    GitHubHeader,
    GitHubIncomplete,
    GitHubOutcome,
    GitHubQueryParameter,
    GitHubRateLimitEvidence,
    GitHubRequest,
    GitHubResponse,
    GitHubSuccess,
    GitHubTransportFailure,
    GitHubUnavailable,
)
from ci_coordinator.integrations.github.transport import GitHubTransport


class GitHubProtocolClient:
    """Common classifier for GitHub protocol facts, without provider I/O."""

    def __init__(self, transport: GitHubTransport, *, api_version: str | None) -> None:
        self._transport = transport
        self._api_version = api_version

    async def _get(
        self,
        *,
        operation: str,
        path: str,
        query: tuple[GitHubQueryParameter, ...] = (),
    ) -> GitHubOutcome:
        headers = (
            ()
            if self._api_version is None
            else (GitHubHeader(name="X-GitHub-Api-Version", value=self._api_version),)
        )
        return await self._send(
            GitHubRequest(
                operation=operation,
                method="GET",
                path=path,
                api_version=self._api_version,
                query=query,
                headers=headers,
            )
        )

    async def _send(self, request: GitHubRequest) -> GitHubOutcome:
        if not request.api_version:
            return self._unavailable(
                kind="missing_api_version_provenance",
                request=request,
                message="GitHub API version provenance is required before transport execution",
            )

        try:
            transport_result = await self._transport.send(request)
        except TimeoutError:
            return self._unavailable(
                kind="timeout",
                request=request,
                message="GitHub transport timed out",
            )

        if isinstance(transport_result, GitHubTransportFailure):
            return self._transport_unavailable(request, transport_result)

        return self._classify_response(request, transport_result)

    def _transport_unavailable(
        self,
        request: GitHubRequest,
        transport_failure: GitHubTransportFailure,
    ) -> GitHubUnavailable:
        kind: GitHubFailureKind
        match transport_failure.kind:
            case "cancelled":
                kind = "cancelled"
            case "timeout":
                kind = "timeout"
            case "unavailable":
                kind = "transport_unavailable"
        return self._unavailable(
            kind=kind,
            request=request,
            message=transport_failure.message,
            rate_limit=transport_failure.rate_limit,
        )

    def _classify_response(
        self,
        request: GitHubRequest,
        response: GitHubResponse,
    ) -> GitHubOutcome:
        if response.api_version is None:
            if response.status in {500, 502, 503, 504} and not any(
                header.name.casefold() == "x-github-api-version-selected"
                for header in response.headers
            ):
                return self._status_unavailable(request, response)
            return self._unavailable(
                kind="missing_api_version_provenance",
                request=request,
                message="GitHub response omitted API version provenance",
                response=response,
            )
        if response.api_version != request.api_version:
            return self._unavailable(
                kind="api_version_provenance_mismatch",
                request=request,
                message="GitHub response API version provenance does not match the request",
                response=response,
            )
        if not 200 <= response.status < 300:
            return self._status_unavailable(request, response)
        if not response.pagination.complete:
            return GitHubIncomplete(request=request, response=response)
        return GitHubSuccess(request=request, response=response)

    def _status_unavailable(
        self,
        request: GitHubRequest,
        response: GitHubResponse,
    ) -> GitHubUnavailable:
        kind: GitHubFailureKind
        if _is_rate_limited(response):
            kind = "rate_limited"
        else:
            match response.status:
                case 403:
                    kind = "forbidden"
                case 404:
                    kind = "not_found"
                case 408 | 504:
                    kind = "timeout"
                case _:
                    kind = "non_success"
        return self._unavailable(
            kind=kind,
            request=request,
            message=f"GitHub operation returned HTTP {response.status}",
            response=response,
            rate_limit=response.rate_limit,
        )

    @staticmethod
    def _unavailable(
        *,
        kind: GitHubFailureKind,
        request: GitHubRequest,
        message: str,
        response: GitHubResponse | None = None,
        rate_limit: GitHubRateLimitEvidence | None = None,
    ) -> GitHubUnavailable:
        return GitHubUnavailable(
            failure=GitHubFailure(
                kind=kind,
                request=request,
                message=message,
                response=response,
                rate_limit=rate_limit,
            )
        )


def _is_rate_limited(response: GitHubResponse) -> bool:
    """Classify only GitHub's documented 403/429 rate-limit evidence."""

    if response.status == 429:
        return True
    if response.status != 403:
        return False
    rate_limit = response.rate_limit
    if rate_limit is not None and (rate_limit.remaining == 0 or rate_limit.retry_after is not None):
        return True
    return has_secondary_rate_limit_message(response.body)
