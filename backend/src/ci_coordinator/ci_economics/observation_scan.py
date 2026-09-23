from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Final, Literal

from ci_coordinator.ci_economics._observation_values import digest, positive_id, utc_time
from ci_coordinator.ci_economics.observation import ObservationSnapshot
from ci_coordinator.ci_economics.observation_windows import ObservationCursor
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

OBSERVATION_LEASE_SECONDS: Final = 60
type ObservationLane = Literal["recent", "backfill"]


def validate_observation_worker_id(worker_id: str) -> None:
    digest(worker_id)


@dataclass(frozen=True, slots=True)
class ObservationLease:
    worker_id: str
    token: str = field(repr=False)
    acquired_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        validate_observation_worker_id(self.worker_id)
        digest(self.token)
        object.__setattr__(self, "acquired_at", utc_time(self.acquired_at))
        object.__setattr__(self, "expires_at", utc_time(self.expires_at))
        if self.expires_at - self.acquired_at != timedelta(seconds=OBSERVATION_LEASE_SECONDS):
            raise ValueError("observation lease must have the admitted duration")


@dataclass(frozen=True, slots=True)
class ObservationScanState:
    scope: RepositoryScope
    config_revision: int
    lane: ObservationLane
    revision: int
    cursor: ObservationCursor | None
    next_attempt_at: datetime
    lease: ObservationLease | None = None

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("scan state requires exact repository scope")
        positive_id(self.config_revision, "scan configuration revision")
        positive_id(self.revision, "scan revision")
        if self.lane not in {"recent", "backfill"}:
            raise ValueError("unknown observation scan lane")
        if self.cursor is not None and type(self.cursor) is not ObservationCursor:
            raise TypeError("scan state requires exact cursor or no active cycle")
        object.__setattr__(self, "next_attempt_at", utc_time(self.next_attempt_at))
        if self.lease is not None:
            if type(self.lease) is not ObservationLease or self.cursor is None:
                raise TypeError("leased scan requires an exact lease and active cursor")
            if self.lease.acquired_at < self.cursor.cycle_started_at:
                raise ValueError("scan lease cannot precede its cycle")


@dataclass(frozen=True, slots=True)
class ObservationClaim:
    scope: RepositoryScope
    config_revision: int
    lane: ObservationLane
    expected_revision: int
    cursor: ObservationCursor
    lease: ObservationLease

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("scan claim requires exact repository scope")
        positive_id(self.config_revision, "claim configuration revision")
        positive_id(self.expected_revision, "claim revision")
        if self.lane not in {"recent", "backfill"}:
            raise ValueError("unknown observation claim lane")
        if type(self.cursor) is not ObservationCursor or type(self.lease) is not ObservationLease:
            raise TypeError("scan claim requires exact cursor and lease")
        if self.lease.acquired_at < self.cursor.cycle_started_at:
            raise ValueError("scan claim cannot precede its cycle")


def acquire_observation_claim(
    snapshot: ObservationSnapshot,
    state: ObservationScanState,
    *,
    now: datetime,
    worker_id: str,
    token: str,
) -> tuple[ObservationScanState, ObservationClaim] | None:
    _require_state(snapshot, state)
    now = utc_time(now)
    validate_observation_worker_id(worker_id)
    digest(token)
    if (
        not snapshot.configuration.enabled
        or snapshot.scope != state.scope
        or snapshot.revision != state.config_revision
        or state.cursor is None
        or now < snapshot.configured_at
        or now < state.cursor.cycle_started_at
        or now < state.next_attempt_at
        or (state.lease is not None and now < state.lease.expires_at)
        or state.revision >= MAX_SAFE_JSON_INTEGER - 1
    ):
        return None
    lease = ObservationLease(
        worker_id, token, now, now + timedelta(seconds=OBSERVATION_LEASE_SECONDS)
    )
    successor = replace(state, revision=state.revision + 1, lease=lease)
    claim = ObservationClaim(
        state.scope, state.config_revision, state.lane, successor.revision, state.cursor, lease
    )
    return successor, claim


def current_observation_claim(
    snapshot: ObservationSnapshot,
    state: ObservationScanState,
    claim: ObservationClaim,
    now: datetime,
) -> bool:
    _require_state(snapshot, state)
    if type(claim) is not ObservationClaim:
        raise TypeError("claim admission requires exact observation claim")
    now = utc_time(now)
    return (
        snapshot.configuration.enabled
        and snapshot.scope == state.scope == claim.scope
        and snapshot.revision == state.config_revision == claim.config_revision
        and state.lane == claim.lane
        and state.revision == claim.expected_revision
        and state.cursor == claim.cursor
        and state.lease == claim.lease
        and snapshot.configured_at <= now
        and claim.lease.acquired_at <= now < claim.lease.expires_at
    )


def finish_observation_claim(
    snapshot: ObservationSnapshot,
    state: ObservationScanState,
    claim: ObservationClaim,
    *,
    now: datetime,
    cursor: ObservationCursor | None,
    next_attempt_at: datetime,
) -> ObservationScanState | None:
    if not current_observation_claim(snapshot, state, claim, now):
        return None
    if state.revision == MAX_SAFE_JSON_INTEGER:
        return None
    next_attempt_at = utc_time(next_attempt_at)
    if next_attempt_at < utc_time(now):
        raise ValueError("scan completion cannot schedule a retry in the past")
    if cursor is not None and (
        type(cursor) is not ObservationCursor
        or cursor.interval != claim.cursor.interval
        or cursor.cycle_started_at != claim.cursor.cycle_started_at
    ):
        raise ValueError("holder completion cannot substitute a new scan cycle")
    return replace(
        state,
        revision=state.revision + 1,
        cursor=cursor,
        next_attempt_at=next_attempt_at,
        lease=None,
    )


def _require_state(snapshot: ObservationSnapshot, state: ObservationScanState) -> None:
    if type(snapshot) is not ObservationSnapshot or type(state) is not ObservationScanState:
        raise TypeError("scan transition requires exact observation snapshot and state")
