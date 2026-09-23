from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from ci_coordinator.ci_economics.observation_gaps import (
    InvalidObservationGapCursor,
    ObservationGap,
    ObservationGapCursor,
    ObservationGapReason,
    gap_detail_truncation_deadline,
)
from ci_coordinator.config_control import RepositoryScope

from .observation_factories import NOW, SCOPE, observation


@pytest.mark.parametrize("revision", [1, 9007199254740991])
def test_gap_cursor_binds_scope_revision_and_anchor(revision: int) -> None:
    raw = f"101.202.{revision}." + "a" * 64
    cursor = ObservationGapCursor.parse(raw)
    assert cursor == ObservationGapCursor(SCOPE, revision, "a" * 64)
    assert cursor.value == raw


@pytest.mark.parametrize(
    "raw",
    [
        "a" * 64,
        "101.202.0." + "a" * 64,
        "0101.202.1." + "a" * 64,
        "101.202.1." + "A" * 64,
        "101.202.1." + "a" * 63,
        "101.202.1." + "a" * 64 + "\n",
        *[
            f"{scope}." + "a" * 64
            for scope in (
                "9007199254740992.202.1",
                "101.9007199254740992.1",
                "101.202.9007199254740992",
            )
        ],
    ],
)
def test_gap_cursor_rejects_noncanonical_or_unsafe_coordinates(raw: str) -> None:
    with pytest.raises(InvalidObservationGapCursor):
        ObservationGapCursor.parse(raw)


def _gap() -> ObservationGap:
    return ObservationGap(
        SCOPE,
        3,
        observation().configuration.selector_digest,
        "recent",
        NOW,
        NOW - timedelta(seconds=1),
        NOW,
        "provider_truncated",
    )


@pytest.mark.parametrize(
    "operand",
    ["installation", "repository", "revision", "selector", "lane", "cycle", "interval", "reason"],
)
def test_gap_identity_preserves_every_provenance_dimension(operand: str) -> None:
    gap = _gap()
    mutations = {
        "installation": replace(gap, scope=RepositoryScope(102, 202)),
        "repository": replace(gap, scope=RepositoryScope(101, 203)),
        "revision": replace(gap, config_revision=4),
        "selector": replace(gap, selector_digest="a" * 64),
        "lane": replace(gap, lane="backfill"),
        "cycle": replace(gap, cycle_started_at=NOW + timedelta(seconds=1)),
        "interval": replace(gap, created_from=NOW - timedelta(seconds=2)),
        "reason": replace(gap, reason="outside_source_window"),
    }
    assert gap.gap_id != mutations[operand].gap_id
    assert replace(gap).gap_id == gap.gap_id


def test_retries_and_reinsertion_cannot_renew_gap_retention() -> None:
    gap = _gap()
    expiry = NOW + timedelta(days=90)
    for days in (0, 1, 30, 89):
        assert (
            gap_detail_truncation_deadline(None, replace(gap), NOW + timedelta(days=days)) == expiry
        )
    assert gap_detail_truncation_deadline(expiry, gap, expiry) is None
    assert gap_detail_truncation_deadline(None, gap, expiry + timedelta(microseconds=1)) is None


def test_newer_eviction_preserves_warning_without_rewriting_older_expiry() -> None:
    older = _gap()
    newer = replace(older, cycle_started_at=NOW + timedelta(days=1))
    old_expiry = NOW + timedelta(days=90)
    new_expiry = NOW + timedelta(days=91)
    assert gap_detail_truncation_deadline(old_expiry, newer, NOW + timedelta(days=1)) == new_expiry
    assert gap_detail_truncation_deadline(new_expiry, older, NOW + timedelta(days=2)) == new_expiry
    assert older.expires_at == old_expiry


@pytest.mark.parametrize(
    "reason",
    ["provider_truncated", "outside_source_window", "source_conflict", "outage_window_lost"],
)
def test_gap_reason_is_preserved_without_claiming_a_missing_source_count(reason: str) -> None:
    gap = replace(_gap(), reason=cast(ObservationGapReason, reason))
    assert gap.canonical_mapping()["reason"] == reason
    assert "missingCount" not in gap.canonical_mapping()


def test_long_outage_gap_is_not_limited_by_provider_query_window() -> None:
    gap = replace(_gap(), created_from=NOW - timedelta(days=365), reason="outage_window_lost")
    assert gap.created_through - gap.created_from == timedelta(days=365)
    assert gap.expires_at == NOW + timedelta(days=90)


@pytest.mark.parametrize(
    "start,end",
    [
        (NOW + timedelta(seconds=1), NOW),
        (NOW, NOW + timedelta(seconds=1)),
        (NOW.replace(tzinfo=None), NOW),
        (NOW, NOW.replace(tzinfo=None)),
    ],
)
def test_gap_times_must_be_aware_ordered_and_observed(start: object, end: object) -> None:
    with pytest.raises(ValueError):
        replace(_gap(), created_from=cast(datetime, start), created_through=cast(datetime, end))
