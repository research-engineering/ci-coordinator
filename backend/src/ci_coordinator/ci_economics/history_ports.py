from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ci_coordinator.ci_economics.archive_detail import (
    ArchivedAttemptDetail,
    validate_history_detail,
)
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_rechecks import HistoryRecheckClaim, HistoryRecheckSource
from ci_coordinator.ci_economics.history_scan import HistoryClaim, HistoryScanLane
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.config_control import RepositoryScope


@dataclass(frozen=True, slots=True)
class HistoryAttemptNotFound:
    requested: HistoryAttemptCursor

    def __post_init__(self) -> None:
        if type(self.requested) is not HistoryAttemptCursor:
            raise TypeError("missing history attempt requires its exact requested cursor")


@dataclass(frozen=True, slots=True)
class HistoryAttemptObservation:
    statistics: ArchivedAttemptStatistics
    detail: ArchivedAttemptDetail | None = None

    def __post_init__(self) -> None:
        if type(self.statistics) is not ArchivedAttemptStatistics:
            raise TypeError("history observation requires exact archived statistics")
        if self.detail is not None:
            validate_history_detail(self.statistics, self.detail)


type HistoryAttemptResult = (
    HistoryAttemptObservation
    | ArchivedAttemptStatistics
    | HistoryAttemptNotFound
    | ProviderAttemptDeferred
)


class HistoryAttemptProvider(Protocol):
    async def load_history_attempt(
        self, cursor: HistoryAttemptCursor, *, run_created_at: datetime
    ) -> HistoryAttemptResult: ...


type HistoryTransition = Literal["applied", "claim_lost", "capacity_reached"]
type ClaimedHistoryRecheck = HistoryRecheckClaim | Literal["recovered", "capacity_reached"] | None
type HistoryDeferral = Literal[
    "provider_unavailable", "provider_malformed", "access_unavailable", "timed_out"
]


type HistoryDeliveryStatus = Literal[
    "applied", "empty", "inactive", "busy", "capacity_reached", "deferred"
]


@dataclass(frozen=True, slots=True)
class HistoryDeliveryTransfer:
    status: HistoryDeliveryStatus
    candidate_count: int
    transferred_count: int
    pending_age_seconds: float | None = None
    expired_pending_sample: int | None = None


class HistoryDeliveryStore(Protocol):
    async def list_delivery_scopes(self) -> tuple[RepositoryScope, ...]: ...

    async def transfer_deliveries(self, scope: RepositoryScope) -> HistoryDeliveryTransfer: ...


class HistoryCollectionStore(Protocol):
    async def claim_history(
        self, *, worker_id: str, lane: HistoryScanLane = "backfill"
    ) -> tuple[HistoryDataset, HistoryClaim] | None: ...

    async def record_history_page(
        self, claim: HistoryClaim, page: ProviderObservationPage
    ) -> HistoryTransition: ...

    async def record_history_statistics(
        self,
        claim: HistoryClaim,
        statistics: ArchivedAttemptStatistics,
        *,
        detail: ArchivedAttemptDetail | None = None,
    ) -> HistoryTransition: ...

    async def record_unavailable_history_attempt(
        self, claim: HistoryClaim
    ) -> HistoryTransition: ...

    async def skip_unselected_history_run(self, claim: HistoryClaim) -> HistoryTransition: ...

    async def handoff_discovered_history_run(self, claim: HistoryClaim) -> HistoryTransition: ...

    async def defer_history(
        self, claim: HistoryClaim, reason: HistoryDeferral
    ) -> HistoryTransition: ...

    async def claim_recheck(
        self, *, worker_id: str, source: HistoryRecheckSource
    ) -> ClaimedHistoryRecheck: ...

    async def record_recheck_statistics(
        self,
        claim: HistoryRecheckClaim,
        statistics: ArchivedAttemptStatistics,
        *,
        detail: ArchivedAttemptDetail | None = None,
    ) -> HistoryTransition: ...

    async def record_recheck_failure(
        self, claim: HistoryRecheckClaim, *, missing: bool
    ) -> HistoryTransition: ...

    async def wait_for_history_attempt(self, claim: HistoryRecheckClaim) -> HistoryTransition: ...
