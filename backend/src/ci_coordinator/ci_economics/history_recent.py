from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_cursor import HistoryCursor
from ci_coordinator.ci_economics.history_scan import HistoryScanState, RecentHistoryProgress

HISTORY_DISCOVERY_INTERVAL_SECONDS: Final = 60
HISTORY_DISCOVERY_OVERLAP_SECONDS: Final = 300


def configure_recent_history(
    dataset: HistoryDataset,
    backfill: HistoryScanState,
    *,
    prior: HistoryScanState | None,
) -> HistoryScanState:
    if type(dataset) is not HistoryDataset or type(backfill) is not HistoryScanState:
        raise TypeError("recent configuration requires exact dataset and backfill")
    if backfill.lane != "backfill" or (
        dataset.scope,
        dataset.generation,
        dataset.configuration_revision,
    ) != (backfill.scope, backfill.generation, backfill.configuration_revision):
        raise ValueError("recent configuration crosses its paired dataset")
    now = dataset.configured_at
    if prior is None:
        if dataset.configuration_revision != 1:
            raise ValueError("existing history cannot silently create a missing discovery lane")
        floor = backfill.checkpoint.cursor.created_through
        cursor = HistoryCursor.start(dataset.scope, floor, floor, cycle_started_at=now)
        return HistoryScanState(
            dataset.scope,
            dataset.generation,
            dataset.configuration_revision,
            1,
            HistoryCheckpoint(cursor),
            now,
            recent=RecentHistoryProgress(floor),
        )
    if type(prior) is not HistoryScanState or prior.recent is None:
        raise TypeError("recent reconfiguration requires its exact previous lane")
    if (
        (prior.scope, prior.generation) != (dataset.scope, dataset.generation)
        or prior.configuration_revision + 1 != dataset.configuration_revision
        or now < prior.checkpoint.cursor.cycle_started_at
    ):
        raise ValueError("recent reconfiguration substitutes its predecessor")
    return replace(
        prior,
        configuration_revision=dataset.configuration_revision,
        revision=prior.revision + 1,
        next_attempt_at=now,
        lease=None,
    )


def prepare_recent_history_cycle(state: HistoryScanState, now: datetime) -> HistoryScanState | None:
    if type(state) is not HistoryScanState or state.recent is None:
        raise TypeError("recent cycle preparation requires its exact lane")
    now = utc_time(now)
    through = state.recent.completed_through
    if (
        not state.checkpoint.complete
        or state.lease is not None
        or through is None
        or now < max(state.next_attempt_at, state.checkpoint.cursor.cycle_started_at)
    ):
        return None
    overlap = min(
        through - state.recent.recovery_floor,
        timedelta(seconds=HISTORY_DISCOVERY_OVERLAP_SECONDS),
    )
    start = through - overlap
    cursor = HistoryCursor.start(
        state.scope, start, now.replace(microsecond=0), cycle_started_at=now
    )
    return replace(state, checkpoint=HistoryCheckpoint(cursor), next_attempt_at=now)
