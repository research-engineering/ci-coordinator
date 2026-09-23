from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.discovery import MAX_DISCOVERY_WINDOW_SECONDS, RunDiscoveryWindow
from ci_coordinator.ci_economics.observation import ObservationSnapshot
from ci_coordinator.ci_economics.observation_gaps import ObservationGap
from ci_coordinator.ci_economics.observation_scan import ObservationLane, ObservationScanState
from ci_coordinator.ci_economics.observation_windows import ObservationCursor
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

OBSERVATION_POLL_INTERVAL: Final = timedelta(minutes=5)
OBSERVATION_RESCAN_INTERVAL: Final = timedelta(hours=6)
OBSERVATION_OVERLAP: Final = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ObservationCyclePreparation:
    state: ObservationScanState
    gap: ObservationGap | None

    def __post_init__(self) -> None:
        if type(self.state) is not ObservationScanState:
            raise TypeError("cycle preparation requires an exact state")
        if self.gap is not None and (
            type(self.gap) is not ObservationGap
            or self.gap.scope != self.state.scope
            or self.gap.config_revision != self.state.config_revision
            or self.gap.lane != self.state.lane
            or self.state.lease is not None
            or self.state.cursor is None
            or self.gap.cycle_started_at != self.state.cursor.cycle_started_at
        ):
            raise ValueError("cycle gap must belong to its exact successor")


def initial_observation_scans(
    snapshot: ObservationSnapshot,
) -> tuple[ObservationScanState, ObservationScanState]:
    if type(snapshot) is not ObservationSnapshot:
        raise TypeError("initial scans require exact observation snapshot")
    now = snapshot.configured_at
    end = now.replace(microsecond=0)
    enabled = snapshot.configuration.enabled
    recent = ObservationScanState(
        snapshot.scope,
        snapshot.revision,
        "recent",
        1,
        ObservationCursor.start(RunDiscoveryWindow(end, end), now) if enabled else None,
        now,
    )
    initial_backfill = enabled and snapshot.configuration.backfill_days > 0
    backfill = ObservationScanState(
        snapshot.scope,
        snapshot.revision,
        "backfill",
        1,
        ObservationCursor.start(
            RunDiscoveryWindow(end - timedelta(days=snapshot.configuration.backfill_days), end), now
        )
        if initial_backfill
        else None,
        now if initial_backfill or not enabled else now + OBSERVATION_RESCAN_INTERVAL,
    )
    return recent, backfill


def prepare_observation_cycle(
    snapshot: ObservationSnapshot,
    state: ObservationScanState,
    *,
    last_completed_through: datetime | None,
    now: datetime,
) -> ObservationCyclePreparation | None:
    if type(snapshot) is not ObservationSnapshot or type(state) is not ObservationScanState:
        raise TypeError("cycle preparation requires exact snapshot and state")
    now = utc_time(now)
    completed = None if last_completed_through is None else utc_time(last_completed_through)
    if completed is not None and completed.microsecond:
        raise ValueError("completed observation endpoint must have whole-second precision")
    if (
        not snapshot.configuration.enabled
        or snapshot.scope != state.scope
        or snapshot.revision != state.config_revision
        or now < snapshot.configured_at
        or now < state.next_attempt_at
        or (state.lease is not None and now < state.lease.expires_at)
        or state.revision >= MAX_SAFE_JSON_INTEGER - 2
        or (completed is not None and completed > now)
        or (state.cursor is not None and state.cursor.cycle_started_at > now)
    ):
        return None
    end = now.replace(microsecond=0)
    lower = end - timedelta(seconds=MAX_DISCOVERY_WINDOW_SECONDS)
    if state.cursor is not None and state.cursor.window.created_from >= lower:
        return ObservationCyclePreparation(state, None)
    if state.cursor is not None:
        start = state.cursor.window.created_from
    elif state.lane == "recent":
        start = max(
            snapshot.configured_at.replace(microsecond=0),
            (completed or snapshot.configured_at.replace(microsecond=0)) - OBSERVATION_OVERLAP,
        )
    else:
        start = lower
    gap = None
    if start < lower:
        gap = ObservationGap(
            snapshot.scope,
            snapshot.revision,
            snapshot.configuration.selector_digest,
            state.lane,
            now,
            start,
            lower,
            "outage_window_lost",
        )
    cursor = ObservationCursor.start(RunDiscoveryWindow(max(start, lower), end), now)
    return ObservationCyclePreparation(
        replace(state, revision=state.revision + 1, cursor=cursor, lease=None, next_attempt_at=now),
        gap,
    )


def observation_cycle_due_at(lane: ObservationLane, now: datetime) -> datetime:
    if lane not in {"recent", "backfill"}:
        raise ValueError("unknown observation lane")
    return utc_time(now) + (
        OBSERVATION_POLL_INTERVAL if lane == "recent" else OBSERVATION_RESCAN_INTERVAL
    )
