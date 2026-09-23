import json
from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, history_dataset

from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_rechecks import (
    HistoryRecheckHint,
    acquire_history_recheck,
    initial_history_recheck,
)
from ci_coordinator.kernel.canonical_json import canonical_json
from ci_coordinator.persistence.ci_history_recheck_codec import (
    decode_history_recheck,
    encode_history_recheck,
)


def _encoded(*, leased: bool = True) -> dict[str, object]:
    dataset = history_dataset()
    hint = HistoryRecheckHint(
        HistoryAttemptCursor(dataset.scope, 303, 4), 404, ARCHIVE_TIME, "recent"
    )
    state = initial_history_recheck(dataset, hint, ARCHIVE_TIME)
    assert state is not None
    if leased:
        claim = acquire_history_recheck(
            dataset, state, now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
        )
        assert claim is not None
        state = claim.state
    return encode_history_recheck(state)


@pytest.mark.parametrize("leased", [False, True])
def test_recheck_canonical_round_trip_preserves_exact_state_and_projection(leased: bool) -> None:
    row = _encoded(leased=leased)
    state = decode_history_recheck(row)
    assert encode_history_recheck(state) == row
    assert state.hint.cursor.next_attempt == state.next_attempt == 1
    assert state.hint.cursor.latest_attempt == 4
    assert state.acquisition_count == (1 if leased else 0)
    if leased:
        assert state.lease is not None
        assert state.lease.expires_at == ARCHIVE_TIME + timedelta(seconds=60)


@pytest.mark.parametrize(
    "column",
    [
        "installation_id",
        "repository_id",
        "generation",
        "workflow_run_id",
        "workflow_id",
        "source",
        "revision",
        "next_attempt_at",
        "acquisition_count",
        "lease_worker_id",
        "lease_token",
        "lease_acquired_at",
        "lease_expires_at",
    ],
)
def test_each_scalar_operand_is_compared_against_independent_canonical_authority(
    column: str,
) -> None:
    row = _encoded()
    value = row[column]
    if type(value) is int:
        row[column] = value + 1
    elif isinstance(value, str):
        row[column] = "repair" if column == "source" else "c" * 64
    else:
        row[column] = ARCHIVE_TIME + timedelta(seconds=5)
    with pytest.raises(ValueError, match="projection"):
        decode_history_recheck(row)


def test_storage_cannot_substitute_boolean_for_integer_projection() -> None:
    row = _encoded()
    row["acquisition_count"] = True
    with pytest.raises(ValueError, match="projection"):
        decode_history_recheck(row)


@pytest.mark.parametrize(
    "field,value",
    [("extra", 1), ("nextAttempt", 5), ("acquisitionCount", True), ("schemaVersion", "unknown")],
)
def test_recomputed_canonical_bytes_do_not_bypass_shape_and_relation_admission(
    field: str, value: object
) -> None:
    row = _encoded()
    raw = row["state_canonical"]
    assert isinstance(raw, bytes)
    payload = json.loads(raw)
    payload[field] = value
    row["state_canonical"] = canonical_json(payload)
    with pytest.raises(ValueError):
        decode_history_recheck(row)


def test_coherent_time_substitution_changes_state_and_cannot_equal_old_claim() -> None:
    state = decode_history_recheck(_encoded())
    assert state.lease is not None
    delta = timedelta(microseconds=1)
    changed = replace(
        state,
        lease=replace(
            state.lease,
            acquired_at=state.lease.acquired_at + delta,
            expires_at=state.lease.expires_at + delta,
        ),
    )
    assert decode_history_recheck(encode_history_recheck(changed)) == changed
    assert changed != state
