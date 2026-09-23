from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from ci_coordinator.ci_economics.discovery import RunDiscoveryWindow
from ci_coordinator.ci_economics.observation_scan import (
    ObservationClaim,
    ObservationLease,
    acquire_observation_claim,
    current_observation_claim,
    finish_observation_claim,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .observation_factories import NOW, claimed_scan


@pytest.mark.parametrize(
    "offset,allowed",
    [(-1, False), (0, True), (59_999_999, True), (60_000_000, False), (60_000_001, False)],
)
def test_holder_authority_uses_exact_hard_lease_interval(offset: int, allowed: bool) -> None:
    snapshot, state, claim = claimed_scan()
    assert (
        current_observation_claim(snapshot, state, claim, NOW + timedelta(microseconds=offset))
        is allowed
    )


@pytest.mark.parametrize(
    "operand",
    [
        "installation",
        "repository",
        "configuration",
        "lane",
        "revision",
        "worker",
        "token",
        "lease_times",
        "interval",
        "window",
        "page",
        "cycle",
    ],
)
def test_each_claim_operand_is_independently_bound(operand: str) -> None:
    snapshot, state, claim = claimed_scan()
    lease = replace(
        claim.lease,
        acquired_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=61),
    )
    state, claim = replace(state, lease=lease), replace(claim, lease=lease)
    mutations = {
        "installation": replace(claim, scope=RepositoryScope(102, 202)),
        "repository": replace(claim, scope=RepositoryScope(101, 203)),
        "configuration": replace(claim, config_revision=4),
        "lane": replace(claim, lane="recent"),
        "revision": replace(claim, expected_revision=8),
        "worker": replace(claim, lease=replace(claim.lease, worker_id="c" * 64)),
        "token": replace(claim, lease=replace(claim.lease, token="c" * 64)),
        "lease_times": replace(
            claim,
            lease=replace(
                claim.lease,
                acquired_at=NOW + timedelta(seconds=2),
                expires_at=NOW + timedelta(seconds=62),
            ),
        ),
        "interval": replace(
            claim,
            cursor=replace(
                claim.cursor, interval=RunDiscoveryWindow(NOW - timedelta(hours=9), NOW)
            ),
        ),
        "window": replace(
            claim,
            cursor=replace(
                claim.cursor,
                window=RunDiscoveryWindow(NOW - timedelta(hours=8), NOW - timedelta(hours=3)),
            ),
        ),
        "page": replace(claim, cursor=replace(claim.cursor, page_number=2)),
        "cycle": replace(
            claim,
            cursor=replace(claim.cursor, cycle_started_at=NOW + timedelta(microseconds=1)),
        ),
    }
    assert current_observation_claim(snapshot, state, claim, NOW + timedelta(seconds=2))
    assert not current_observation_claim(
        snapshot, state, mutations[operand], NOW + timedelta(seconds=2)
    )


@pytest.mark.parametrize(
    "operand",
    [
        "disabled",
        "revision",
        "installation",
        "repository",
        "future_configuration",
        "state_revision",
        "state_scope",
        "state_configuration",
        "state_lane",
        "no_lease",
    ],
)
def test_current_owner_state_not_only_claim_controls_admission(operand: str) -> None:
    snapshot, state, claim = claimed_scan()
    if operand == "disabled":
        snapshot = replace(snapshot, configuration=replace(snapshot.configuration, enabled=False))
    elif operand == "revision":
        snapshot = replace(snapshot, revision=4)
    elif operand == "installation":
        snapshot = replace(snapshot, scope=RepositoryScope(102, 202))
    elif operand == "repository":
        snapshot = replace(snapshot, scope=RepositoryScope(101, 203))
    elif operand == "future_configuration":
        snapshot = replace(snapshot, configured_at=NOW + timedelta(seconds=1))
    elif operand == "state_revision":
        state = replace(state, revision=8)
    elif operand == "state_scope":
        state = replace(state, scope=RepositoryScope(102, 202))
    elif operand == "state_configuration":
        state = replace(state, config_revision=4)
    elif operand == "state_lane":
        state = replace(state, lane="recent")
    else:
        state = replace(state, lease=None)
    assert not current_observation_claim(snapshot, state, claim, NOW)
    assert (
        finish_observation_claim(
            snapshot, state, claim, now=NOW, cursor=claim.cursor, next_attempt_at=NOW
        )
        is None
    )


@pytest.mark.parametrize("offset,acquired", [(59, False), (60, True), (61, True)])
def test_reclaim_and_holder_have_disjoint_time_authority(offset: int, acquired: bool) -> None:
    snapshot, state, old = claimed_scan()
    now = NOW + timedelta(seconds=offset)
    result = acquire_observation_claim(snapshot, state, now=now, worker_id="c" * 64, token="d" * 64)
    assert (result is not None) is acquired
    if result is not None:
        successor, claim = result
        assert successor.revision == claim.expected_revision == 8
        assert current_observation_claim(snapshot, successor, claim, now)
        assert not current_observation_claim(snapshot, successor, old, now)


def test_acquisition_revision_is_the_completion_prior_revision() -> None:
    snapshot, state, old = claimed_scan()
    ready = replace(state, lease=None)
    acquired = acquire_observation_claim(
        snapshot, ready, now=NOW, worker_id="c" * 64, token="d" * 64
    )
    assert acquired is not None
    leased, claim = acquired
    assert claim.expected_revision == ready.revision + 1
    completed = finish_observation_claim(
        snapshot,
        leased,
        claim,
        now=NOW,
        cursor=claim.cursor.advance_window(),
        next_attempt_at=NOW + timedelta(seconds=1),
    )
    assert completed is not None
    assert completed.revision == 9 and completed.lease is None
    assert not current_observation_claim(snapshot, completed, claim, NOW)
    assert not current_observation_claim(snapshot, completed, old, NOW)


@pytest.mark.parametrize(
    "operand",
    ["disabled", "wrong_scope", "wrong_revision", "no_cursor", "not_due", "exhausted_revision"],
)
def test_ineligible_scan_does_not_acquire(operand: str) -> None:
    snapshot, state, _ = claimed_scan()
    state = replace(state, lease=None)
    if operand == "disabled":
        snapshot = replace(snapshot, configuration=replace(snapshot.configuration, enabled=False))
    elif operand == "wrong_scope":
        state = replace(state, scope=RepositoryScope(101, 203))
    elif operand == "wrong_revision":
        state = replace(state, config_revision=4)
    elif operand == "no_cursor":
        state = replace(state, cursor=None)
    elif operand == "not_due":
        state = replace(state, next_attempt_at=NOW + timedelta(seconds=1))
    else:
        state = replace(state, revision=MAX_SAFE_JSON_INTEGER)
    assert (
        acquire_observation_claim(snapshot, state, now=NOW, worker_id="c" * 64, token="d" * 64)
        is None
    )


def test_failure_retry_can_keep_cursor_but_cannot_replace_cycle() -> None:
    snapshot, state, claim = claimed_scan()
    retried = finish_observation_claim(
        snapshot,
        state,
        claim,
        now=NOW,
        cursor=claim.cursor,
        next_attempt_at=NOW + timedelta(seconds=30),
    )
    assert retried is not None and retried.cursor == claim.cursor and retried.lease is None
    with pytest.raises(ValueError, match="new scan cycle"):
        finish_observation_claim(
            snapshot,
            state,
            claim,
            now=NOW,
            cursor=replace(claim.cursor, cycle_started_at=NOW + timedelta(seconds=1)),
            next_attempt_at=NOW,
        )
    with pytest.raises(ValueError, match="past"):
        finish_observation_claim(
            snapshot,
            state,
            claim,
            now=NOW,
            cursor=claim.cursor,
            next_attempt_at=NOW - timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_id", "x" * 64),
        ("token", "a" * 63),
        ("token", "A" * 64),
        ("token", None),
        ("expires_at", NOW + timedelta(seconds=61)),
        ("acquired_at", NOW.replace(tzinfo=None)),
    ],
)
def test_lease_identity_and_duration_reject_invalid_fields(field: str, value: object) -> None:
    lease = claimed_scan()[2].lease
    with pytest.raises(ValueError):
        ObservationLease(
            cast(str, value) if field == "worker_id" else lease.worker_id,
            cast(str, value) if field == "token" else lease.token,
            cast(datetime, value) if field == "acquired_at" else lease.acquired_at,
            cast(datetime, value) if field == "expires_at" else lease.expires_at,
        )


def test_secret_claim_token_is_not_in_repr() -> None:
    _, state, claim = claimed_scan()
    assert "b" * 64 not in repr(state)
    assert "b" * 64 not in repr(claim)


@pytest.mark.parametrize("value", [None, {}, "claim"])
def test_admission_requires_typed_claim(value: object) -> None:
    snapshot, state, _ = claimed_scan()
    with pytest.raises(TypeError):
        current_observation_claim(snapshot, state, cast(ObservationClaim, value), NOW)
