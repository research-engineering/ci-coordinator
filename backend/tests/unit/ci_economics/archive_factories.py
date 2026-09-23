from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ci_coordinator.ci_economics.archive_detail import (
    ArchivedAttemptDetail,
    ArchivedJobDetail,
    ArchiveStepDetail,
)
from ci_coordinator.ci_economics.archive_statistics import (
    ArchivedAttemptStatistics,
    ArchivedJobStatistics,
    ArchivePopulation,
)
from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint
from ci_coordinator.ci_economics.history_configuration import (
    HistoryConfiguration,
    HistoryDataset,
    HistoryUsage,
)
from ci_coordinator.ci_economics.history_cursor import HistoryCursor
from ci_coordinator.ci_economics.history_scan import HistoryScanState, RecentHistoryProgress
from ci_coordinator.ci_economics.model import WorkflowConclusion
from ci_coordinator.ci_economics.report_payload import ReportAttemptPayload
from ci_coordinator.config_control import RepositoryScope

ARCHIVE_TIME = datetime(2020, 1, 1, tzinfo=UTC)


def archived_detail(
    statistics: ArchivedAttemptStatistics | None = None,
    *,
    conclusion: WorkflowConclusion = "success",
) -> ArchivedAttemptDetail:
    statistics = archived_statistics() if statistics is None else statistics
    return ArchivedAttemptDetail(
        schemaVersion="ci-economics-archive-detail/v1",
        attempt=statistics.attempt,
        jobs=tuple(
            ArchivedJobDetail(
                providerJobId=job.provider_job_id,
                steps=(
                    ArchiveStepDetail(
                        number=1,
                        status="completed",
                        conclusion=conclusion,
                        startedAt=statistics.run_created_at + timedelta(minutes=1),
                        completedAt=statistics.run_created_at + timedelta(minutes=2),
                    ),
                ),
            )
            for job in statistics.jobs
        ),
    )


def history_dataset() -> HistoryDataset:
    return HistoryDataset(
        RepositoryScope(101, 202),
        1,
        1,
        1,
        ARCHIVE_TIME,
        "active",
        HistoryConfiguration.model_validate(
            {
                "enabled": True,
                "workflowIds": None,
                "detailRetention": {
                    "mode": "days",
                    "days": 365,
                    "anchor": "first_successful_detail_import",
                },
                "quota": {"attempts": 1000, "jobs": 10000, "gaps": 100, "canonicalBytes": 10000000},
            }
        ),
        HistoryUsage.empty(),
    )


def history_scan(dataset: HistoryDataset | None = None, *, days: int = 365) -> HistoryScanState:
    dataset = history_dataset() if dataset is None else dataset
    return HistoryScanState(
        dataset.scope,
        dataset.generation,
        dataset.configuration_revision,
        1,
        HistoryCheckpoint(
            HistoryCursor.start(
                dataset.scope,
                ARCHIVE_TIME - timedelta(days=days),
                ARCHIVE_TIME,
                cycle_started_at=ARCHIVE_TIME,
            )
        ),
        ARCHIVE_TIME,
    )


def archived_job(job_id: int = 1) -> ArchivedJobStatistics:
    return ArchivedJobStatistics(
        providerJobId=job_id,
        name="Lint",
        conclusion="success",
        createdAt=ARCHIVE_TIME,
        startedAt=ARCHIVE_TIME.replace(minute=1),
        completedAt=ARCHIVE_TIME.replace(minute=2),
        labels=("linux",),
        runnerId=7,
        runnerGroupId=8,
    )


def history_discovery(dataset: HistoryDataset | None = None) -> HistoryScanState:
    return replace(history_scan(dataset, days=0), recent=RecentHistoryProgress(ARCHIVE_TIME))


def archived_statistics(
    *,
    jobs: tuple[ArchivedJobStatistics, ...] | None = None,
    population: ArchivePopulation = "complete",
    provider_total: int | None = 1,
) -> ArchivedAttemptStatistics:
    return ArchivedAttemptStatistics(
        schemaVersion="ci-economics-archive-statistics/v1",
        attempt=ReportAttemptPayload(
            installationId=101,
            repositoryId=202,
            workflowRunId=303,
            runAttempt=1,
            headSha="a" * 40,
        ),
        workflowId=404,
        workflowPath=".github/workflows/fullcheck.yml",
        workflowBlobSha=None,
        event="pull_request",
        conclusion="success",
        runCreatedAt=ARCHIVE_TIME,
        population=population,
        providerJobTotal=provider_total,
        jobs=(archived_job(),) if jobs is None else jobs,
    )
