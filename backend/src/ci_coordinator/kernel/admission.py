"""Process-local no-queue admission primitives with exactly-once leases."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock


@dataclass(slots=True)
class AdmissionLease:
    """One acquired capacity unit that can be released exactly once."""

    _release_capacity: Callable[[], None] = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)
    _release_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                raise RuntimeError("admission lease was already released")
            self._release_capacity()
            self._released = True


class NoQueueAdmission:
    """A thread-safe finite counter whose acquisition never waits."""

    def __init__(self, limit: int) -> None:
        if type(limit) is not int or limit < 1:
            raise ValueError("no-queue admission limit must be positive")
        self._limit = limit
        self._active = 0
        self._lock = Lock()

    def try_acquire(self) -> AdmissionLease | None:
        with self._lock:
            if self._active >= self._limit:
                return None
            self._active += 1
        return AdmissionLease(self._release)

    def _release(self) -> None:
        with self._lock:
            if self._active < 1:
                raise RuntimeError("no-queue admission release has no owner")
            self._active -= 1


class WeightedNoQueueAdmission:
    """A thread-safe weighted budget with immediate admission or rejection."""

    def __init__(self, maximum_weight: int) -> None:
        if type(maximum_weight) is not int or maximum_weight < 1:
            raise ValueError("weighted admission maximum must be positive")
        self._maximum_weight = maximum_weight
        self._retained_weight = 0
        self._active = 0
        self._lock = Lock()

    def try_acquire(self, weight: int) -> AdmissionLease | None:
        if type(weight) is not int or not 0 <= weight <= self._maximum_weight:
            raise ValueError("admission weight must fit the declared budget")
        with self._lock:
            if self._retained_weight + weight > self._maximum_weight:
                return None
            self._retained_weight += weight
            self._active += 1
        return AdmissionLease(lambda: self._release(weight))

    def _release(self, weight: int) -> None:
        with self._lock:
            if self._active < 1 or self._retained_weight < weight:
                raise RuntimeError("weighted admission release has no owner")
            self._active -= 1
            self._retained_weight -= weight
