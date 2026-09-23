from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Literal

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.discovery import (
    DISCOVERY_PAGE_SIZE,
    MAX_DISCOVERY_PAGES,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)

MAX_OBSERVATION_SUBWINDOW: Final = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class ObservationCursor:
    interval: RunDiscoveryWindow
    window: RunDiscoveryWindow
    page_number: int
    cycle_started_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.interval) is not RunDiscoveryWindow
            or type(self.window) is not RunDiscoveryWindow
        ):
            raise TypeError("observation cursor requires exact discovery windows")
        if not (
            self.interval.created_from
            <= self.window.created_from
            <= self.window.created_through
            <= self.interval.created_through
        ):
            raise ValueError("active observation window is outside the frozen interval")
        if self.window.created_through - self.window.created_from > MAX_OBSERVATION_SUBWINDOW:
            raise ValueError("active observation window exceeds six hours")
        if type(self.page_number) is not int or not 1 <= self.page_number <= MAX_DISCOVERY_PAGES:
            raise ValueError("observation page exceeds its bound")
        object.__setattr__(self, "cycle_started_at", utc_time(self.cycle_started_at))
        if self.cycle_started_at < self.interval.created_through:
            raise ValueError("observation interval cannot extend past its cycle start")

    @classmethod
    def start(cls, interval: RunDiscoveryWindow, cycle_started_at: datetime) -> ObservationCursor:
        if type(interval) is not RunDiscoveryWindow:
            raise TypeError("observation cycle requires an exact interval")
        return cls(
            interval,
            _subwindow(interval.created_from, interval.created_through),
            1,
            cycle_started_at,
        )

    def split(self) -> ObservationCursor | None:
        span = self.window.created_through - self.window.created_from
        seconds = span // timedelta(seconds=1)
        if seconds <= 1:
            return None
        middle = self.window.created_from + timedelta(seconds=seconds // 2)
        return replace(
            self,
            window=RunDiscoveryWindow(self.window.created_from, middle),
            page_number=1,
        )

    def advance_window(self) -> ObservationCursor | None:
        if self.window.created_through == self.interval.created_through:
            return None
        return replace(
            self,
            window=_subwindow(self.window.created_through, self.interval.created_through),
            page_number=1,
        )

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "intervalFrom": self.interval.created_from.isoformat(),
            "intervalThrough": self.interval.created_through.isoformat(),
            "windowFrom": self.window.created_from.isoformat(),
            "windowThrough": self.window.created_through.isoformat(),
            "pageNumber": self.page_number,
            "cycleStartedAt": self.cycle_started_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ObservationPageProgress:
    cursor: ObservationCursor | None
    register_sources: bool
    gap_reason: Literal["provider_truncated"] | None

    def __post_init__(self) -> None:
        if self.cursor is not None and type(self.cursor) is not ObservationCursor:
            raise TypeError("page progress requires an exact cursor or completion")
        if type(self.register_sources) is not bool:
            raise TypeError("page registration decision must be an exact boolean")
        if self.gap_reason not in {None, "provider_truncated"}:
            raise ValueError("unknown page progress gap")
        if not self.register_sources and (self.cursor is None or self.gap_reason is not None):
            raise ValueError("a split must retain a cursor without declaring a gap")


def advance_discovery_cursor(
    cursor: ObservationCursor, page: ProviderRunDiscoveryPage
) -> ObservationPageProgress:
    if type(cursor) is not ObservationCursor or type(page) is not ProviderRunDiscoveryPage:
        raise TypeError("observation progress requires exact cursor and admitted page")
    if page.window != cursor.window or page.page_number != cursor.page_number:
        raise ValueError("discovery page does not match its exact cursor")
    if page.provider_total > DISCOVERY_PAGE_SIZE * MAX_DISCOVERY_PAGES:
        split = cursor.split()
        if split is not None:
            return ObservationPageProgress(split, False, None)
        return ObservationPageProgress(cursor.advance_window(), True, "provider_truncated")
    if page.termination == "next_page":
        return ObservationPageProgress(
            replace(cursor, page_number=cursor.page_number + 1), True, None
        )
    gap: Literal["provider_truncated"] | None = (
        "provider_truncated" if page.termination == "truncated" else None
    )
    return ObservationPageProgress(cursor.advance_window(), True, gap)


def _subwindow(start: datetime, end: datetime) -> RunDiscoveryWindow:
    return RunDiscoveryWindow(start, start + min(end - start, MAX_OBSERVATION_SUBWINDOW))
