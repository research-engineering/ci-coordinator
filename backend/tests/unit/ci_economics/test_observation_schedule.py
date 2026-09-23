from dataclasses import replace
from datetime import timedelta

import pytest

from ci_coordinator.ci_economics.observation import ObservationConfiguration
from ci_coordinator.ci_economics.observation_scan import (
    ObservationLane,
    ObservationScanState,
    acquire_observation_claim,
)
from ci_coordinator.ci_economics.observation_schedule import (
    initial_observation_scans,
    observation_cycle_due_at,
    prepare_observation_cycle,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_economics.observation_factories import NOW, SCOPE, claimed_scan, observation


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("days", [0, 1, 6])
def test_initial_lanes_respect_pause_backfill_and_fractional_configuration_time(
    enabled: bool, days: int
) -> None:
    snapshot = replace(
        observation(),
        configuration=ObservationConfiguration(enabled, None, days),
        configured_at=NOW + timedelta(microseconds=1),
    )
    recent, backfill = initial_observation_scans(snapshot)
    assert (recent.lane, backfill.lane) == ("recent", "backfill")
    for state in (recent, backfill):
        assert state.scope == snapshot.scope and state.config_revision == snapshot.revision
        assert state.revision == 1 and state.lease is None
    if not enabled:
        assert recent.cursor is backfill.cursor is None
        assert recent.next_attempt_at == backfill.next_attempt_at == snapshot.configured_at
        return
    assert recent.cursor is not None
    assert recent.cursor.interval.created_from == recent.cursor.interval.created_through == NOW
    assert recent.cursor.cycle_started_at == snapshot.configured_at
    if days:
        assert backfill.cursor is not None
        assert backfill.cursor.interval.created_from == NOW - timedelta(days=days)
        assert backfill.cursor.interval.created_through == NOW
        assert backfill.next_attempt_at == snapshot.configured_at
    else:
        assert backfill.cursor is None
        assert backfill.next_attempt_at == snapshot.configured_at + timedelta(hours=6)


@pytest.mark.parametrize("lane", ["recent", "backfill"])
def test_completed_cycle_uses_overlap_or_full_eligible_rescan(lane: ObservationLane) -> None:
    snapshot = observation()
    now = NOW + timedelta(hours=8, microseconds=500_000)
    completed = NOW + timedelta(hours=6)
    state = ObservationScanState(SCOPE, snapshot.revision, lane, 4, None, NOW)
    outcome = prepare_observation_cycle(snapshot, state, last_completed_through=completed, now=now)
    assert outcome is not None and outcome.gap is None
    assert outcome.state.revision == 5 and outcome.state.lease is None
    cursor = outcome.state.cursor
    assert cursor is not None and cursor.cycle_started_at == now
    assert cursor.interval.created_through == now.replace(microsecond=0)
    assert cursor.interval.created_from == (
        completed - timedelta(minutes=5)
        if lane == "recent"
        else now.replace(microsecond=0) - timedelta(days=7)
    )
    assert observation_cycle_due_at(lane, now) == now + (
        timedelta(minutes=5) if lane == "recent" else timedelta(hours=6)
    )


@pytest.mark.parametrize("outage_days", [8, 365])
def test_outage_recovery_records_loss_before_replacing_cycle(outage_days: int) -> None:
    snapshot, state, old_claim = claimed_scan()
    now = NOW + timedelta(days=outage_days)
    outcome = prepare_observation_cycle(snapshot, state, last_completed_through=None, now=now)
    assert outcome is not None and outcome.gap is not None
    assert outcome.gap.created_from == old_claim.cursor.window.created_from
    assert outcome.gap.created_through == now - timedelta(days=7)
    assert outcome.gap.cycle_started_at == now
    assert outcome.gap.expires_at == now + timedelta(days=90)
    assert outcome.gap.selector_digest == snapshot.configuration.selector_digest
    assert outcome.gap.config_revision == state.config_revision
    assert outcome.state.revision == state.revision + 1
    assert outcome.state.lease is None
    assert outcome.state.cursor is not None
    assert outcome.state.cursor.interval.created_from == now - timedelta(days=7)
    assert outcome.state.cursor.interval.created_through == now
    assert outcome.state.cursor.page_number == 1
    assert outcome.state.cursor.cycle_started_at == now
    acquired = acquire_observation_claim(
        snapshot, outcome.state, now=now, worker_id="c" * 64, token="d" * 64
    )
    assert acquired is not None
    assert acquired[1].expected_revision == state.revision + 2


def test_expired_lease_resume_preserves_every_old_cursor_operand_until_acquisition() -> None:
    snapshot, state, _claim = claimed_scan()
    now = NOW + timedelta(seconds=60)
    outcome = prepare_observation_cycle(snapshot, state, last_completed_through=None, now=now)
    assert outcome is not None and outcome.state == state and outcome.gap is None
    acquired = acquire_observation_claim(
        snapshot, outcome.state, now=now, worker_id="c" * 64, token="d" * 64
    )
    assert acquired is not None and acquired[1].cursor == state.cursor
    assert acquired[0].revision == state.revision + 1


@pytest.mark.parametrize(
    "blocked", ["pause", "scope", "revision", "due", "lease", "clock", "completed", "overflow"]
)
def test_cycle_preparation_rejects_each_independent_authority_failure(blocked: str) -> None:
    snapshot, prior, _ = claimed_scan()
    state = replace(prior, lease=None)
    now = NOW + timedelta(seconds=10)
    completed = None
    if blocked == "pause":
        snapshot = replace(snapshot, configuration=replace(snapshot.configuration, enabled=False))
    elif blocked == "scope":
        state = replace(state, scope=RepositoryScope(101, 203))
    elif blocked == "revision":
        state = replace(state, config_revision=state.config_revision + 1)
    elif blocked == "due":
        state = replace(state, next_attempt_at=now + timedelta(seconds=1))
    elif blocked == "lease":
        state = prior
    elif blocked == "clock":
        now = NOW - timedelta(microseconds=1)
    elif blocked == "completed":
        completed = now + timedelta(seconds=1)
    else:
        state = replace(state, revision=MAX_SAFE_JSON_INTEGER - 2)
    assert (
        prepare_observation_cycle(snapshot, state, last_completed_through=completed, now=now)
        is None
    )


def test_zero_backfill_does_not_spin_before_six_hour_rescan() -> None:
    snapshot = replace(observation(), configuration=ObservationConfiguration(True, None, 0))
    _, state = initial_observation_scans(snapshot)
    for now in (NOW, NOW + timedelta(hours=6) - timedelta(microseconds=1)):
        assert (
            prepare_observation_cycle(snapshot, state, last_completed_through=None, now=now) is None
        )
    assert (
        prepare_observation_cycle(
            snapshot, state, last_completed_through=None, now=NOW + timedelta(hours=6)
        )
        is not None
    )


def test_recent_overlap_never_reopens_before_configuration_start() -> None:
    snapshot = observation()
    state = ObservationScanState(SCOPE, snapshot.revision, "recent", 3, None, NOW)
    outcome = prepare_observation_cycle(
        snapshot, state, last_completed_through=NOW, now=NOW + timedelta(minutes=5)
    )
    assert outcome is not None and outcome.state.cursor is not None
    assert outcome.state.cursor.interval.created_from == NOW
