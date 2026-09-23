from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Literal

from ci_coordinator.ci_economics._observation_values import digest, positive_id, utc_time
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.observation_scan import OBSERVATION_LEASE_SECONDS, ObservationLease
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

MAX_HISTORY_RECHECK_RUNS: Final = 256
MAX_HISTORY_REPAIR_RUNS: Final = 128
MAX_HISTORY_RECHECK_ACQUISITIONS: Final = 3
HISTORY_RECHECK_BACKOFF_SECONDS: Final = 60
MAX_HISTORY_RECHECK_JITTER_SECONDS: Final = 60
type HistoryRecheckSource = Literal["recent", "repair"]
type HistoryRecheckOutcome = Literal[
    "recorded", "unavailable", "deferred", "retry_exhausted", "capacity_reached", "waiting"
]


@dataclass(frozen=True, slots=True)
class HistoryRecheckHint:
    cursor: HistoryAttemptCursor
    workflow_id: int
    run_created_at: datetime
    source: HistoryRecheckSource

    def __post_init__(self) -> None:
        if type(self.cursor) is not HistoryAttemptCursor:
            raise TypeError("history hint requires an exact attempt interval")
        positive_id(self.workflow_id, "history workflow ID")
        object.__setattr__(self, "run_created_at", utc_time(self.run_created_at))
        if self.source not in {"recent", "repair"}:
            raise ValueError("history hint requires a known source")


@dataclass(frozen=True, slots=True)
class HistoryRecheckState:
    hint: HistoryRecheckHint
    generation: int
    revision: int
    next_attempt: int
    next_attempt_at: datetime
    acquisition_count: int = 0
    lease: ObservationLease | None = None

    def __post_init__(self) -> None:
        if type(self.hint) is not HistoryRecheckHint:
            raise TypeError("history recheck requires an exact admitted interval")
        for value in (self.generation, self.revision, self.next_attempt):
            positive_id(value, "history recheck identity")
        if (
            not self.hint.cursor.next_attempt
            <= self.next_attempt
            <= self.hint.cursor.latest_attempt
        ):
            raise ValueError("recheck cursor lies outside its original interval")
        if (
            type(self.acquisition_count) is not int
            or not 0 <= self.acquisition_count <= MAX_HISTORY_RECHECK_ACQUISITIONS
        ):
            raise ValueError("history acquisition count exceeds its finite budget")
        if self.acquisition_count == MAX_HISTORY_RECHECK_ACQUISITIONS and self.lease is None:
            raise ValueError("exhausted recheck must retain its recovery lease")
        if self.revision <= self.acquisition_count:
            raise ValueError("recheck revision must include every acquisition")
        object.__setattr__(self, "next_attempt_at", utc_time(self.next_attempt_at))
        if self.next_attempt_at < self.hint.run_created_at:
            raise ValueError("recheck cannot precede its workflow run")
        if self.lease is not None and (
            type(self.lease) is not ObservationLease
            or self.acquisition_count == 0
            or self.lease.acquired_at < self.next_attempt_at
        ):
            raise ValueError("recheck lease must follow an eligible counted acquisition")

    @property
    def cursor(self) -> HistoryAttemptCursor:
        return replace(self.hint.cursor, next_attempt=self.next_attempt)


@dataclass(frozen=True, slots=True)
class HistoryRecheckClaim:
    state: HistoryRecheckState
    configuration_revision: int

    def __post_init__(self) -> None:
        if type(self.state) is not HistoryRecheckState or self.state.lease is None:
            raise TypeError("history recheck claim requires an exact leased state")
        positive_id(self.configuration_revision, "history claim configuration revision")


@dataclass(frozen=True, slots=True)
class HistoryRecheckTransition:
    successor: HistoryRecheckState | None
    outcome: HistoryRecheckOutcome

    def __post_init__(self) -> None:
        if self.successor is not None and type(self.successor) is not HistoryRecheckState:
            raise TypeError("recheck transition requires an exact successor or deletion")
        if self.outcome not in {
            "recorded",
            "unavailable",
            "deferred",
            "retry_exhausted",
            "capacity_reached",
            "waiting",
        }:
            raise ValueError("unknown history recheck outcome")
        if self.successor is not None and self.successor.lease is not None:
            raise ValueError("completed recheck transition cannot retain a lease")
        if self.outcome in {"deferred", "capacity_reached", "waiting"} and self.successor is None:
            raise ValueError("deferred recheck cannot delete pending work")


def recheck_capacity_available(total: int, repair_only: int, source: HistoryRecheckSource) -> bool:
    if (
        type(total) is not int
        or type(repair_only) is not int
        or not 0 <= repair_only <= min(total, MAX_HISTORY_REPAIR_RUNS)
        or not 0 <= total <= MAX_HISTORY_RECHECK_RUNS
        or source not in {"recent", "repair"}
    ):
        raise ValueError("recheck capacity requires bounded exact counts and source")
    return total < MAX_HISTORY_RECHECK_RUNS and (
        source == "recent" or repair_only < MAX_HISTORY_REPAIR_RUNS
    )


def initial_history_recheck(
    dataset: HistoryDataset, hint: HistoryRecheckHint, now: datetime
) -> HistoryRecheckState | None:
    if type(dataset) is not HistoryDataset or type(hint) is not HistoryRecheckHint:
        raise TypeError("history recheck admission requires exact dataset and hint")
    now = utc_time(now)
    if now < hint.run_created_at:
        return None
    state = HistoryRecheckState(hint, dataset.generation, 1, hint.cursor.next_attempt, now)
    return state if _dataset_admits(dataset, state, now) else None


def merge_history_recheck_hint(
    state: HistoryRecheckState, hint: HistoryRecheckHint, *, revisit_completed: bool = False
) -> HistoryRecheckState:
    if type(state) is not HistoryRecheckState or type(hint) is not HistoryRecheckHint:
        raise TypeError("history hint merge requires exact values")
    if type(revisit_completed) is not bool or (revisit_completed and hint.source != "repair"):
        raise ValueError("only a parent-bound historical repair may revisit completed work")
    old = state.hint
    if (hint.cursor.scope, hint.cursor.workflow_run_id, hint.workflow_id, hint.run_created_at) != (
        old.cursor.scope,
        old.cursor.workflow_run_id,
        old.workflow_id,
        old.run_created_at,
    ):
        raise ValueError("history hint contradicts the existing source identity")
    lower = min(old.cursor.next_attempt, hint.cursor.next_attempt)
    latest = max(old.cursor.latest_attempt, hint.cursor.latest_attempt)
    source: HistoryRecheckSource = "recent" if "recent" in {old.source, hint.source} else "repair"
    merged = replace(
        old, cursor=replace(old.cursor, next_attempt=lower, latest_attempt=latest), source=source
    )
    next_attempt = (
        min(state.next_attempt, hint.cursor.next_attempt)
        if (revisit_completed or lower < old.cursor.next_attempt)
        else state.next_attempt
    )
    if merged == old and next_attempt == state.next_attempt:
        return state
    if state.revision >= MAX_SAFE_JSON_INTEGER - 1:
        raise ValueError("history hint cannot exhaust the terminal revision reserve")
    rewound = next_attempt < state.next_attempt
    return replace(
        state,
        hint=merged,
        revision=state.revision + 1,
        next_attempt=next_attempt,
        acquisition_count=0 if rewound else state.acquisition_count,
        lease=None if rewound else state.lease,
    )


def acquire_history_recheck(
    dataset: HistoryDataset,
    state: HistoryRecheckState,
    *,
    now: datetime,
    worker_id: str,
    token: str,
) -> HistoryRecheckClaim | None:
    now = utc_time(now)
    digest(worker_id)
    digest(token)
    if (
        not _dataset_admits(dataset, state, now)
        or now < state.next_attempt_at
        or (state.lease is not None and now < state.lease.expires_at)
        or state.acquisition_count == MAX_HISTORY_RECHECK_ACQUISITIONS
        or state.revision >= MAX_SAFE_JSON_INTEGER - 1
    ):
        return None
    return HistoryRecheckClaim(
        replace(
            state,
            revision=state.revision + 1,
            acquisition_count=state.acquisition_count + 1,
            lease=ObservationLease(
                worker_id, token, now, now + timedelta(seconds=OBSERVATION_LEASE_SECONDS)
            ),
        ),
        dataset.configuration_revision,
    )


def current_history_recheck(
    dataset: HistoryDataset, state: HistoryRecheckState, claim: HistoryRecheckClaim, now: datetime
) -> bool:
    if type(claim) is not HistoryRecheckClaim:
        raise TypeError("history recheck completion requires an exact claim")
    now = utc_time(now)
    return (
        _dataset_admits(dataset, state, now)
        and state == claim.state
        and dataset.configuration_revision == claim.configuration_revision
        and state.lease is not None
        and state.lease.acquired_at <= now < state.lease.expires_at
    )


def finish_history_recheck(
    dataset: HistoryDataset,
    state: HistoryRecheckState,
    claim: HistoryRecheckClaim,
    *,
    now: datetime,
    outcome: Literal["recorded", "unavailable", "deferred", "capacity_reached", "waiting"],
    jitter_seconds: int = 0,
) -> HistoryRecheckTransition | None:
    if outcome not in {"recorded", "unavailable", "deferred", "capacity_reached", "waiting"}:
        raise ValueError("unsupported history recheck completion")
    if (
        type(jitter_seconds) is not int
        or not 0 <= jitter_seconds <= MAX_HISTORY_RECHECK_JITTER_SECONDS
    ):
        raise ValueError("history recheck jitter exceeds its bound")
    now = utc_time(now)
    if (
        not current_history_recheck(dataset, state, claim, now)
        or state.revision == MAX_SAFE_JSON_INTEGER
    ):
        return None
    if outcome in {"capacity_reached", "waiting"}:
        return HistoryRecheckTransition(
            replace(
                state,
                revision=state.revision + 1,
                lease=None,
                acquisition_count=state.acquisition_count - 1,
                next_attempt_at=now
                + timedelta(seconds=HISTORY_RECHECK_BACKOFF_SECONDS + jitter_seconds),
            ),
            outcome,
        )
    if outcome != "deferred":
        return HistoryRecheckTransition(_advance(state, now), outcome)
    if state.acquisition_count == MAX_HISTORY_RECHECK_ACQUISITIONS:
        return HistoryRecheckTransition(_advance(state, now), "retry_exhausted")
    delay = HISTORY_RECHECK_BACKOFF_SECONDS * 2 ** (state.acquisition_count - 1) + jitter_seconds
    return HistoryRecheckTransition(
        replace(
            state,
            revision=state.revision + 1,
            lease=None,
            next_attempt_at=now + timedelta(seconds=delay),
        ),
        "deferred",
    )


def recover_exhausted_history_recheck(
    dataset: HistoryDataset, state: HistoryRecheckState, now: datetime
) -> HistoryRecheckTransition | None:
    now = utc_time(now)
    if (
        not _dataset_admits(dataset, state, now)
        or state.acquisition_count != MAX_HISTORY_RECHECK_ACQUISITIONS
        or state.lease is None
        or now < state.lease.expires_at
        or state.revision == MAX_SAFE_JSON_INTEGER
    ):
        return None
    return HistoryRecheckTransition(_advance(state, now), "retry_exhausted")


def _advance(state: HistoryRecheckState, now: datetime) -> HistoryRecheckState | None:
    if state.next_attempt == state.hint.cursor.latest_attempt:
        return None
    return replace(
        state,
        revision=state.revision + 1,
        next_attempt=state.next_attempt + 1,
        next_attempt_at=now,
        acquisition_count=0,
        lease=None,
    )


def _dataset_admits(dataset: HistoryDataset, state: HistoryRecheckState, now: datetime) -> bool:
    if type(dataset) is not HistoryDataset or type(state) is not HistoryRecheckState:
        raise TypeError("history recheck authority requires exact dataset and state")
    return (
        dataset.scope == state.hint.cursor.scope
        and dataset.generation == state.generation
        and dataset.state == "active"
        and dataset.configured_at <= now
        and state.hint.run_created_at <= now
        and dataset.configuration.selects(state.hint.workflow_id)
    )
