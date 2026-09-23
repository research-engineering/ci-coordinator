import type {
  ArchiveDetailRead,
  ArchiveQuery,
  ArchiveRead,
} from "../src/api/ciEconomics/archiveReadSchema";
import type {
  ArchiveAttemptDetail,
  ArchiveJob,
  ArchiveRecord,
} from "../src/api/ciEconomics/archiveRecordSchema";
import type { RetentionPreview } from "../src/api/ciEconomics/archiveRetentionSchema";

export const ARCHIVE_TIME = "2026-09-12T10:00:00Z";
export function archiveQuery(kind: ArchiveQuery["kind"] = "records"): ArchiveQuery {
  return {
    installationId: 1,
    repositoryId: 2,
    generation: 3,
    kind,
    limit: 50,
    createdFrom: null,
    createdThrough: null,
    workflowId: null,
    jobName: null,
    workflowRunId: kind === "jobs" || kind === "detail" ? 101 : null,
    runAttempt: kind === "jobs" || kind === "detail" ? 1 : null,
  };
}
export function archiveRecord(): ArchiveRecord {
  return {
    header: {
      schemaVersion: "ci-economics-archive-statistics/v1",
      attempt: {
        installationId: 1,
        repositoryId: 2,
        workflowRunId: 101,
        runAttempt: 1,
        headSha: "a".repeat(40),
      },
      workflowId: 17,
      workflowPath: ".github/workflows/fullcheck.yml",
      workflowBlobSha: null,
      event: "push",
      conclusion: "success",
      runCreatedAt: "2026-09-10T09:00:00Z",
      population: "complete",
      providerJobTotal: 1,
    },
    jobCount: 1,
    hasConflict: false,
    firstImportedAt: ARCHIVE_TIME,
    upstreamAvailability: "not_checked",
    detail: {
      state: "not_imported",
      firstImportedAt: null,
      expiresAt: null,
      appliedPolicy: null,
      policySource: null,
      policyRevision: null,
      content: "not_imported",
    },
  };
}
export function archiveJob(): ArchiveJob {
  return {
    providerJobId: 301,
    name: "Ruff",
    conclusion: "success",
    createdAt: null,
    startedAt: "2026-09-10T09:00:00Z",
    completedAt: "2026-09-10T09:00:02Z",
    labels: ["linux"],
    runnerId: 51,
    runnerGroupId: null,
  };
}
export function archivePage(query = archiveQuery()): ArchiveRead {
  return {
    coverage: "retained_local_rows",
    providerCompleteness: "not_established",
    query,
    configurationRevision: 4,
    dataRevision: 5,
    observedAt: ARCHIVE_TIME,
    records: query.kind === "gaps" ? [] : [archiveRecord()],
    jobs: query.kind === "jobs" ? [archiveJob()] : [],
    gaps: [],
    detail: query.kind === "detail" ? archiveRecord().detail : null,
    nextCursor: null,
  };
}
export function archiveGap(): ArchiveRead["gaps"][number] {
  return {
    gapId: "d".repeat(64),
    configurationRevision: 4,
    reason: "retry_exhausted",
    recordedAt: ARCHIVE_TIME,
    workflowRunId: 101,
    runAttempt: 1,
    runCreatedAt: archiveRecord().header.runCreatedAt,
    sourceWindow: null,
    retained: null,
    retrySupported: true,
    resolution: "missing",
  };
}
export function archiveGapPage(): ArchiveRead {
  return { ...archivePage(archiveQuery("gaps")), gaps: [archiveGap()] };
}

export function archiveDetailPayload(): ArchiveAttemptDetail {
  return {
    schemaVersion: "ci-economics-archive-detail/v1",
    attempt: archiveRecord().header.attempt,
    jobs: [
      {
        providerJobId: 301,
        steps: [
          {
            number: 1,
            status: "completed",
            conclusion: "success",
            startedAt: "2026-09-10T09:00:00Z",
            completedAt: "2026-09-10T09:00:02Z",
          },
        ],
      },
    ],
  };
}

export function archiveDetailPage(
  query: ArchiveDetailRead["query"] = {
    ...archiveQuery("detail"),
    kind: "detail",
    limit: 1,
    createdFrom: null,
    createdThrough: null,
    workflowId: null,
    workflowRunId: 101,
    runAttempt: 1,
    jobName: null,
  },
): ArchiveDetailRead {
  const record = archiveRecord();
  record.detail = {
    state: "retained",
    firstImportedAt: ARCHIVE_TIME,
    expiresAt: "2026-10-12T10:00:00Z",
    appliedPolicy: { mode: "days", days: 30, anchor: "first_successful_detail_import" },
    policySource: "repository_override",
    policyRevision: 4,
    content: "unavailable_format",
  };
  return {
    schemaVersion: "ci-economics-history-attempt-detail/v1",
    coverage: "retained_local_rows",
    providerCompleteness: "not_established",
    query,
    configurationRevision: 4,
    dataRevision: 5,
    observedAt: ARCHIVE_TIME,
    record,
    detail: record.detail,
    detailPayload: archiveDetailPayload(),
  };
}
export function retentionPreview(): RetentionPreview {
  const detail = archiveRecord().detail;
  return {
    reviewedDigest: "b".repeat(64),
    preview: {
      selection: {
        installationId: 1,
        repositoryId: 2,
        generation: 3,
        configurationRevision: 4,
        dataRevision: 5,
        defaultRevision: 1,
        importedThrough: ARCHIVE_TIME,
        action: "erase_details",
        keys: [{ workflowRunId: 101, runAttempt: 1 }],
      },
      effects: [
        {
          key: { workflowRunId: 101, runAttempt: 1 },
          before: detail,
          after: detail,
          payloadBytes: 0,
          deletePayload: false,
        },
      ],
      releasedBytes: 0,
      deletedDetails: 0,
      statisticsPreserved: true,
    },
  };
}
