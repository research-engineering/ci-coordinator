from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type GitHubHttpMethod = Literal["GET", "POST", "PATCH", "PUT", "DELETE"]
type GitHubPaginationTermination = Literal[
    "not_paginated",
    "exhausted",
    "next_page",
    "page_limit",
    "unknown",
]
type GitHubTransportFailureKind = Literal["cancelled", "timeout", "unavailable"]
type GitHubFailureKind = Literal[
    "missing_api_version_provenance",
    "api_version_provenance_mismatch",
    "cancelled",
    "timeout",
    "transport_unavailable",
    "rate_limited",
    "forbidden",
    "not_found",
    "non_success",
]


@dataclass(frozen=True, slots=True)
class GitHubHeader:
    name: str
    value: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("GitHub header name must not be empty")


@dataclass(frozen=True, slots=True)
class GitHubQueryParameter:
    name: str
    value: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("GitHub query parameter name must not be empty")


@dataclass(frozen=True, slots=True)
class GitHubPaginationEvidence:
    complete: bool
    pages_observed: int
    item_count: int | None
    next_page: str | None
    termination: GitHubPaginationTermination

    def __post_init__(self) -> None:
        if self.pages_observed < 1:
            raise ValueError("GitHub pagination must record at least one observed page")
        if self.item_count is not None and self.item_count < 0:
            raise ValueError("GitHub pagination item_count must not be negative")
        if self.complete and self.next_page is not None:
            raise ValueError("complete GitHub pagination cannot retain a next page")
        if not self.complete and self.termination in {"not_paginated", "exhausted"}:
            raise ValueError("incomplete GitHub pagination requires a non-terminal reason")
        if self.complete and self.termination == "next_page":
            raise ValueError("next_page pagination termination is incomplete")

    @classmethod
    def not_paginated(cls) -> GitHubPaginationEvidence:
        return cls(
            complete=True,
            pages_observed=1,
            item_count=None,
            next_page=None,
            termination="not_paginated",
        )


@dataclass(frozen=True, slots=True)
class GitHubRateLimitEvidence:
    limit: int | None
    remaining: int | None
    reset_at: str | None
    retry_after: str | None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 0:
            raise ValueError("GitHub rate limit must not be negative")
        if self.remaining is not None and self.remaining < 0:
            raise ValueError("GitHub rate limit remaining count must not be negative")


@dataclass(frozen=True, slots=True)
class GitHubRequest:
    operation: str
    method: GitHubHttpMethod
    path: str
    api_version: str | None
    query: tuple[GitHubQueryParameter, ...] = ()
    headers: tuple[GitHubHeader, ...] = ()
    body: bytes | None = None

    def __post_init__(self) -> None:
        if not self.operation:
            raise ValueError("GitHub operation must not be empty")
        if not self.path.startswith("/"):
            raise ValueError("GitHub request path must start with '/'")


@dataclass(frozen=True, slots=True)
class GitHubResponse:
    status: int
    api_version: str | None
    headers: tuple[GitHubHeader, ...]
    body: bytes
    pagination: GitHubPaginationEvidence
    rate_limit: GitHubRateLimitEvidence | None = None

    def __post_init__(self) -> None:
        if not 100 <= self.status <= 599:
            raise ValueError("GitHub response status must be an HTTP status code")


@dataclass(frozen=True, slots=True)
class GitHubTransportFailure:
    kind: GitHubTransportFailureKind
    message: str
    rate_limit: GitHubRateLimitEvidence | None = None

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("GitHub transport failure message must not be empty")


@dataclass(frozen=True, slots=True)
class GitHubFailure:
    kind: GitHubFailureKind
    request: GitHubRequest
    message: str
    response: GitHubResponse | None = None
    rate_limit: GitHubRateLimitEvidence | None = None

    @property
    def status(self) -> int | None:
        return self.response.status if self.response is not None else None


@dataclass(frozen=True, slots=True)
class GitHubSuccess:
    request: GitHubRequest
    response: GitHubResponse
    kind: Literal["success"] = "success"


@dataclass(frozen=True, slots=True)
class GitHubIncomplete:
    request: GitHubRequest
    response: GitHubResponse
    kind: Literal["incomplete"] = "incomplete"


@dataclass(frozen=True, slots=True)
class GitHubUnavailable:
    failure: GitHubFailure
    kind: Literal["unavailable"] = "unavailable"


type GitHubTransportResult = GitHubResponse | GitHubTransportFailure
type GitHubOutcome = GitHubSuccess | GitHubIncomplete | GitHubUnavailable
