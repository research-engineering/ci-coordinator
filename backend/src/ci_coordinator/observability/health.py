"""Liveness projection with no dependency or policy decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class HealthStatus:
    state: Literal["alive"] = "alive"


def health() -> HealthStatus:
    return HealthStatus()
