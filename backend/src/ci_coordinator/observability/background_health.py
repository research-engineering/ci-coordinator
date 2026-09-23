"""Bounded, redacted health projection for process-owned background work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BackgroundHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STARTING = "starting"
    STOPPED = "stopped"
    NOT_CONFIGURED = "not_configured"
    UNOBSERVABLE = "unobservable"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True, slots=True)
class BackgroundHealth:
    """Secret-free state; exception text and provider identifiers never cross this boundary."""

    state: BackgroundHealthState

    @property
    def ready(self) -> bool:
        return self.state is BackgroundHealthState.HEALTHY

    @property
    def observable(self) -> bool:
        return self.state is not BackgroundHealthState.UNOBSERVABLE

    @property
    def terminal_failure(self) -> bool:
        return self.state is BackgroundHealthState.TERMINAL_FAILURE
