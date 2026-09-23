"""Readiness is a projection of supplied dependency facts, never a substitute for them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DependencyReadiness:
    name: str
    available: bool
    required_for_selected_issuance: bool

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise ValueError("dependency readiness name is required")
        if type(self.available) is not bool:
            raise ValueError("dependency availability must be bool")
        if type(self.required_for_selected_issuance) is not bool:
            raise ValueError("dependency requirement must be bool")


@dataclass(frozen=True, slots=True)
class ReadinessStatus:
    ready: bool
    unavailable_dependencies: tuple[str, ...]


class RuntimeReadinessUseCase(Protocol):
    async def __call__(self) -> ReadinessStatus: ...


def assess_readiness(dependencies: tuple[DependencyReadiness, ...]) -> ReadinessStatus:
    unavailable = tuple(
        sorted(
            {
                dependency.name
                for dependency in dependencies
                if dependency.required_for_selected_issuance and not dependency.available
            }
        )
    )
    return ReadinessStatus(ready=not unavailable, unavailable_dependencies=unavailable)
