import asyncio

import pytest
from app._history_collection_support import (
    PENDING_CLAIM,
    STATISTICS,
    CollectionBoundary,
    recheck_claim,
)
from ci_economics.archive_factories import archived_detail

from ci_coordinator.app.ci_history_collection import HistoryLane
from ci_coordinator.ci_economics.history_ports import HistoryAttemptObservation


@pytest.mark.parametrize("lane", ["backfill", "recent", "repair"])
def test_valid_sidecar_is_bound_and_routed_as_optional_detail_keyword(lane: HistoryLane) -> None:
    async def scenario() -> None:
        detail = archived_detail(STATISTICS)
        boundary = CollectionBoundary.for_lane(lane)
        boundary.attempt = HistoryAttemptObservation(STATISTICS, detail)
        assert await boundary.service().collect_next(lane, asyncio.Event()) == "applied"
        kind = "statistics_detail" if lane == "backfill" else "recheck_statistics_detail"
        claim = PENDING_CLAIM if lane == "backfill" else recheck_claim(lane)
        assert [value for name, value in boundary.calls if name == kind] == [
            (claim, STATISTICS, detail)
        ]
        assert not any(name in {"statistics", "recheck_statistics"} for name, _ in boundary.calls)

    asyncio.run(scenario())
