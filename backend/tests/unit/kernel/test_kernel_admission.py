"""Concurrency witnesses for process-local admission primitives."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from typing import cast

import pytest

from ci_coordinator.kernel import NoQueueAdmission, WeightedNoQueueAdmission


@pytest.mark.parametrize("constructor", [NoQueueAdmission, WeightedNoQueueAdmission])
@pytest.mark.parametrize("limit", [0, -1, True, False, 1.5, "1", None])
def test_admission_requires_a_positive_exact_integer_limit(
    constructor: Callable[[int], object], limit: object
) -> None:
    with pytest.raises(ValueError):
        constructor(cast(int, limit))


def test_no_queue_admission_rejects_at_the_exact_limit_and_reuses_capacity() -> None:
    admission = NoQueueAdmission(2)
    first = admission.try_acquire()
    second = admission.try_acquire()

    assert first is not None
    assert second is not None
    assert admission.try_acquire() is None

    first.release()
    replacement = admission.try_acquire()
    assert replacement is not None

    second.release()
    replacement.release()
    restored = (admission.try_acquire(), admission.try_acquire())
    assert admission.try_acquire() is None
    for lease in restored:
        assert lease is not None
        lease.release()


def test_weighted_admission_conserves_the_exact_budget() -> None:
    admission = WeightedNoQueueAdmission(10)
    first = admission.try_acquire(4)
    second = admission.try_acquire(6)

    assert first is not None
    assert second is not None
    assert admission.try_acquire(1) is None

    first.release()
    replacement = admission.try_acquire(4)
    assert replacement is not None

    second.release()
    replacement.release()
    full = admission.try_acquire(10)
    assert full is not None
    assert admission.try_acquire(1) is None
    full.release()


def test_weighted_admission_includes_zero_and_the_exact_minimum_budget() -> None:
    admission = WeightedNoQueueAdmission(1)
    empty = admission.try_acquire(0)
    full = admission.try_acquire(1)
    assert empty is not None
    assert full is not None
    assert admission.try_acquire(1) is None
    empty.release()
    assert admission.try_acquire(1) is None
    full.release()
    restored = admission.try_acquire(1)
    assert restored is not None
    restored.release()


@pytest.mark.parametrize("weight", [-1, 2, True, False, 0.5, "0", None])
def test_invalid_weight_is_rejected_without_reserving_capacity(weight: object) -> None:
    admission = WeightedNoQueueAdmission(1)
    with pytest.raises(ValueError):
        admission.try_acquire(cast(int, weight))
    full = admission.try_acquire(1)
    assert full is not None
    assert admission.try_acquire(1) is None
    full.release()


def test_one_lease_cannot_release_capacity_twice() -> None:
    admission = NoQueueAdmission(1)
    lease = admission.try_acquire()
    assert lease is not None

    lease.release()

    with pytest.raises(RuntimeError, match="already released"):
        lease.release()


def test_concurrent_admission_never_exceeds_the_limit() -> None:
    limit = 4
    contenders = 32
    barrier = Barrier(contenders)
    all_attempted = Event()
    release_capacity = Event()
    admission = NoQueueAdmission(limit)
    admitted = 0
    attempted = 0
    admitted_lock = Lock()

    def contend() -> bool:
        nonlocal admitted, attempted
        barrier.wait()
        lease = admission.try_acquire()
        with admitted_lock:
            attempted += 1
            if lease is not None:
                admitted += 1
            if attempted == contenders:
                all_attempted.set()
        if lease is None:
            return False
        release_capacity.wait()
        lease.release()
        return True

    with ThreadPoolExecutor(max_workers=contenders) as executor:
        futures = tuple(executor.submit(contend) for _ in range(contenders))
        assert all_attempted.wait(timeout=1)
        release_capacity.set()
        results = tuple(future.result(timeout=1) for future in futures)

    assert admitted == limit
    assert sum(results) == limit
