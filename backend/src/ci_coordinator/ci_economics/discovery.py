from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

DISCOVERY_PAGE_SIZE: Final = 100
MAX_DISCOVERY_PAGES: Final = 10
MAX_DISCOVERY_WINDOW_SECONDS: Final = 604_800
type DiscoveryPageTermination = Literal["next_page", "exhausted", "truncated"]


@dataclass(frozen=True, slots=True)
class RunDiscoveryWindow:
    created_from: datetime
    created_through: datetime

    def __post_init__(self) -> None:
        for name, value in (
            ("created_from", self.created_from),
            ("created_through", self.created_through),
        ):
            if (
                type(value) is not datetime
                or value.tzinfo is None
                or value.utcoffset() is None
                or value.microsecond
            ):
                raise ValueError("discovery window needs aware second-precision timestamps")
            object.__setattr__(self, name, value.astimezone(UTC))
        if (
            not timedelta(0)
            <= self.created_through - self.created_from
            <= timedelta(seconds=MAX_DISCOVERY_WINDOW_SECONDS)
        ):
            raise ValueError("discovery window must span at most seven days")


@dataclass(frozen=True, slots=True)
class ProviderRunDiscoveryPage:
    scope: RepositoryScope
    window: RunDiscoveryWindow
    page_number: int
    provider_total: int
    sources: tuple[ProviderRunCollectionSource, ...]
    termination: DiscoveryPageTermination

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope or type(self.window) is not RunDiscoveryWindow:
            raise TypeError("discovery page requires exact scope and window")
        if type(self.page_number) is not int or not 1 <= self.page_number <= MAX_DISCOVERY_PAGES:
            raise ValueError("discovery page number exceeds its bound")
        if (
            type(self.provider_total) is not int
            or not 0 <= self.provider_total <= MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("discovery provider total must be a non-negative safe integer")
        if type(self.sources) is not tuple or any(
            type(source) is not ProviderRunCollectionSource for source in self.sources
        ):
            raise TypeError("discovery sources must be an exact tuple")
        if len(self.sources) > DISCOVERY_PAGE_SIZE or len(
            {source.attempt.workflow_run_id for source in self.sources}
        ) != len(self.sources):
            raise ValueError("discovery page must contain at most100 distinct runs")
        if any(
            source.attempt.scope != self.scope
            or not self.window.created_from <= source.run_created_at <= self.window.created_through
            for source in self.sources
        ):
            raise ValueError("discovery source is outside the exact scope or window")
        end_offset = (self.page_number - 1) * DISCOVERY_PAGE_SIZE + len(self.sources)
        if end_offset > self.provider_total:
            raise ValueError("discovery page exceeds the provider population")
        if self.termination == "exhausted":
            if end_offset != self.provider_total:
                raise ValueError("exhausted page must reach the declared population end")
        elif self.termination == "next_page":
            if (
                len(self.sources) != DISCOVERY_PAGE_SIZE
                or end_offset >= self.provider_total
                or self.page_number >= MAX_DISCOVERY_PAGES
            ):
                raise ValueError("next page requires a full nonterminal page within its budget")
        elif self.termination == "truncated":
            if end_offset >= self.provider_total:
                raise ValueError("truncated page requires an unobserved remainder")
        else:
            raise ValueError("discovery page termination is unsupported")


@dataclass(frozen=True, slots=True)
class ProviderObservationPage:
    page: ProviderRunDiscoveryPage
    workflow_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if type(self.page) is not ProviderRunDiscoveryPage or type(self.workflow_ids) is not tuple:
            raise TypeError("observation requires an exact discovery page and workflow tuple")
        if len(self.workflow_ids) != len(self.page.sources) or any(
            type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER
            for value in self.workflow_ids
        ):
            raise ValueError("observation workflow identities must correspond to every source")
