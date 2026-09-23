from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from ci_coordinator.kernel import MonotonicClock
from ci_coordinator.repo_context.diff_model import RepositoryEpoch
from ci_coordinator.repo_context.planning_input import PlanningInput, PolicySnapshot

_MAX_ENTRIES = 4
_MAX_UNITS = 100_000
_LIFETIME_SECONDS = 120.0
type ContextKey = tuple[RepositoryEpoch, PolicySnapshot]


@dataclass(frozen=True, slots=True)
class PreparedContext:
    planning_input: PlanningInput
    acquired_at: float
    expires_at: float
    units: int


class PreparedContextCache:
    def __init__(self, clock: MonotonicClock, observe: Callable[[str], None] | None = None) -> None:
        self._clock = clock
        self._observe = observe
        self._entries: OrderedDict[ContextKey, PreparedContext] = OrderedDict()
        self._last_time = 0.0
        self._closed = False

    def acquisition_time(self) -> float | None:
        if self._closed:
            return None
        now = self._clock.now()
        if not math.isfinite(now) or now < self._last_time:
            self._entries.clear()
            if self._observe is not None:
                self._observe("cache_invalid")
            return None
        self._last_time = now
        for key, entry in tuple(self._entries.items()):
            if now >= entry.expires_at:
                del self._entries[key]
                if self._observe is not None:
                    self._observe("cache_expired")
        return now

    def find(self, epoch: RepositoryEpoch, policy: PolicySnapshot) -> PreparedContext | None:
        if self.acquisition_time() is None:
            return None
        key = epoch, policy
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def current(self, entry: PreparedContext) -> bool:
        now = self.acquisition_time()
        key = entry.planning_input.repo_epoch, entry.planning_input.policy
        return (
            now is not None
            and entry.acquired_at <= now < entry.expires_at
            and self._entries.get(key) is entry
        )

    def put(self, context: PlanningInput, acquired_at: float) -> bool:
        now = self.acquisition_time()
        expires_at = acquired_at + _LIFETIME_SECONDS
        if (
            now is None
            or not math.isfinite(acquired_at)
            or not math.isfinite(expires_at)
            or not 0 <= acquired_at <= now < expires_at
            or context.full_ci_invalidating
        ):
            return False
        units = len(context.diff.files) + sum(
            1 + len(node.dependents) for node in context.dependency_graph.nodes
        )
        if units > _MAX_UNITS:
            return False
        key = context.repo_epoch, context.policy
        prior = self._entries.get(key)
        if prior is not None:
            return True
        while self._entries and (
            len(self._entries) >= _MAX_ENTRIES
            or sum(entry.units for entry in self._entries.values()) + units > _MAX_UNITS
        ):
            self._entries.popitem(last=False)
        self._entries[key] = PreparedContext(context, acquired_at, expires_at, units)
        return True

    def close(self) -> None:
        self._closed = True
        self._entries.clear()
