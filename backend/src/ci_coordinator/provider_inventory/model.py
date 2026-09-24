"""Immutable provider inventory facts and fail-closed outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type AccountType = Literal["Organization", "User"]
type InstallationState = Literal["active", "suspended", "unsupported"]
type RepositorySelection = Literal["all", "selected"]
type RepositoryVisibility = Literal["public", "private", "internal"]
type InventoryFailureReason = Literal[
    "unavailable",
    "rate_limited",
    "not_found",
    "malformed_provider_response",
    "provider_binding_mismatch",
]
type InventoryIneligibleReason = Literal["suspended", "unsupported_account_type"]
type InventoryMode = Literal["restricted", "app"]

_MAX_INSTALLATION_CATALOG_ITEMS = 64
_MAX_REPOSITORY_PAGE = 10_000


@dataclass(frozen=True, slots=True)
class InventoryGrant:
    mode: InventoryMode
    installation_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"restricted", "app"}:
            raise ValueError("inventory mode is not admitted")
        if self.mode == "app" and self.installation_ids:
            raise ValueError("App inventory cannot override an installation restriction")
        if type(self.installation_ids) is not tuple or len(self.installation_ids) > 64:
            raise ValueError("inventory grant exceeds its bound")
        for installation_id in self.installation_ids:
            _require_positive_safe_integer(installation_id, "installation id")
        if self.installation_ids != tuple(sorted(set(self.installation_ids))):
            raise ValueError("inventory grant must have canonical unique identities")

    def permits(self, installation_id: int) -> bool:
        return self.mode == "app" or installation_id in self.installation_ids


@dataclass(frozen=True, slots=True)
class InstallationSummary:
    installation_id: int
    account_id: int
    account_login: str
    account_type: AccountType
    repository_selection: RepositorySelection
    state: InstallationState

    def __post_init__(self) -> None:
        _require_positive_safe_integer(self.installation_id, "installation id")
        _require_positive_safe_integer(self.account_id, "account id")
        _require_text(self.account_login, "account login", maximum_bytes=512)
        if self.account_type not in {"Organization", "User"}:
            raise ValueError("account type is not admitted")
        if self.repository_selection not in {"all", "selected"}:
            raise ValueError("repository selection is not admitted")
        if self.state not in {"active", "suspended", "unsupported"}:
            raise ValueError("installation state is not admitted")
        if self.account_type == "User" and self.state == "active":
            raise ValueError("user installation cannot be active")
        if self.account_type == "Organization" and self.state == "unsupported":
            raise ValueError("organization installation cannot be unsupported")


@dataclass(frozen=True, slots=True)
class RepositorySummary:
    scope: RepositoryScope
    node_id: str
    owner_id: int
    owner_login: str
    name: str
    full_name: str
    visibility: RepositoryVisibility
    default_branch: str
    archived: bool
    disabled: bool
    fork: bool
    workbench_authorized: bool = False
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("repository inventory requires an exact repository scope")
        _require_text(self.node_id, "repository node id", maximum_bytes=512)
        _require_positive_safe_integer(self.owner_id, "repository owner id")
        _require_text(self.owner_login, "repository owner login", maximum_bytes=512)
        _require_text(self.name, "repository name", maximum_bytes=512)
        _require_text(self.full_name, "repository full name", maximum_bytes=1_025)
        _require_text(self.default_branch, "repository default branch", maximum_bytes=1_024)
        if self.full_name != f"{self.owner_login}/{self.name}":
            raise ValueError("repository full name must match owner and name")
        if self.visibility not in {"public", "private", "internal"}:
            raise ValueError("repository visibility is not admitted")
        for value in (self.archived, self.disabled, self.fork, self.workbench_authorized):
            if type(value) is not bool:
                raise TypeError("repository flags must be exact booleans")
        if self.created_at is not None:
            _require_aware_instant(self.created_at)


@dataclass(frozen=True, slots=True)
class ProviderRepositoryPage:
    page: int
    per_page: int
    total_count: int
    has_next_page: bool
    repositories: tuple[RepositorySummary, ...]

    def __post_init__(self) -> None:
        _require_page(self.page, self.per_page)
        if type(self.total_count) is not int or not 0 <= self.total_count <= MAX_SAFE_JSON_INTEGER:
            raise ValueError("provider repository total must be a non-negative safe integer")
        if type(self.has_next_page) is not bool:
            raise TypeError("provider repository pagination state must be boolean")
        if type(self.repositories) is not tuple or len(self.repositories) > self.per_page:
            raise ValueError("provider repository page exceeds its item bound")
        repository_ids = tuple(item.scope.repository_id for item in self.repositories)
        if len(repository_ids) != len(set(repository_ids)):
            raise ValueError("provider repository page contains duplicate identities")
        observed_start = (self.page - 1) * self.per_page
        observed_end = observed_start + len(self.repositories)
        if self.repositories and observed_end > self.total_count:
            raise ValueError("provider repository page contradicts its total")
        if self.has_next_page and (not self.repositories or observed_end >= self.total_count):
            raise ValueError("provider next-page evidence contradicts its total")
        if self.has_next_page and self.page == _MAX_REPOSITORY_PAGE:
            raise ValueError("provider next-page evidence exceeds the request range")
        if not self.has_next_page and (
            (self.repositories and observed_end < self.total_count)
            or (not self.repositories and observed_start < self.total_count)
        ):
            raise ValueError("provider terminal-page evidence contradicts its total")


@dataclass(frozen=True, slots=True)
class RepositoryPage:
    installation: InstallationSummary
    observed_at: datetime
    page: int
    per_page: int
    total_count: int
    has_next_page: bool
    repositories: tuple[RepositorySummary, ...]
    consistency: Literal["best_effort"] = "best_effort"

    def __post_init__(self) -> None:
        if type(self.installation) is not InstallationSummary:
            raise TypeError("repository page requires an exact installation")
        if self.installation.state != "active":
            raise ValueError("repository page requires an active installation")
        _require_aware_instant(self.observed_at)
        provider_page = ProviderRepositoryPage(
            self.page,
            self.per_page,
            self.total_count,
            self.has_next_page,
            self.repositories,
        )
        if any(
            repository.scope.installation_id != self.installation.installation_id
            for repository in provider_page.repositories
        ):
            raise ValueError("repository page crosses installation scope")
        if any(
            repository.owner_id != self.installation.account_id
            for repository in provider_page.repositories
        ):
            raise ValueError("repository page crosses installation account")


@dataclass(frozen=True, slots=True)
class ProviderInventoryUnavailable:
    reason: InventoryFailureReason
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.reason not in {
            "unavailable",
            "rate_limited",
            "not_found",
            "malformed_provider_response",
            "provider_binding_mismatch",
        }:
            raise ValueError("provider inventory failure reason is not admitted")
        if self.retry_after_seconds is not None and (
            type(self.retry_after_seconds) is not int or not 0 <= self.retry_after_seconds <= 3_600
        ):
            raise ValueError("provider retry delay must be a bounded integer")
        if self.reason != "rate_limited" and self.retry_after_seconds is not None:
            raise ValueError("retry delay is reserved for rate-limited outcomes")


@dataclass(frozen=True, slots=True)
class InstallationFailure:
    installation_id: int
    reason: InventoryFailureReason
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        _require_positive_safe_integer(self.installation_id, "installation id")
        ProviderInventoryUnavailable(self.reason, self.retry_after_seconds)


@dataclass(frozen=True, slots=True)
class InstallationCatalog:
    observed_at: datetime
    complete: bool
    installations: tuple[InstallationSummary, ...]
    failures: tuple[InstallationFailure, ...]
    page: int = 1
    per_page: int = 30
    has_next_page: bool = False
    consistency: Literal["best_effort"] = "best_effort"

    def __post_init__(self) -> None:
        _require_aware_instant(self.observed_at)
        _require_page(self.page, self.per_page)
        if type(self.has_next_page) is not bool or self.consistency != "best_effort":
            raise ValueError("catalog pagination is invalid")
        if self.has_next_page and (
            self.page == _MAX_REPOSITORY_PAGE or (not self.installations and not self.failures)
        ):
            raise ValueError("catalog continuation exceeds its bounds")
        if type(self.complete) is not bool or self.complete != (not self.failures):
            raise ValueError("catalog completeness must equal absence of failures")
        installation_ids = tuple(item.installation_id for item in self.installations)
        failure_ids = tuple(item.installation_id for item in self.failures)
        if len(installation_ids) + len(failure_ids) > self.per_page:
            raise ValueError("catalog exceeds its installation bound")
        if installation_ids != tuple(sorted(installation_ids)):
            raise ValueError("catalog installations must use canonical identity order")
        if failure_ids != tuple(sorted(failure_ids)):
            raise ValueError("catalog failures must use canonical identity order")
        if len({*installation_ids, *failure_ids}) != len(installation_ids) + len(failure_ids):
            raise ValueError("catalog installation identities must be unique")


@dataclass(frozen=True, slots=True)
class ProviderInstallationPage:
    page: int
    per_page: int
    has_next_page: bool
    installations: tuple[InstallationSummary, ...]

    def __post_init__(self) -> None:
        _require_page(self.page, self.per_page)
        if type(self.installations) is not tuple or len(self.installations) > self.per_page:
            raise ValueError("installation page exceeds its bound")
        if type(self.has_next_page) is not bool:
            raise ValueError("installation continuation must be boolean")
        if self.has_next_page and (not self.installations or self.page == _MAX_REPOSITORY_PAGE):
            raise ValueError("installation continuation exceeds its bounds")
        ids = tuple(item.installation_id for item in self.installations)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("installation page must have canonical unique identities")


@dataclass(frozen=True, slots=True)
class UserInstallationSet:
    installations: tuple[InstallationSummary, ...]

    def __post_init__(self) -> None:
        if type(self.installations) is not tuple or len(self.installations) > (
            _MAX_INSTALLATION_CATALOG_ITEMS
        ):
            raise ValueError("user installation set exceeds its bound")
        identities = tuple(item.installation_id for item in self.installations)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("user installations must have unique canonical identity order")


@dataclass(frozen=True, slots=True)
class ProviderInventoryForbidden:
    pass


@dataclass(frozen=True, slots=True)
class ProviderInventoryIneligible:
    reason: InventoryIneligibleReason

    def __post_init__(self) -> None:
        if self.reason not in {"suspended", "unsupported_account_type"}:
            raise ValueError("provider inventory ineligibility reason is not admitted")


type InstallationReadResult = InstallationSummary | ProviderInventoryUnavailable
type InstallationPageReadResult = ProviderInstallationPage | ProviderInventoryUnavailable
type RepositoryReadResult = ProviderRepositoryPage | ProviderInventoryUnavailable
type InstallationCatalogResult = (
    InstallationCatalog | ProviderInventoryForbidden | ProviderInventoryUnavailable
)
type UserInstallationSetResult = UserInstallationSet | ProviderInventoryUnavailable
type RepositoryPageResult = (
    RepositoryPage
    | ProviderInventoryForbidden
    | ProviderInventoryIneligible
    | ProviderInventoryUnavailable
)


def _require_positive_safe_integer(value: object, name: str) -> None:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER:
        raise ValueError(f"{name} must be a positive safe integer")


def _require_text(value: object, name: str, *, maximum_bytes: int) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be canonical non-empty text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be Unicode scalar text") from error
    if size > maximum_bytes:
        raise ValueError(f"{name} exceeds its byte bound")


def _require_page(page: object, per_page: object) -> None:
    if type(page) is not int or not 1 <= page <= _MAX_REPOSITORY_PAGE:
        raise ValueError("repository page number must be in range")
    if type(per_page) is not int or not 1 <= per_page <= 100:
        raise ValueError("repository page size must be in range")


def _require_aware_instant(value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("provider observation time must be timezone-aware")
