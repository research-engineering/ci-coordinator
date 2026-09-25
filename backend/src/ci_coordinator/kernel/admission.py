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


@dataclass(slots=True)
class GrowingAdmissionLease:
    """Created by weighted admission; its owner serializes growth and release."""

    _owner: WeightedNoQueueAdmission = field(repr=False)
    _weight: int = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)

    def try_grow(self, delta: int) -> bool:
        return self._owner._try_grow(self, delta)

    def release(self) -> None:
        self._owner._release_growing(self)


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
            active = self._active + 1
            lease = AdmissionLease(self._release)
            self._active = active
        return lease

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
        self._validate_weight(weight)
        with self._lock:
            retained_weight = self._retained_weight + weight
            if retained_weight > self._maximum_weight:
                return None
            active = self._active + 1
            lease = AdmissionLease(lambda: self._release(weight))
            self._retained_weight = retained_weight
            self._active = active
        return lease

    def try_acquire_growing(self, weight: int = 0) -> GrowingAdmissionLease | None:
        self._validate_weight(weight)
        with self._lock:
            retained_weight = self._retained_weight + weight
            if retained_weight > self._maximum_weight:
                return None
            active = self._active + 1
            lease = GrowingAdmissionLease(self, weight)
            self._retained_weight = retained_weight
            self._active = active
        return lease

    def _validate_weight(self, weight: int) -> None:
        if type(weight) is not int or not 0 <= weight <= self._maximum_weight:
            raise ValueError("admission weight must fit the declared budget")

    def _try_grow(self, lease: GrowingAdmissionLease, delta: int) -> bool:
        self._validate_weight(delta)
        with self._lock:
            if lease._released:
                raise RuntimeError("admission lease was already released")
            retained_weight = self._retained_weight + delta
            if retained_weight > self._maximum_weight:
                return False
            weight = lease._weight + delta
            self._retained_weight = retained_weight
            lease._weight = weight
            return True

    def _release_growing(self, lease: GrowingAdmissionLease) -> None:
        with self._lock:
            if lease._released:
                raise RuntimeError("admission lease was already released")
            self._release_locked(lease._weight)
            lease._weight = 0
            lease._released = True

    def _release(self, weight: int) -> None:
        with self._lock:
            self._release_locked(weight)

    def _release_locked(self, weight: int) -> None:
        if self._active < 1 or self._retained_weight < weight:
            raise RuntimeError("weighted admission release has no owner")
        active = self._active - 1
        retained_weight = self._retained_weight - weight
        self._active = active
        self._retained_weight = retained_weight
