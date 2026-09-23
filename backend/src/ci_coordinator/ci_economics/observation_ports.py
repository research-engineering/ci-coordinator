from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.discovery import ProviderObservationPage, RunDiscoveryWindow
from ci_coordinator.ci_economics.observation import ObservationSnapshot
from ci_coordinator.ci_economics.observation_commands import (
    ConfigureObservation,
    ObservationWriteResult,
)
from ci_coordinator.ci_economics.observation_gaps import (
    MAX_OBSERVATION_GAP_PAGE_SIZE,
    ObservationGap,
    ObservationGapCursor,
)
from ci_coordinator.ci_economics.observation_progress import (
    ObservationFailure,
    ObservationScanProgress,
)
from ci_coordinator.ci_economics.observation_scan import ObservationClaim
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.ci_economics.sources import MAX_PROVIDER_SOURCES_PER_REPOSITORY
from ci_coordinator.config_control import RepositoryScope


@dataclass(frozen=True, slots=True)
class ClaimedObservation:
    snapshot: ObservationSnapshot
    claim: ObservationClaim

    def __post_init__(self) -> None:
        if (
            type(self.snapshot) is not ObservationSnapshot
            or type(self.claim) is not ObservationClaim
        ):
            raise TypeError("claimed observation requires exact configuration and claim")
        if (
            not self.snapshot.configuration.enabled
            or self.snapshot.scope != self.claim.scope
            or self.snapshot.revision != self.claim.config_revision
            or self.snapshot.configured_at > self.claim.lease.acquired_at
        ):
            raise ValueError("observation claim contradicts its configuration")


@dataclass(frozen=True, slots=True)
class ObservationStatus:
    scope: RepositoryScope
    snapshot: ObservationSnapshot | None
    scans: tuple[ObservationScanProgress, ...]
    occupied_source_slots: int
    detail_truncated_until: datetime | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope or type(self.scans) is not tuple:
            raise TypeError("observation status requires exact scope and scans")
        object.__setattr__(self, "observed_at", utc_time(self.observed_at))
        if self.detail_truncated_until is not None:
            object.__setattr__(
                self, "detail_truncated_until", utc_time(self.detail_truncated_until)
            )
        if (
            type(self.occupied_source_slots) is not int
            or not 0 <= self.occupied_source_slots <= MAX_PROVIDER_SOURCES_PER_REPOSITORY
        ):
            raise ValueError("observation source occupancy exceeds its quota")
        if self.snapshot is None:
            if self.scans or self.detail_truncated_until is not None:
                raise ValueError("unconfigured observation cannot have scan or gap state")
        elif (
            type(self.snapshot) is not ObservationSnapshot
            or self.snapshot.scope != self.scope
            or len(self.scans) != 2
            or any(type(scan) is not ObservationScanProgress for scan in self.scans)
            or {scan.state.lane for scan in self.scans} != {"recent", "backfill"}
            or any(
                scan.state.scope != self.scope
                or scan.state.config_revision != self.snapshot.revision
                for scan in self.scans
            )
        ):
            raise ValueError("observation status contradicts its current configuration")


@dataclass(frozen=True, slots=True)
class ObservationGapPage:
    scope: RepositoryScope
    gaps: tuple[ObservationGap, ...]
    next_cursor: str | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope or type(self.gaps) is not tuple:
            raise TypeError("observation gaps require exact scope and tuple")
        object.__setattr__(self, "observed_at", utc_time(self.observed_at))
        if len(self.gaps) > MAX_OBSERVATION_GAP_PAGE_SIZE or any(
            type(gap) is not ObservationGap
            or gap.scope != self.scope
            or gap.expires_at <= self.observed_at
            for gap in self.gaps
        ):
            raise ValueError("observation gap page violates its scope or lifetime")
        ids = tuple(gap.gap_id for gap in self.gaps)
        cursor = None if self.next_cursor is None else ObservationGapCursor.parse(self.next_cursor)
        if ids != tuple(sorted(set(ids))) or (
            cursor is not None
            and (not ids or cursor.scope != self.scope or cursor.gap_id != ids[-1])
        ):
            raise ValueError("observation gap page cursor contradicts its ordered values")


type ObservationTransitionResult = Literal["applied", "claim_lost", "capacity_reached"]


class ObservationQuery(Protocol):
    async def observation_status(self, scope: RepositoryScope) -> ObservationStatus: ...

    async def observation_gaps(
        self, scope: RepositoryScope, *, after_cursor: str | None, limit: int
    ) -> ObservationGapPage: ...


class ObservationConfigurationStore(Protocol):
    async def configure_observation(
        self, command: ConfigureObservation
    ) -> ObservationWriteResult: ...


class ObservationClaimStore(Protocol):
    async def claim_observation(self, *, worker_id: str) -> ClaimedObservation | None: ...

    async def record_observation_page(
        self,
        claim: ObservationClaim,
        page: ProviderObservationPage,
    ) -> ObservationTransitionResult: ...

    async def defer_observation(
        self,
        claim: ObservationClaim,
        reason: ObservationFailure,
    ) -> ObservationTransitionResult: ...

    async def purge_observation_gaps(self, *, scope_limit: int) -> int: ...


class ObservationProvider(Protocol):
    async def discover_observation_page(
        self,
        scope: RepositoryScope,
        window: RunDiscoveryWindow,
        *,
        page_number: int,
    ) -> ProviderObservationPage | ProviderAttemptDeferred: ...
