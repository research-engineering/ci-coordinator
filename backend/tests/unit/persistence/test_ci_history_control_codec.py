from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.archive_factories import (
    ARCHIVE_TIME,
    history_dataset,
    history_discovery,
    history_scan,
)

from ci_coordinator.ci_economics.history_scan import acquire_history_claim
from ci_coordinator.persistence.ci_history_control_codec import (
    decode_history_dataset,
    decode_history_scan,
    encode_history_dataset,
    encode_history_scan,
)


def test_dataset_canonical_configuration_and_typed_columns_roundtrip() -> None:
    dataset = history_dataset()
    row = encode_history_dataset(dataset)
    assert decode_history_dataset(row) == dataset
    assert isinstance(row["configuration_canonical"], bytes)
    row["configuration_canonical"] = memoryview(row["configuration_canonical"])
    assert decode_history_dataset(row) == dataset


@pytest.mark.parametrize(
    "field,value",
    [
        ("installation_id", True),
        ("generation", "1"),
        ("configuration_revision", 0),
        ("data_revision", -1),
        ("configured_at", ARCHIVE_TIME.replace(tzinfo=None)),
        ("state", "erased"),
        ("attempt_count", -1),
        ("job_count", True),
        ("gap_count", "0"),
        ("canonical_bytes", -1),
        ("configuration_canonical", b"{}"),
        ("configuration_canonical", b"{} "),
    ],
)
def test_dataset_rejects_invalid_independent_storage_operands(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        decode_history_dataset({**encode_history_dataset(history_dataset()), field: value})


def _leased_row() -> dict[str, object]:
    dataset = history_dataset()
    acquired = acquire_history_claim(
        dataset, history_scan(), now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    return encode_history_scan(acquired[0])


def test_lease_roundtrip_keeps_exact_canonical_and_query_projections() -> None:
    row = _leased_row()
    state = decode_history_scan(row)
    assert encode_history_scan(state) == row
    assert isinstance(row["state_canonical"], bytes)
    row["state_canonical"] = memoryview(row["state_canonical"])
    assert decode_history_scan(row) == state
    unleased = replace(state, lease=None)
    assert decode_history_scan(encode_history_scan(unleased)) == unleased


@pytest.mark.parametrize(
    "field,value",
    [
        ("installation_id", 102),
        ("repository_id", 203),
        ("lane", "discovery"),
        ("generation", 2),
        ("configuration_revision", 2),
        ("revision", 3),
        ("traversal_complete", True),
        ("traversal_complete", 0),
        ("next_attempt_at", ARCHIVE_TIME + timedelta(seconds=1)),
        ("lease_worker_id", "c" * 64),
        ("lease_token", "c" * 64),
        ("lease_acquired_at", ARCHIVE_TIME + timedelta(seconds=1)),
        ("lease_expires_at", ARCHIVE_TIME + timedelta(seconds=59)),
        ("state_canonical", b"{}"),
        ("state_canonical", b"{"),
    ],
)
def test_each_scan_projection_must_match_its_canonical_state(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        decode_history_scan({**_leased_row(), field: value})


def test_discovery_progress_has_an_exact_separate_lane() -> None:
    state = history_discovery()
    encoded = encode_history_scan(state)
    assert encoded["lane"] == "discovery"
    assert decode_history_scan(encoded) == state
    with pytest.raises(ValueError):
        decode_history_scan({**encoded, "lane": "backfill"})


@pytest.mark.parametrize(
    "field", ["generation", "configuration_revision", "revision", "installation_id"]
)
def test_equal_boolean_and_integer_values_are_not_equal_storage_identity(field: str) -> None:
    with pytest.raises(ValueError):
        decode_history_scan({**_leased_row(), field: True})


def test_completed_scan_retains_its_frozen_population_in_canonical_and_query_state() -> None:
    state = history_scan(days=0)
    completed = replace(state, checkpoint=replace(state.checkpoint, complete=True))
    row = encode_history_scan(completed)
    assert row["traversal_complete"] is True
    restored = decode_history_scan(row)
    assert restored == completed
    assert restored.checkpoint.cursor == state.checkpoint.cursor
