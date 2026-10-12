from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ci_coordinator.ci_economics._observation_values import positive_id, utc_time
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_scan import HistoryScanState
from ci_coordinator.config_control import RepositoryScope

type HistoryPopulationBoundSource = Literal["explicit", "repository_created_at"]


def utc_day_floor(instant: datetime) -> datetime:
    """Return midnight UTC of the calendar day containing an aware instant."""
    return utc_time(instant).replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class HistoryPopulationBound:
    """Recorded provenance of one backfill lower bound within one dataset generation."""

    scope: RepositoryScope
    generation: int
    created_from: datetime
    source: HistoryPopulationBoundSource
    repository_created_at: datetime | None
    configuration_revision: int
    recorded_at: datetime

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("history population bound requires an exact repository scope")
        positive_id(self.generation, "history population bound generation")
        positive_id(self.configuration_revision, "history population bound revision")
        created_from = utc_time(self.created_from)
        recorded_at = utc_time(self.recorded_at)
        if created_from.microsecond or created_from > recorded_at:
            raise ValueError("history population bound requires a recorded second-precision bound")
        object.__setattr__(self, "created_from", created_from)
        object.__setattr__(self, "recorded_at", recorded_at)
        if self.source == "explicit":
            if self.repository_created_at is not None:
                raise ValueError("an explicit population bound has no repository creation instant")
            return
        if self.source != "repository_created_at" or self.repository_created_at is None:
            raise ValueError("unknown history population bound source")
        created_at = utc_time(self.repository_created_at)
        if created_at > recorded_at or utc_day_floor(created_at) != created_from:
            raise ValueError("catalog population bound is not the UTC day of its creation instant")
        object.__setattr__(self, "repository_created_at", created_at)


def recorded_population_bound(
    prior_scan: HistoryScanState | None,
    successor: HistoryDataset,
    successor_scan: HistoryScanState,
    *,
    repository_created_at: datetime | None,
    recorded_at: datetime,
) -> HistoryPopulationBound | None:
    """Return the provenance row for a transition that sets a new backfill lower bound.

    Only the initial bound and a strictly earlier expansion set a bound. Rescan,
    selector restarts and policy edits keep the cursor bound and therefore its
    existing provenance.
    """
    if type(successor) is not HistoryDataset or type(successor_scan) is not HistoryScanState:
        raise TypeError("history population bound requires an exact dataset and scan")
    if prior_scan is not None and type(prior_scan) is not HistoryScanState:
        raise TypeError("history population bound requires an exact prior scan")
    bound = successor_scan.checkpoint.cursor.created_from
    if prior_scan is not None and bound >= prior_scan.checkpoint.cursor.created_from:
        if repository_created_at is not None:
            raise ValueError("a catalog population source requires a new lower bound")
        return None
    return HistoryPopulationBound(
        successor.scope,
        successor.generation,
        bound,
        "explicit" if repository_created_at is None else "repository_created_at",
        repository_created_at,
        successor.configuration_revision,
        recorded_at,
    )
