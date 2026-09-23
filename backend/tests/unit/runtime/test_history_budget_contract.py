from math import isfinite

from ci_coordinator.app.ci_history_collection import HISTORY_PROVIDER_DEADLINE_SECONDS
from ci_coordinator.ci_economics.observation_scan import OBSERVATION_LEASE_SECONDS
from ci_coordinator.runtime.history_collection_worker import HISTORY_ITEM_TIMEOUT_SECONDS


def test_history_provider_worker_lease_budgets_are_owner_bound_and_ordered() -> None:
    budgets = (
        HISTORY_PROVIDER_DEADLINE_SECONDS,
        HISTORY_ITEM_TIMEOUT_SECONDS,
        OBSERVATION_LEASE_SECONDS,
    )

    assert all(type(value) is int and isfinite(value) and value > 0 for value in budgets)
    assert HISTORY_PROVIDER_DEADLINE_SECONDS < HISTORY_ITEM_TIMEOUT_SECONDS
    assert HISTORY_ITEM_TIMEOUT_SECONDS < OBSERVATION_LEASE_SECONDS
