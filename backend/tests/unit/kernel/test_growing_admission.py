from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Never, cast

import pytest

from ci_coordinator.kernel import AdmissionLease, NoQueueAdmission, WeightedNoQueueAdmission
from ci_coordinator.kernel import admission as admission_module


def _assert_full_capacity(budget: WeightedNoQueueAdmission, maximum: int) -> None:
    full = budget.try_acquire(maximum)
    assert type(full) is AdmissionLease
    assert budget.try_acquire(1) is None
    full.release()


def test_fixed_and_growing_leases_conserve_one_budget() -> None:
    budget = WeightedNoQueueAdmission(10)
    fixed = budget.try_acquire(4)
    growing = budget.try_acquire_growing(2)
    assert type(fixed) is AdmissionLease
    assert growing is not None
    assert growing.try_grow(3)
    assert not growing.try_grow(2)
    last = budget.try_acquire(1)
    assert last is not None
    assert not growing.try_grow(1)
    fixed.release()
    replacement = budget.try_acquire_growing(4)
    assert replacement is not None
    assert budget.try_acquire_growing(1) is None
    growing.release()
    last.release()
    replacement.release()
    _assert_full_capacity(budget, 10)


def test_zero_weight_and_zero_growth_remain_admitted_at_full_capacity() -> None:
    budget = WeightedNoQueueAdmission(1)
    full = budget.try_acquire(1)
    empty = budget.try_acquire_growing()
    assert full is not None and empty is not None
    assert empty.try_grow(0)
    assert not empty.try_grow(1)
    full.release()
    assert empty.try_grow(1)
    assert budget.try_acquire(1) is None
    empty.release()
    _assert_full_capacity(budget, 1)


@pytest.mark.parametrize("initial", [0, 1, 10])
def test_growth_can_reach_the_exact_maximum(initial: int) -> None:
    budget = WeightedNoQueueAdmission(10)
    lease = budget.try_acquire_growing(initial)
    assert lease is not None
    assert lease.try_grow(10 - initial)
    assert not lease.try_grow(1)
    lease.release()
    _assert_full_capacity(budget, 10)


@pytest.mark.parametrize("value", [-1, 11, True, False, 0.5, "1", None])
def test_invalid_initial_weight_cannot_reserve_capacity(value: object) -> None:
    budget = WeightedNoQueueAdmission(10)
    with pytest.raises(ValueError, match="weight must fit"):
        budget.try_acquire_growing(cast(int, value))
    _assert_full_capacity(budget, 10)


@pytest.mark.parametrize("value", [-1, 11, True, False, 0.5, "1", None])
def test_invalid_growth_preserves_the_prior_balance(value: object) -> None:
    budget = WeightedNoQueueAdmission(10)
    lease = budget.try_acquire_growing(3)
    assert lease is not None
    with pytest.raises(ValueError, match="weight must fit"):
        lease.try_grow(cast(int, value))
    remainder = budget.try_acquire(7)
    assert remainder is not None
    assert budget.try_acquire(1) is None
    remainder.release()
    lease.release()
    _assert_full_capacity(budget, 10)


def test_released_lease_cannot_grow_or_refund_twice() -> None:
    budget = WeightedNoQueueAdmission(10)
    lease = budget.try_acquire_growing(3)
    assert lease is not None
    lease.release()
    with pytest.raises(ValueError, match="weight must fit"):
        lease.try_grow(-1)
    for delta in (0, 1):
        with pytest.raises(RuntimeError, match="already released"):
            lease.try_grow(delta)
    with pytest.raises(RuntimeError, match="already released"):
        lease.release()
    _assert_full_capacity(budget, 10)


def test_concurrent_growth_has_no_lost_or_partial_charges() -> None:
    budget = WeightedNoQueueAdmission(10)
    fixed = budget.try_acquire(2)
    lease = budget.try_acquire_growing()
    assert fixed is not None and lease is not None
    start = Barrier(16)

    def grow() -> bool:
        start.wait(timeout=10)
        return lease.try_grow(1)

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = tuple(executor.map(lambda _: grow(), range(16)))
    assert sum(results) == 8
    assert budget.try_acquire(1) is None
    lease.release()
    restored = budget.try_acquire(8)
    assert restored is not None
    assert budget.try_acquire(1) is None
    restored.release()
    fixed.release()
    _assert_full_capacity(budget, 10)


@pytest.mark.parametrize("operation", ["grow", "release"])
def test_release_races_linearize_without_resurrection_or_extra_credit(operation: str) -> None:
    budget = WeightedNoQueueAdmission(10)
    fixed = budget.try_acquire(6)
    lease = budget.try_acquire_growing(3)
    assert fixed is not None and lease is not None
    start = Barrier(2)

    def release() -> str:
        start.wait(timeout=10)
        try:
            lease.release()
            return "released"
        except RuntimeError as error:
            assert str(error) == "admission lease was already released"
            return "closed"

    def contend() -> str:
        if operation == "release":
            return release()
        start.wait(timeout=10)
        try:
            assert lease.try_grow(1)
            return "grown"
        except RuntimeError as error:
            assert str(error) == "admission lease was already released"
            return "closed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(release)
        second = executor.submit(contend)
        outcomes = (first.result(), second.result())
    if operation == "release":
        assert sorted(outcomes) == ["closed", "released"]
    else:
        assert outcomes[0] == "released"
        assert outcomes[1] in {"grown", "closed"}
    restored = budget.try_acquire(4)
    assert restored is not None
    assert budget.try_acquire(1) is None
    with pytest.raises(RuntimeError, match="already released"):
        lease.try_grow(0)
    restored.release()
    fixed.release()
    _assert_full_capacity(budget, 10)


@pytest.mark.parametrize("kind", ["unweighted", "fixed", "growing"])
def test_lease_allocation_failure_precedes_capacity_publication(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    unweighted = NoQueueAdmission(1)
    weighted = WeightedNoQueueAdmission(1)

    def acquire() -> object:
        if kind == "unweighted":
            return unweighted.try_acquire()
        if kind == "fixed":
            return weighted.try_acquire(1)
        return weighted.try_acquire_growing(1)

    def allocation_failure(*_: object, **__: object) -> Never:
        raise MemoryError("lease allocation failed")

    with monkeypatch.context() as patch:
        name = "GrowingAdmissionLease" if kind == "growing" else "AdmissionLease"
        patch.setattr(admission_module, name, allocation_failure)
        with pytest.raises(MemoryError, match="lease allocation failed"):
            acquire()
    if kind == "unweighted":
        lease = unweighted.try_acquire()
        assert lease is not None
        assert unweighted.try_acquire() is None
        lease.release()
    else:
        _assert_full_capacity(weighted, 1)


def test_fixed_callback_constructor_is_unchanged() -> None:
    releases: list[str] = []
    lease = AdmissionLease(lambda: releases.append("released"))
    lease.release()
    with pytest.raises(RuntimeError, match="already released"):
        lease.release()
    assert releases == ["released"]
