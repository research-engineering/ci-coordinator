from ci_coordinator.ci_economics.history_gap import HistoryRecheckGap
from ci_coordinator.ci_economics.history_gap_recovery import RepairHistoryGaps
from ci_economics.archive_factories import ARCHIVE_TIME


def recheck_gap(run: int = 303, attempt: int = 1) -> HistoryRecheckGap:
    return HistoryRecheckGap(
        schemaVersion="ci-economics-history-recheck-gap/v1",
        installationId=101,
        repositoryId=202,
        generation=1,
        configurationRevision=1,
        workflowRunId=run,
        workflowId=404,
        runAttempt=attempt,
        runCreatedAt=ARCHIVE_TIME,
        reason="provider_not_found",
    )


def gap_command(*gaps: HistoryRecheckGap, operation: str = "retry-gaps") -> RepairHistoryGaps:
    selected = gaps or (recheck_gap(),)
    return RepairHistoryGaps(
        installationId=101,
        repositoryId=202,
        generation=1,
        expectedRevision=1,
        gapIds=tuple(sorted(gap.gap_id for gap in selected)),
        operationId=operation,
        actor="admin",
    )
