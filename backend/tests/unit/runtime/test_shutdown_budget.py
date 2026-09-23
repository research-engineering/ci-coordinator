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
