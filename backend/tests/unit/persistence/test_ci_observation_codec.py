from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.observation_factories import NOW, SCOPE, claimed_scan, observation

from ci_coordinator.ci_economics.observation_gaps import ObservationGap
from ci_coordinator.ci_economics.observation_progress import ObservationScanProgress
from ci_coordinator.persistence.ci_observation_codec import (
    decode_gap,
    decode_scan,
    decode_subscription,
    encode_observation_payload,
    encode_scan,
)


def _subscription() -> dict[str, object]:
    snapshot = observation()
    return {
        "installation_id": SCOPE.installation_id,
        "repository_id": SCOPE.repository_id,
        "revision": snapshot.revision,
        "enabled": True,
        "snapshot_digest": snapshot.snapshot_digest,
        "snapshot_canonical": encode_observation_payload(snapshot.canonical_mapping()),
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("installation_id", 102),
        ("repository_id", 203),
        ("revision", 4),
        ("enabled", False),
        ("snapshot_digest", "f" * 64),
    ],
)
def test_subscription_checks_each_indexed_projection(key: str, value: object) -> None:
    row = _subscription()
    assert decode_subscription(row) == observation()
    row[key] = value
    with pytest.raises(ValueError):
        decode_subscription(row)


@pytest.mark.parametrize("leased", [False, True])
def test_scan_roundtrip_preserves_full_fencing_identity_and_counters(leased: bool) -> None:
    _, state, _ = claimed_scan()
    if not leased:
        state = replace(state, lease=None)
    progress = ObservationScanProgress(state, NOW, NOW, 1, 100, "page_recorded")
    row = encode_scan(progress)
    assert decode_scan(row) == progress
    assert row["lease_expires_at"] == (state.lease.expires_at if state.lease else None)
    assert row["config_revision"] == 3 and row["revision"] == 7
    assert row["pages_seen"] == 1 and row["sources_registered"] == 100
    assert "b" * 64 not in repr(progress)


@pytest.mark.parametrize(
    "key,value",
    [
        ("installation_id", 102),
        ("repository_id", 203),
        ("config_revision", 4),
        ("revision", 8),
        ("lane", "recent"),
        ("next_attempt_at", NOW + timedelta(seconds=1)),
        ("lease_expires_at", NOW + timedelta(seconds=59)),
        ("pages_seen", True),
        ("sources_registered", -1),
        ("last_outcome", "unknown"),
        ("last_page_at", NOW),
        ("last_completed_through", NOW),
    ],
)
def test_scan_rejects_each_contradictory_row_operand(key: str, value: object) -> None:
    _, state, _ = claimed_scan()
    row = encode_scan(ObservationScanProgress(state))
    row[key] = value
    with pytest.raises(ValueError):
        decode_scan(row)


@pytest.mark.parametrize(
    "payload", [b"{}", b'{"revision":1,"revision":2}', b" " * 2049, b"[]", b"null", b"not-json"]
)
def test_storage_payload_rejects_ambiguous_missing_or_unbounded_bytes(payload: bytes) -> None:
    row = _subscription()
    row["snapshot_canonical"] = payload
    with pytest.raises(ValueError):
        decode_subscription(row)


@pytest.mark.parametrize(
    "key,value",
    [
        ("installation_id", 102),
        ("repository_id", 203),
        ("gap_id", "e" * 64),
        ("expires_at", NOW + timedelta(days=91)),
    ],
)
def test_gap_canonical_identity_and_retention_match_projections(key: str, value: object) -> None:
    gap = ObservationGap(
        SCOPE,
        3,
        "f" * 64,
        "recent",
        NOW,
        NOW - timedelta(days=20),
        NOW - timedelta(days=7),
        "outage_window_lost",
    )
    row: dict[str, object] = {
        "installation_id": SCOPE.installation_id,
        "repository_id": SCOPE.repository_id,
        "gap_id": gap.gap_id,
        "gap_canonical": encode_observation_payload(gap.canonical_mapping()),
        "expires_at": gap.expires_at,
    }
    assert decode_gap(row) == gap
    row[key] = value
    with pytest.raises(ValueError):
        decode_gap(row)
