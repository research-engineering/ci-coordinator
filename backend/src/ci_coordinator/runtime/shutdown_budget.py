"""One compositional deadline budget for process shutdown phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MINIMUM_ORCHESTRATION_RESERVE_SECONDS: Final = 0.25


@dataclass(frozen=True, slots=True)
class ShutdownBudget:
    total_seconds: int
    uvicorn_grace_seconds: int
    resource_cleanup_seconds: float
    background_drain_seconds: float
    orchestration_reserve_seconds: float

    def __post_init__(self) -> None:
        if type(self.total_seconds) is not int or self.total_seconds < 1:
            raise ValueError("shutdown budget total must be a positive integer")
        if type(self.uvicorn_grace_seconds) is not int or self.uvicorn_grace_seconds < 0:
            raise ValueError("Uvicorn shutdown budget must be a non-negative integer")
        if type(self.resource_cleanup_seconds) is not float or self.resource_cleanup_seconds <= 0:
            raise ValueError("resource cleanup budget must be a positive float")
        if (
            type(self.orchestration_reserve_seconds) is not float
            or self.orchestration_reserve_seconds < MINIMUM_ORCHESTRATION_RESERVE_SECONDS
        ):
            raise ValueError("shutdown orchestration reserve is too small")
        if (
            type(self.background_drain_seconds) is not float
            or self.background_drain_seconds <= 0
            or self.background_drain_seconds > self.resource_cleanup_seconds
        ):
            raise ValueError("background drain budget must fit within resource cleanup")
        if (
            self.uvicorn_grace_seconds
            + self.resource_cleanup_seconds
            + self.orchestration_reserve_seconds
            != self.total_seconds
        ):
            raise ValueError("shutdown phase budgets must equal the configured total")


def partition_shutdown_budget(total_seconds: int) -> ShutdownBudget:
    """Partition one process bound across orchestration, drain, and cleanup."""
    if type(total_seconds) is not int or total_seconds < 1:
        raise ValueError("shutdown budget total must be a positive integer")
    orchestration_reserve_seconds = min(1.0, total_seconds / 4.0)
    phase_seconds = total_seconds - orchestration_reserve_seconds
    uvicorn_grace_seconds = (int(phase_seconds) + 1) // 2
    resource_cleanup_seconds = float(phase_seconds - uvicorn_grace_seconds)
    return ShutdownBudget(
        total_seconds=total_seconds,
        uvicorn_grace_seconds=uvicorn_grace_seconds,
        resource_cleanup_seconds=resource_cleanup_seconds,
        background_drain_seconds=resource_cleanup_seconds / 2,
        orchestration_reserve_seconds=orchestration_reserve_seconds,
    )
