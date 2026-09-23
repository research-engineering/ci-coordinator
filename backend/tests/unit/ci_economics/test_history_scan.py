from dataclasses import replace
from datetime import timedelta

import pytest

from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration
from ci_coordinator.ci_economics.history_scan import (
    acquire_history_claim,
    current_history_claim,
    finish_history_claim,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .archive_factories import ARCHIVE_TIME, history_dataset, history_scan


def test_acquisition_completion_and_replay_bind_the_leased_revision() -> None:
    dataset = history_dataset()
    initial = history_scan(dataset, days=0)
    acquired = acquire_history_claim(
        dataset, initial, now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    leased, claim = acquired
    assert leased.revision == 2 and claim.state == leased
    assert not current_history_claim(dataset, initial, claim, ARCHIVE_TIME)
    assert current_history_claim(dataset, leased, claim, ARCHIVE_TIME)
    completed = finish_history_claim(
        dataset,
        leased,
        claim,
        now=ARCHIVE_TIME,
        checkpoint=replace(leased.checkpoint, complete=True),
        next_attempt_at=ARCHIVE_TIME,
        outcome="page_recorded",
        pages_completed=1,
    )
    assert completed is not None and completed.revision == 3
    assert completed.lease is None and completed.pages_seen == 1
    assert not current_history_claim(dataset, completed, claim, ARCHIVE_TIME)
    assert (
        acquire_history_claim(
            dataset, completed, now=ARCHIVE_TIME, worker_id="a" * 64, token="c" * 64
        )
        is None
    )


@pytest.mark.parametrize(
    "offset,valid,reclaim", [(-1, True, False), (0, False, True), (1, False, True)]
)
def test_expiry_boundary_partitions_holder_and_reclaimer_authority(
    offset: int, valid: bool, reclaim: bool
) -> None:
    dataset = history_dataset()
    acquired = acquire_history_claim(
        dataset, history_scan(), now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    leased, claim = acquired
    assert leased.lease is not None
    now = leased.lease.expires_at + timedelta(microseconds=offset)
    assert current_history_claim(dataset, leased, claim, now) is valid
    successor = acquire_history_claim(dataset, leased, now=now, worker_id="c" * 64, token="d" * 64)
    assert (successor is not None) is reclaim
    if successor is not None:
        replacement, fresh = successor
        assert replacement.revision == leased.revision + 1
        assert not current_history_claim(dataset, replacement, claim, now)
        assert current_history_claim(dataset, replacement, fresh, now)


@pytest.mark.parametrize(
    "field", ["scope", "generation", "configuration_revision", "configured_at", "state"]
)
def test_each_dataset_authority_operand_independently_invalidates_a_claim(field: str) -> None:
    dataset = history_dataset()
    acquired = acquire_history_claim(
        dataset, history_scan(), now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    leased, claim = acquired
    if field == "scope":
        changed = replace(dataset, scope=RepositoryScope(102, 202))
    elif field == "generation":
        changed = replace(dataset, generation=2)
    elif field == "configuration_revision":
        changed = replace(dataset, configuration_revision=2)
    elif field == "configured_at":
        changed = replace(dataset, configured_at=ARCHIVE_TIME + timedelta(seconds=1))
    else:
        changed = replace(
            dataset,
            state="paused",
            configuration=HistoryConfiguration.model_validate(
                {**dataset.configuration.model_dump(), "enabled": False}
            ),
        )
    assert not current_history_claim(changed, leased, claim, ARCHIVE_TIME)


@pytest.mark.parametrize("field", ["worker_id", "token", "acquired_at", "revision", "checkpoint"])
def test_each_lease_and_claim_identity_operand_is_compared(field: str) -> None:
    dataset = history_dataset()
    acquired = acquire_history_claim(
        dataset, history_scan(), now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    leased, claim = acquired
    assert leased.lease is not None
    if field == "revision":
        changed = replace(leased, revision=leased.revision + 1)
    elif field == "checkpoint":
        assert leased.checkpoint.cursor is not None
        changed = replace(
            leased, checkpoint=HistoryCheckpoint(replace(leased.checkpoint.cursor, page_number=2))
        )
    elif field == "acquired_at":
        delta = timedelta(microseconds=1)
        changed = replace(
            leased,
            lease=replace(
                leased.lease,
                acquired_at=leased.lease.acquired_at + delta,
                expires_at=leased.lease.expires_at + delta,
            ),
        )
    elif field == "worker_id":
        changed = replace(leased, lease=replace(leased.lease, worker_id="c" * 64))
    else:
        changed = replace(leased, lease=replace(leased.lease, token="c" * 64))
    assert not current_history_claim(dataset, changed, claim, ARCHIVE_TIME + timedelta(seconds=1))


def test_data_changes_do_not_cancel_an_otherwise_current_scan() -> None:
    dataset = history_dataset()
    acquired = acquire_history_claim(
        dataset, history_scan(), now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    leased, claim = acquired
    assert current_history_claim(replace(dataset, data_revision=2), leased, claim, ARCHIVE_TIME)


@pytest.mark.parametrize("revision", [MAX_SAFE_JSON_INTEGER - 1, MAX_SAFE_JSON_INTEGER])
def test_acquisition_reserves_a_safe_terminal_revision(revision: int) -> None:
    assert (
        acquire_history_claim(
            history_dataset(),
            replace(history_scan(), revision=revision),
            now=ARCHIVE_TIME,
            worker_id="a" * 64,
            token="b" * 64,
        )
        is None
    )


def test_retry_and_completion_cannot_replace_the_frozen_scan_population() -> None:
    dataset = history_dataset()
    acquired = acquire_history_claim(
        dataset, history_scan(), now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    leased, claim = acquired
    assert leased.checkpoint.cursor is not None
    with pytest.raises(ValueError):
        finish_history_claim(
            dataset,
            leased,
            claim,
            now=ARCHIVE_TIME,
            checkpoint=HistoryCheckpoint(
                replace(
                    leased.checkpoint.cursor, cycle_started_at=ARCHIVE_TIME + timedelta(seconds=1)
                )
            ),
            next_attempt_at=ARCHIVE_TIME,
            outcome="page_recorded",
        )
    with pytest.raises(ValueError):
        finish_history_claim(
            dataset,
            leased,
            claim,
            now=ARCHIVE_TIME,
            checkpoint=leased.checkpoint,
            next_attempt_at=ARCHIVE_TIME - timedelta(seconds=1),
            outcome="provider_unavailable",
        )
