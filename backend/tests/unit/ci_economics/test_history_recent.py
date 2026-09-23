from dataclasses import replace
from datetime import timedelta

import pytest

from ci_coordinator.ci_economics.history_recent import (
    configure_recent_history,
    prepare_recent_history_cycle,
)
from ci_coordinator.ci_economics.history_scan import (
    RecentHistoryProgress,
    acquire_history_claim,
    finish_history_claim,
)

from .archive_factories import ARCHIVE_TIME, history_dataset, history_discovery, history_scan


def test_configuration_creates_and_rebinds_independent_frontiers_without_rewind() -> None:
    dataset, backfill = history_dataset(), history_scan()
    recent = configure_recent_history(dataset, backfill, prior=None)
    assert recent == history_discovery()
    assert recent.checkpoint.cursor.created_from == backfill.checkpoint.cursor.created_through
    successor_dataset = replace(dataset, configuration_revision=2)
    successor = configure_recent_history(
        successor_dataset, replace(backfill, configuration_revision=2), prior=recent
    )
    assert successor.configuration_revision == 2 and successor.revision == recent.revision + 1
    assert successor.checkpoint == recent.checkpoint and successor.recent == recent.recent
    with pytest.raises(ValueError, match="missing discovery"):
        configure_recent_history(
            successor_dataset, replace(backfill, configuration_revision=2), prior=None
        )
    with pytest.raises((TypeError, ValueError)):
        configure_recent_history(dataset, backfill, prior=backfill)


@pytest.mark.parametrize("delay_days", [0, 1, 30])
def test_completed_recent_frontier_catches_up_without_the_active_seven_day_clip(
    delay_days: int,
) -> None:
    original = history_discovery()
    acquired = acquire_history_claim(
        history_dataset(), original, now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    state, claim = acquired
    completed = finish_history_claim(
        history_dataset(),
        state,
        claim,
        now=ARCHIVE_TIME,
        checkpoint=replace(state.checkpoint, complete=True),
        next_attempt_at=ARCHIVE_TIME + timedelta(seconds=60),
        outcome="page_recorded",
        pages_completed=1,
    )
    assert completed is not None and completed.recent is not None
    assert completed.recent.completed_through == ARCHIVE_TIME
    assert prepare_recent_history_cycle(completed, ARCHIVE_TIME) is None
    now = ARCHIVE_TIME + timedelta(days=delay_days, seconds=60)
    prepared = prepare_recent_history_cycle(completed, now)
    assert prepared is not None
    assert prepared.checkpoint.cursor.created_from == ARCHIVE_TIME
    assert prepared.checkpoint.cursor.created_through == now
    assert not prepared.checkpoint.complete and prepared.pages_seen == 1
    assert prepared.revision == completed.revision
    assert prepared.recent == completed.recent
    assert prepared.checkpoint.cursor.window.created_through <= ARCHIVE_TIME + timedelta(days=7)


def test_progress_is_utc_second_precision_and_cannot_assert_an_unfinished_future() -> None:
    with pytest.raises(ValueError):
        RecentHistoryProgress(ARCHIVE_TIME, ARCHIVE_TIME - timedelta(seconds=1))
    with pytest.raises(ValueError):
        RecentHistoryProgress(ARCHIVE_TIME.replace(microsecond=1))
    with pytest.raises(ValueError):
        replace(
            history_discovery(),
            recent=RecentHistoryProgress(ARCHIVE_TIME, ARCHIVE_TIME + timedelta(seconds=1)),
        )
    with pytest.raises(ValueError):
        replace(history_discovery(), attempts_seen=1)
    with pytest.raises(ValueError):
        replace(
            history_discovery(), checkpoint=replace(history_discovery().checkpoint, complete=True)
        )
