from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

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
            if self.pending_rechecks:
                raise ValueError("unconfigured history cannot have pending rechecks")
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


class HistoryAdministrationStore(Protocol):
    async def configure_history(self, command: ConfigureHistory) -> HistoryConfigurationResult: ...

    async def history_status(self, scope: RepositoryScope) -> HistoryStatus: ...

    async def repair_history_gaps(self, command: RepairHistoryGaps) -> HistoryGapRepairResult: ...
