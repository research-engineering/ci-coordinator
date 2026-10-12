from __future__ import annotations

from typing import cast

import pytest

from ci_coordinator.ci_economics import load_bundled_ci_economics_profile
from ci_coordinator.runtime.connection_shares import (
    FOREGROUND_CONNECTION_RESERVE,
    ConnectionShare,
    runtime_connection_shares,
)
from ci_coordinator.runtime.webhook_admission import WEBHOOK_VERIFIER_PERMITS


def test_source_counted_background_share_exceeds_the_declared_pool_of_sixteen() -> None:
    """Kills an inventory that omits the archive delivery workers or concurrent maintenance."""
    inventory = runtime_connection_shares(load_bundled_ci_economics_profile())

    assert {share.population: share.maximum_connections for share in inventory.background} == {
        "reconciliation_round": 1,
        "ci_economics_collection": 4,
        "ci_economics_expiry": 1,
        "ci_economics_tombstone_purge": 1,
        "ci_economics_observation_cleanup": 1,
        "ci_observation_discovery": 2,
        "ci_observation_gap_cleanup": 1,
        "ci_history_delivery": 4,
        "ci_history_detail_cleanup": 1,
        "activity_cleanup": 1,
        "ci_history_collection_lanes": 4,
        "planning_preparation": 1,
    }
    assert inventory.webhook_permits == WEBHOOK_VERIFIER_PERMITS == 4
    assert inventory.foreground_reserve == FOREGROUND_CONNECTION_RESERVE == 4
    assert inventory.background_connections == 22
    assert inventory.required_pool_size == 30
    assert not inventory.admits(16)


@pytest.mark.parametrize(("pool_size", "admitted"), [(29, False), (30, True), (31, True)])
def test_connection_share_sum_admits_equality_and_rejects_one_less(
    pool_size: int, admitted: bool
) -> None:
    """Kills an off-by-one comparison in N + B + F_min <= pool."""
    inventory = runtime_connection_shares(load_bundled_ci_economics_profile())

    assert inventory.admits(pool_size) is admitted


@pytest.mark.parametrize("connections", [0, -1, True, 1.0])
def test_connection_share_rejects_unbounded_or_non_integer_counts(connections: object) -> None:
    with pytest.raises(ValueError, match="positive connection count"):
        ConnectionShare("population", cast(int, connections))
