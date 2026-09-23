from dataclasses import replace
from datetime import datetime

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint
from ci_coordinator.ci_economics.history_configuration import (
    HistoryConfiguration,
    HistoryDataset,
    HistoryUsage,
)
from ci_coordinator.ci_economics.history_cursor import HistoryCursor
from ci_coordinator.ci_economics.history_scan import HistoryScanState
from ci_coordinator.config_control import RepositoryScope


def configure_history_state(
    scope: RepositoryScope,
    configuration: HistoryConfiguration,
    *,
    prior: HistoryDataset | None,
    scan: HistoryScanState | None,
    now: datetime,
    initial_created_from: datetime | None = None,
    rescan: bool = False,
) -> tuple[HistoryDataset, HistoryScanState]:
    if type(scope) is not RepositoryScope or type(rescan) is not bool:
        raise TypeError("history configuration requires exact scope and rescan decision")
    configuration = HistoryConfiguration.model_validate(configuration)
    now = utc_time(now)
    if prior is None:
        if scan is not None or initial_created_from is None:
            raise ValueError(
                "initial history requires its population lower bound and no prior scan"
            )
        cursor = HistoryCursor.start(
            scope, initial_created_from, now.replace(microsecond=0), cycle_started_at=now
        )
        dataset = HistoryDataset(
            scope,
            1,
            1,
            1,
            now,
            "active" if configuration.enabled else "paused",
            configuration,
            HistoryUsage.empty(),
        )
        return dataset, HistoryScanState(scope, 1, 1, 1, HistoryCheckpoint(cursor), now)
    if type(prior) is not HistoryDataset or type(scan) is not HistoryScanState:
        raise TypeError("existing history requires its exact dataset and scan")
    if scan.lane != "backfill":
        raise ValueError("historical configuration requires its original backfill lane")
    if (prior.scope, prior.generation, prior.configuration_revision) != (
        scan.scope,
        scan.generation,
        scan.configuration_revision,
    ) or prior.scope != scope:
        raise ValueError("history configuration crosses its current dataset authority")
    if prior.state not in {"active", "paused"}:
        raise ValueError("ordinary configuration cannot reopen erased or erasing history")
    if initial_created_from is not None:
        raise ValueError("ordinary configuration cannot replace the original population bound")
    cursor = scan.checkpoint.cursor
    if now < max(prior.configured_at, cursor.cycle_started_at):
        raise ValueError("history configuration clock precedes current authority")
    successor = replace(
        prior,
        configuration_revision=prior.configuration_revision + 1,
        configuration=configuration,
        configured_at=now,
        state="active" if configuration.enabled else "paused",
    )
    if rescan or configuration.workflow_ids != prior.configuration.workflow_ids:
        cursor = HistoryCursor.start(
            scope, cursor.created_from, now.replace(microsecond=0), cycle_started_at=now
        )
        successor_scan = HistoryScanState(
            scope,
            successor.generation,
            successor.configuration_revision,
            scan.revision + 1,
            HistoryCheckpoint(cursor),
            now,
        )
    else:
        successor_scan = replace(
            scan,
            configuration_revision=successor.configuration_revision,
            revision=scan.revision + 1,
            next_attempt_at=now,
            lease=None,
        )
    return successor, successor_scan
