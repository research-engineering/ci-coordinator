from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        pass


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class MonotonicClock(Protocol):
    def now(self) -> float:
        pass


class SystemMonotonicClock:
    def now(self) -> float:
        return monotonic()


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant
