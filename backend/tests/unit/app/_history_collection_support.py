from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from ci_economics.archive_factories import (
    ARCHIVE_TIME,
    archived_statistics,
    history_dataset,
    history_scan,
)
from prometheus_support import prometheus_samples

from ci_coordinator.app.ci_history_collection import CiHistoryCollectionService, HistoryLane
from ci_coordinator.ci_economics.archive_detail import ArchivedAttemptDetail
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.discovery import (
    ProviderObservationPage,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_ports import (
    ClaimedHistoryRecheck,
    HistoryAttemptResult,
    HistoryDeferral,
    HistoryTransition,
)
from ci_coordinator.ci_economics.history_rechecks import (
    HistoryRecheckClaim,
    HistoryRecheckHint,
    HistoryRecheckSource,
    acquire_history_recheck,
    initial_history_recheck,
)
from ci_coordinator.ci_economics.history_scan import (
    HistoryClaim,
    HistoryScanLane,
    acquire_history_claim,
)
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.observability import RuntimeMetrics

WORKER = "a" * 64
DATASET = history_dataset()
ACQUIRED = acquire_history_claim(
    DATASET, history_scan(days=0), now=ARCHIVE_TIME, worker_id=WORKER, token="b" * 64
)
assert ACQUIRED is not None
SCAN, CLAIM = ACQUIRED
STATISTICS = archived_statistics()
PAGE = ProviderObservationPage(
    ProviderRunDiscoveryPage(
        DATASET.scope,
        SCAN.checkpoint.cursor.window,
        1,
        1,
        (
            ProviderRunCollectionSource(
                STATISTICS.attempt.to_attempt(), ARCHIVE_TIME, "2026-03-10", "b" * 64
            ),
        ),
        "exhausted",
    ),
    (404,),
)
PENDING_CLAIM = HistoryClaim(replace(SCAN, checkpoint=SCAN.checkpoint.accept_page(PAGE)[0]))


def recheck_claim(source: HistoryRecheckSource) -> HistoryRecheckClaim:
    state = initial_history_recheck(
        DATASET,
        HistoryRecheckHint(
            HistoryAttemptCursor(DATASET.scope, 303, 1, 1), 404, ARCHIVE_TIME, source
        ),
        ARCHIVE_TIME,
    )
    assert state is not None
    claim = acquire_history_recheck(
        DATASET, state, now=ARCHIVE_TIME, worker_id=WORKER, token="c" * 64
    )
    assert claim is not None
    return claim


@dataclass
class CollectionBoundary:
    history: tuple[HistoryDataset, HistoryClaim] | None = (DATASET, CLAIM)
    discovery_history: tuple[HistoryDataset, HistoryClaim] | None = None
    rechecks: dict[HistoryRecheckSource, ClaimedHistoryRecheck] = field(default_factory=dict)
    page: ProviderObservationPage | ProviderAttemptDeferred = PAGE
    attempt: HistoryAttemptResult = STATISTICS
    allowed: bool = True
    transition: HistoryTransition = "applied"
    errors: dict[str, BaseException] = field(default_factory=dict)
    hook: Callable[[str], Awaitable[None]] | None = None
    calls: list[tuple[str, object]] = field(default_factory=list)
    metrics: RuntimeMetrics = field(default_factory=RuntimeMetrics)

    async def record(self, stage: str, value: object = None) -> None:
        self.calls.append((stage, value))
        if self.hook is not None:
            await self.hook(stage)
        if stage in self.errors:
            raise self.errors[stage]

    async def claim_history(
        self, *, worker_id: str, lane: HistoryScanLane = "backfill"
    ) -> tuple[HistoryDataset, HistoryClaim] | None:
        await self.record("claim_" + lane, worker_id)
        return self.history if lane == "backfill" else self.discovery_history

    async def claim_recheck(
        self, *, worker_id: str, source: HistoryRecheckSource
    ) -> ClaimedHistoryRecheck:
        await self.record("claim_" + source, worker_id)
        return self.rechecks.get(source)

    async def allows_repository(self, scope: RepositoryScope) -> bool:
        await self.record("access", scope)
        return self.allowed

    async def discover_observation_page(
        self, scope: RepositoryScope, window: RunDiscoveryWindow, *, page_number: int
    ) -> ProviderObservationPage | ProviderAttemptDeferred:
        await self.record("discover", (scope, window, page_number))
        return self.page

    async def load_history_attempt(
        self, cursor: HistoryAttemptCursor, *, run_created_at: datetime
    ) -> HistoryAttemptResult:
        await self.record("read_attempt", (cursor, run_created_at))
        return self.attempt

    async def record_history_page(
        self, claim: HistoryClaim, page: ProviderObservationPage
    ) -> HistoryTransition:
        await self.record("page", (claim, page))
        return self.transition

    async def record_history_statistics(
        self,
        claim: HistoryClaim,
        statistics: ArchivedAttemptStatistics,
        *,
        detail: ArchivedAttemptDetail | None = None,
    ) -> HistoryTransition:
        await self.record(
            "statistics" if detail is None else "statistics_detail",
            (claim, statistics) if detail is None else (claim, statistics, detail),
        )
        return self.transition

    async def record_unavailable_history_attempt(self, claim: HistoryClaim) -> HistoryTransition:
        await self.record("missing", claim)
        return self.transition

    async def skip_unselected_history_run(self, claim: HistoryClaim) -> HistoryTransition:
        await self.record("skip", claim)
        return self.transition

    async def defer_history(
        self, claim: HistoryClaim, reason: HistoryDeferral
    ) -> HistoryTransition:
        await self.record("defer", (claim, reason))
        return self.transition

    async def handoff_discovered_history_run(self, claim: HistoryClaim) -> HistoryTransition:
        await self.record("handoff", claim)
        return self.transition

    async def record_recheck_statistics(
        self,
        claim: HistoryRecheckClaim,
        statistics: ArchivedAttemptStatistics,
        *,
        detail: ArchivedAttemptDetail | None = None,
    ) -> HistoryTransition:
        await self.record(
            "recheck_statistics" if detail is None else "recheck_statistics_detail",
            (claim, statistics) if detail is None else (claim, statistics, detail),
        )
        return self.transition

    async def record_recheck_failure(
        self, claim: HistoryRecheckClaim, *, missing: bool
    ) -> HistoryTransition:
        await self.record("recheck_failure", (claim, missing))
        return self.transition

    async def wait_for_history_attempt(self, claim: HistoryRecheckClaim) -> HistoryTransition:
        await self.record("waiting", claim)
        return self.transition

    def service(self, *, worker_id: str = WORKER) -> CiHistoryCollectionService:
        return CiHistoryCollectionService(
            store=self,
            discovery=self,
            attempts=self,
            repository_access=self,
            worker_id=worker_id,
            metrics=self.metrics,
        )

    async def run(self, abort: asyncio.Event | None = None) -> tuple[str, ...]:
        return await self.service().run(abort or asyncio.Event())

    def count(self, kind: str, lane: str, outcome: str) -> float:
        return prometheus_samples(self.metrics).get(
            (
                "ci_coordinator_ci_history_" + kind + "_total",
                (("lane", lane), ("outcome", outcome)),
            ),
            0,
        )

    @classmethod
    def for_lane(cls, lane: HistoryLane) -> CollectionBoundary:
        return cls(
            history=(DATASET, PENDING_CLAIM) if lane == "backfill" else None,
            rechecks={} if lane == "backfill" else {lane: recheck_claim(lane)},
        )
