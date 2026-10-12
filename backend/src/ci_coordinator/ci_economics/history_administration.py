from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.history_commands import (
    ConfigureHistory,
    HistoryConfigurationResult,
)
from ci_coordinator.ci_economics.history_configuration import HistoryDataset, HistoryDefaults
from ci_coordinator.ci_economics.history_gap_recovery import (
    HistoryGapRepairResult,
    RepairHistoryGaps,
)
from ci_coordinator.ci_economics.history_population_bound import HistoryPopulationBound
from ci_coordinator.ci_economics.history_rechecks import MAX_HISTORY_RECHECK_RUNS
from ci_coordinator.ci_economics.history_scan import HistoryScanState
from ci_coordinator.config_control import RepositoryScope


@dataclass(frozen=True, slots=True)
class HistoryStatus:
    scope: RepositoryScope
    defaults: HistoryDefaults
    dataset: HistoryDataset | None
    scan: HistoryScanState | None
    pending_rechecks: int
    observed_at: datetime
    discovery: HistoryScanState | None = None
    population_bound: HistoryPopulationBound | None = None

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope or type(self.defaults) is not HistoryDefaults:
            raise TypeError("history status requires exact scope and defaults")
        object.__setattr__(self, "observed_at", utc_time(self.observed_at))
        if (
            type(self.pending_rechecks) is not int
            or not 0 <= self.pending_rechecks <= MAX_HISTORY_RECHECK_RUNS
        ):
            raise ValueError("history recheck count exceeds its admitted bound")
        if self.defaults.updated_at > self.observed_at:
            raise ValueError("history defaults postdate their observation")
        dataset, scan = self.dataset, self.scan
        if dataset is None and scan is None and self.discovery is None:
            if self.pending_rechecks or self.population_bound is not None:
                raise ValueError("unconfigured history cannot have pending work or a bound")
            return
        if type(dataset) is not HistoryDataset or type(scan) is not HistoryScanState:
            raise TypeError("history status requires an exact dataset and scan pair")
        if (
            dataset.scope != self.scope
            or scan.lane != "backfill"
            or scan.scope != self.scope
            or dataset.generation != scan.generation
            or dataset.configuration_revision != scan.configuration_revision
        ):
            raise ValueError("history status crosses dataset authority")
        if max(dataset.configured_at, scan.checkpoint.cursor.cycle_started_at) > self.observed_at:
            raise ValueError("history state postdates its observation")
        discovery = self.discovery
        if type(discovery) is not HistoryScanState or discovery.lane != "discovery":
            raise ValueError("configured history requires independent discovery progress")
        if (
            discovery.scope != self.scope
            or discovery.generation != dataset.generation
            or discovery.configuration_revision != dataset.configuration_revision
            or discovery.checkpoint.cursor.cycle_started_at > self.observed_at
        ):
            raise ValueError("history discovery crosses dataset authority or observation time")
        bound = self.population_bound
        if bound is not None and (
            type(bound) is not HistoryPopulationBound
            or bound.scope != self.scope
            or bound.generation != dataset.generation
            or bound.configuration_revision > dataset.configuration_revision
            or bound.created_from < scan.checkpoint.cursor.created_from
            or bound.recorded_at > self.observed_at
        ):
            raise ValueError("history population bound crosses dataset authority")

    @property
    def current_population_bound(self) -> HistoryPopulationBound | None:
        """Return recorded provenance only for the bound the backfill cursor still uses."""
        bound = self.population_bound
        if bound is None or self.scan is None:
            return None
        return bound if bound.created_from == self.scan.checkpoint.cursor.created_from else None


@dataclass(frozen=True, slots=True)
class RepositoryCreationObserved:
    """One scoped provider read of repository creation metadata; ``None`` means absent."""

    scope: RepositoryScope
    created_at: datetime | None

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("repository creation observation requires an exact scope")
        if self.created_at is not None:
            object.__setattr__(self, "created_at", utc_time(self.created_at))


@dataclass(frozen=True, slots=True)
class RepositoryCreationUnavailable:
    reason: Literal["provider_unavailable", "provider_malformed", "provider_binding_mismatch"]

    def __post_init__(self) -> None:
        if self.reason not in {
            "provider_unavailable",
            "provider_malformed",
            "provider_binding_mismatch",
        }:
            raise ValueError("unknown repository creation unavailability")


class HistoryRepositoryCreationReader(Protocol):
    async def read_repository_created_at(
        self, scope: RepositoryScope
    ) -> RepositoryCreationObserved | RepositoryCreationUnavailable: ...


class HistoryAdministrationStore(Protocol):
    async def find_history_receipt(
        self, command: ConfigureHistory
    ) -> HistoryConfigurationResult | None: ...

    async def configure_history(
        self,
        command: ConfigureHistory,
        *,
        verified_creation: RepositoryCreationObserved | None = None,
    ) -> HistoryConfigurationResult: ...

    async def history_status(self, scope: RepositoryScope) -> HistoryStatus: ...

    async def repair_history_gaps(self, command: RepairHistoryGaps) -> HistoryGapRepairResult: ...
