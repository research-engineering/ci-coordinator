import asyncio
from dataclasses import replace

import pytest
from app._history_collection_support import (
    DATASET,
    PENDING_CLAIM,
    CollectionBoundary,
    recheck_claim,
)

from ci_coordinator.ci_economics.history_scan import HistoryClaim, RecentHistoryProgress
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred


def test_discovery_hands_off_the_current_run_without_direct_attempt_reads() -> None:
    async def scenario() -> None:
        prior = PENDING_CLAIM.state
        claim = HistoryClaim(
            replace(prior, recent=RecentHistoryProgress(prior.checkpoint.cursor.created_from))
        )
        boundary = CollectionBoundary(history=None, discovery_history=(DATASET, claim))
        assert await boundary.service().collect_next("discovery", asyncio.Event()) == "applied"
        assert ("handoff", claim) in boundary.calls
        assert not any(
            stage in {"read_attempt", "statistics", "recheck_statistics", "access"}
            for stage, _ in boundary.calls
        )
        assert boundary.count("items", "discovery", "applied") == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("pending", [False, True])
def test_successful_nonterminal_read_uses_waiting_not_failure_admission(pending: bool) -> None:
    async def scenario() -> None:
        claim = recheck_claim("recent")
        boundary = CollectionBoundary(
            history=None,
            rechecks={"recent": claim},
            attempt=ProviderAttemptDeferred(
                "provider_not_terminal" if pending else "provider_unavailable"
            ),
        )
        result = await boundary.run()
        assert result[1] == "applied"
        assert (("waiting", claim) in boundary.calls) is pending
        assert (("recheck_failure", (claim, False)) in boundary.calls) is not pending

    asyncio.run(scenario())
