from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.discovery import (
    DISCOVERY_PAGE_SIZE,
    MAX_DISCOVERY_PAGES,
    MAX_DISCOVERY_WINDOW_SECONDS,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.config_control import RepositoryScope


@dataclass(frozen=True, slots=True)
class HistoryCursor:
    scope: RepositoryScope
    created_from: datetime
    created_through: datetime
    window: RunDiscoveryWindow
    page_number: int
    cycle_started_at: datetime

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope or type(self.window) is not RunDiscoveryWindow:
            raise TypeError("history cursor requires exact scope and discovery window")
        for name in ("created_from", "created_through"):
            value = utc_time(getattr(self, name))
            if value.microsecond:
                raise ValueError("history boundaries require second precision")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "cycle_started_at", utc_time(self.cycle_started_at))
        if not (
            self.created_from
            <= self.window.created_from
            <= self.window.created_through
            <= self.created_through
            <= self.cycle_started_at
        ):
            raise ValueError("history segment is outside its frozen population")
        if (
            self.window.created_from == self.window.created_through
            and self.created_from != self.created_through
        ):
            raise ValueError("a nonempty history interval requires positive-width query windows")
        if type(self.page_number) is not int or not 1 <= self.page_number <= MAX_DISCOVERY_PAGES:
            raise ValueError("history page exceeds its bound")

    @classmethod
    def start(
        cls,
        scope: RepositoryScope,
        created_from: datetime,
        created_through: datetime,
        *,
        cycle_started_at: datetime,
    ) -> HistoryCursor:
        start, end = utc_time(created_from), utc_time(created_through)
        return cls(
            scope,
            start,
            end,
            _segment(start, end),
            1,
            cycle_started_at,
        )

    def split(self) -> HistoryCursor | None:
        seconds = (self.window.created_through - self.window.created_from) // timedelta(seconds=1)
        if seconds <= 1:
            return None
        return replace(
            self,
            window=RunDiscoveryWindow(
                self.window.created_from,
                self.window.created_from + timedelta(seconds=seconds // 2),
            ),
            page_number=1,
        )

    def advance_window(self) -> HistoryCursor | None:
        if self.window.created_through == self.created_through:
            return None
        return replace(
            self,
            window=_segment(self.window.created_through, self.created_through),
            page_number=1,
        )

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "createdFrom": self.created_from.isoformat(),
            "createdThrough": self.created_through.isoformat(),
            "windowFrom": self.window.created_from.isoformat(),
            "windowThrough": self.window.created_through.isoformat(),
            "pageNumber": self.page_number,
            "cycleStartedAt": self.cycle_started_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class HistoryPageProgress:
    cursor: HistoryCursor | None
    register_sources: bool
    gap_reason: Literal["provider_truncated"] | None

    def __post_init__(self) -> None:
        if self.cursor is not None and type(self.cursor) is not HistoryCursor:
            raise TypeError("history progress requires an exact cursor or completion")
        if type(self.register_sources) is not bool:
            raise TypeError("history registration decision must be an exact boolean")
        if self.gap_reason not in {None, "provider_truncated"}:
            raise ValueError("unknown history progress gap")
        if not self.register_sources and (self.cursor is None or self.gap_reason is not None):
            raise ValueError("history subdivision must preserve a cursor without a gap")


def advance_history_cursor(
    cursor: HistoryCursor, page: ProviderRunDiscoveryPage
) -> HistoryPageProgress:
    if type(cursor) is not HistoryCursor or type(page) is not ProviderRunDiscoveryPage:
        raise TypeError("history progress requires exact cursor and provider page")
    if (page.scope, page.window, page.page_number) != (
        cursor.scope,
        cursor.window,
        cursor.page_number,
    ):
        raise ValueError("history page does not match its exact cursor")
    if page.provider_total > DISCOVERY_PAGE_SIZE * MAX_DISCOVERY_PAGES:
        split = cursor.split()
        if split is not None:
            return HistoryPageProgress(split, False, None)
    if page.termination == "next_page":
        return HistoryPageProgress(replace(cursor, page_number=cursor.page_number + 1), True, None)
    return HistoryPageProgress(
        cursor.advance_window(),
        True,
        "provider_truncated"
        if page.termination == "truncated"
        or page.provider_total > DISCOVERY_PAGE_SIZE * MAX_DISCOVERY_PAGES
        else None,
    )


def _segment(start: datetime, end: datetime) -> RunDiscoveryWindow:
    span = end - start
    if span < timedelta(0):
        raise ValueError("history lower bound follows its upper bound")
    return RunDiscoveryWindow(
        start, start + min(span, timedelta(seconds=MAX_DISCOVERY_WINDOW_SECONDS))
    )
