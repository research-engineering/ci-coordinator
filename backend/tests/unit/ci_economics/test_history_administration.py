from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import cast

import pytest

from ci_coordinator.ci_economics.archive_retention import DetailRetentionPolicy
from ci_coordinator.ci_economics.history_administration import HistoryStatus
from ci_coordinator.ci_economics.history_configuration import HistoryDefaults
from ci_coordinator.config_control import RepositoryScope
from ci_economics.archive_factories import (
    ARCHIVE_TIME,
    history_dataset,
    history_discovery,
    history_scan,
)

DATASET = history_dataset()
SCAN = history_scan(DATASET)
DEFAULTS = HistoryDefaults(1, DetailRetentionPolicy.default(), ARCHIVE_TIME)
STATUS = HistoryStatus(
    DATASET.scope, DEFAULTS, DATASET, SCAN, 0, ARCHIVE_TIME, history_discovery(DATASET)
)
FOREIGN_DATASET = replace(DATASET, scope=RepositoryScope(101, 203))


@pytest.mark.parametrize("count", [0, 1, 256])
def test_status_preserves_bounded_progress_and_future_retry_without_granting_authority(
    count: int,
) -> None:
    scan = replace(SCAN, next_attempt_at=ARCHIVE_TIME + timedelta(days=1))
    result = replace(STATUS, pending_rechecks=count, scan=scan)
    assert result.dataset == DATASET and result.scan == scan
    assert result.pending_rechecks == count and result.observed_at == ARCHIVE_TIME


def test_unconfigured_scope_has_defaults_and_zero_work_without_a_fake_dataset() -> None:
    result = replace(STATUS, dataset=None, scan=None, discovery=None)
    assert result.defaults == DEFAULTS and result.pending_rechecks == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"scope": (101, 202)},
        {"defaults": None},
        {"dataset": None},
        {"scan": None},
        {"discovery": None},
        {"discovery": SCAN},
        {"discovery": history_discovery(FOREIGN_DATASET)},
        {"discovery": replace(history_discovery(), generation=2)},
        {"discovery": replace(history_discovery(), configuration_revision=2)},
        {"dataset": {}},
        {"scan": {}},
        {"pending_rechecks": True},
        {"pending_rechecks": -1},
        {"pending_rechecks": 257},
        {"pending_rechecks": 1.0},
        {"dataset": None, "scan": None, "pending_rechecks": 1},
        {"dataset": FOREIGN_DATASET},
        {"scan": history_scan(FOREIGN_DATASET)},
        {"dataset": replace(DATASET, generation=2)},
        {"scan": replace(SCAN, generation=2)},
        {"dataset": replace(DATASET, configuration_revision=2)},
        {"scan": replace(SCAN, configuration_revision=2)},
        {"defaults": replace(DEFAULTS, updated_at=ARCHIVE_TIME + timedelta(microseconds=1))},
        {"dataset": replace(DATASET, configured_at=ARCHIVE_TIME + timedelta(microseconds=1))},
        {
            "scan": replace(
                SCAN,
                checkpoint=replace(
                    SCAN.checkpoint,
                    cursor=replace(
                        SCAN.checkpoint.cursor,
                        cycle_started_at=ARCHIVE_TIME + timedelta(microseconds=1),
                    ),
                ),
            )
        },
        {"observed_at": ARCHIVE_TIME.replace(tzinfo=None)},
    ],
)
def test_each_status_relation_rejects_its_isolated_counterexample(patch: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        cast(Callable[..., HistoryStatus], replace)(STATUS, **patch)
