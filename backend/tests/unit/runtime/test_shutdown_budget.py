from __future__ import annotations

import pytest

from ci_coordinator.runtime.shutdown_budget import partition_shutdown_budget


@pytest.mark.parametrize("total_seconds", [1, 30, 3_600])
def test_shutdown_partition_is_total_and_background_is_nested(total_seconds: int) -> None:
    budget = partition_shutdown_budget(total_seconds)

    assert (
        budget.uvicorn_grace_seconds
        + budget.resource_cleanup_seconds
        + budget.orchestration_reserve_seconds
        == total_seconds
    )
    assert 0 < budget.background_drain_seconds <= budget.resource_cleanup_seconds
    assert budget.background_drain_seconds == budget.resource_cleanup_seconds / 2
    assert budget.orchestration_reserve_seconds > 0.1


def test_documented_thirty_second_budget_has_independent_literal_partitions() -> None:
    budget = partition_shutdown_budget(30)

    assert budget.total_seconds == 30
    assert budget.uvicorn_grace_seconds == 15
    assert budget.resource_cleanup_seconds == 14.0
    assert budget.background_drain_seconds == 7.0
    assert budget.orchestration_reserve_seconds == 1.0
