from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from ci_coordinator.ci_economics._observation_values import digest, positive_id, utc_time
from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.observation_scan import OBSERVATION_LEASE_SECONDS, ObservationLease
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type HistoryScanOutcome = Literal[
    "page_recorded",
    "attempt_recorded",
    "refined",
    "replayed",
    "incomparable",
    "conflict",
    "unavailable",
    "capacity_reached",
    "provider_unavailable",
    "provider_malformed",
    "access_unavailable",
    "timed_out",
    "unselected",
    "recheck_queued",
]
type HistoryScanLane = Literal["backfill", "discovery"]


@dataclass(frozen=True, slots=True)
class RecentHistoryProgress:
    recovery_floor: datetime
    completed_through: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "recovery_floor", utc_time(self.recovery_floor))
        if self.recovery_floor.microsecond:
            raise ValueError("recent history floor requires second precision")
        if self.completed_through is not None:
            completed = utc_time(self.completed_through)
            if completed.microsecond or completed < self.recovery_floor:
                raise ValueError("recent completion must follow its exact recovery floor")
            object.__setattr__(self, "completed_through", completed)


@dataclass(frozen=True, slots=True)
class HistoryScanState:
    scope: RepositoryScope
    generation: int
    configuration_revision: int
    revision: int
    checkpoint: HistoryCheckpoint
    next_attempt_at: datetime
    lease: ObservationLease | None = None
    pages_seen: int = 0
    attempts_seen: int = 0
    last_outcome: HistoryScanOutcome | None = None
    recent: RecentHistoryProgress | None = None

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not RepositoryScope
            or type(self.checkpoint) is not HistoryCheckpoint
        ):
            raise TypeError("history scan requires exact scope and checkpoint")
        for value in (self.generation, self.configuration_revision, self.revision):
            positive_id(value, "history scan revision")
        object.__setattr__(self, "next_attempt_at", utc_time(self.next_attempt_at))
        for count in (self.pages_seen, self.attempts_seen):
            if type(count) is not int or not 0 <= count <= MAX_SAFE_JSON_INTEGER:
                raise ValueError("history progress count must be a non-negative safe integer")
        cursor = self.checkpoint.cursor
        if cursor.scope != self.scope:
            raise ValueError("history checkpoint crosses its scan scope")
        if self.recent is not None:
            if type(self.recent) is not RecentHistoryProgress:
                raise TypeError("recent scan requires exact recovery progress")
            if cursor.created_from < self.recent.recovery_floor or self.attempts_seen:
                raise ValueError("recent discovery cannot cross its floor or count attempt reads")
            completed = self.recent.completed_through
            if completed is not None and completed > cursor.created_through:
                raise ValueError("recent completion exceeds its frozen cycle")
            if self.checkpoint.complete and completed != cursor.created_through:
                raise ValueError("completed discovery must retain its exact completed frontier")
        if self.lease is not None and (
            type(self.lease) is not ObservationLease
            or self.checkpoint.complete
            or self.lease.acquired_at < cursor.cycle_started_at
        ):
            raise ValueError("history lease requires its current active cycle")
        if self.last_outcome not in {
            None,
            "page_recorded",
            "attempt_recorded",
            "refined",
            "replayed",
            "incomparable",
            "conflict",
            "unavailable",
            "capacity_reached",
            "provider_unavailable",
            "provider_malformed",
            "access_unavailable",
            "timed_out",
            "unselected",
            "recheck_queued",
        }:
            raise ValueError("unknown history scan outcome")

    @property
    def lane(self) -> HistoryScanLane:
        return "backfill" if self.recent is None else "discovery"


@dataclass(frozen=True, slots=True)
class HistoryClaim:
    state: HistoryScanState

    def __post_init__(self) -> None:
        if type(self.state) is not HistoryScanState or self.state.lease is None:
            raise TypeError("history claim requires the exact leased scan")


def acquire_history_claim(
    dataset: HistoryDataset, state: HistoryScanState, *, now: datetime, worker_id: str, token: str
) -> tuple[HistoryScanState, HistoryClaim] | None:
    _require_dataset_state(dataset, state)
    now = utc_time(now)
    digest(worker_id)
    digest(token)
    cursor = state.checkpoint.cursor
    if (
        not _dataset_matches(dataset, state)
        or state.checkpoint.complete
        or now < max(dataset.configured_at, cursor.cycle_started_at, state.next_attempt_at)
        or (state.lease is not None and now < state.lease.expires_at)
        or state.revision >= MAX_SAFE_JSON_INTEGER - 1
    ):
        return None
    successor = replace(
        state,
        revision=state.revision + 1,
        lease=ObservationLease(
            worker_id,
            token,
            now,
            now + timedelta(seconds=OBSERVATION_LEASE_SECONDS),
        ),
    )
    return successor, HistoryClaim(successor)


def current_history_claim(
    dataset: HistoryDataset, state: HistoryScanState, claim: HistoryClaim, now: datetime
) -> bool:
    _require_dataset_state(dataset, state)
    if type(claim) is not HistoryClaim:
        raise TypeError("history transition requires an exact claim")
    now = utc_time(now)
    lease = state.lease
    return (
        _dataset_matches(dataset, state)
        and state == claim.state
        and lease is not None
        and dataset.configured_at <= now
        and lease.acquired_at <= now < lease.expires_at
    )


def finish_history_claim(
    dataset: HistoryDataset,
    state: HistoryScanState,
    claim: HistoryClaim,
    *,
    now: datetime,
    checkpoint: HistoryCheckpoint,
    next_attempt_at: datetime,
    outcome: HistoryScanOutcome,
    pages_completed: int = 0,
    attempts_completed: int = 0,
) -> HistoryScanState | None:
    if not current_history_claim(dataset, state, claim, now):
        return None
    if state.revision == MAX_SAFE_JSON_INTEGER:
        return None
    now, next_attempt_at = utc_time(now), utc_time(next_attempt_at)
    if next_attempt_at < now:
        raise ValueError("history retry cannot be scheduled before its completion")
    if type(checkpoint) is not HistoryCheckpoint:
        raise TypeError("history completion requires an exact checkpoint")
    old, new = state.checkpoint.cursor, checkpoint.cursor
    if (new.scope, new.created_from, new.created_through, new.cycle_started_at) != (
        old.scope,
        old.created_from,
        old.created_through,
        old.cycle_started_at,
    ):
        raise ValueError("history worker cannot substitute its frozen population")
    if any(
        type(value) is not int or value not in (0, 1)
        for value in (pages_completed, attempts_completed)
    ):
        raise ValueError("history completion can consume at most one page or attempt")
    if pages_completed and attempts_completed:
        raise ValueError("one claim cannot consume a page and an attempt")
    return replace(
        state,
        revision=state.revision + 1,
        checkpoint=checkpoint,
        next_attempt_at=next_attempt_at,
        lease=None,
        pages_seen=state.pages_seen + pages_completed,
        attempts_seen=state.attempts_seen + attempts_completed,
        last_outcome=outcome,
        recent=replace(state.recent, completed_through=checkpoint.cursor.created_through)
        if state.recent is not None and checkpoint.complete
        else state.recent,
    )


def _dataset_matches(dataset: HistoryDataset, state: HistoryScanState) -> bool:
    return (
        dataset.state == "active"
        and dataset.scope == state.scope
        and dataset.generation == state.generation
        and dataset.configuration_revision == state.configuration_revision
    )


def _require_dataset_state(dataset: HistoryDataset, state: HistoryScanState) -> None:
    if type(dataset) is not HistoryDataset or type(state) is not HistoryScanState:
        raise TypeError("history lease transition requires exact dataset and scan")
