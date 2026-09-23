import type {
  CiEconomicsAttemptIdentity,
  CiEconomicsAttemptJobs,
  CiEconomicsAttemptPage,
  CiEconomicsAttemptSummary,
} from "../src/api/ciEconomics/schema";

export function ciEconomicsAttemptIdentityFixture(
  overrides: Partial<CiEconomicsAttemptIdentity> = {},
): CiEconomicsAttemptIdentity {
  return {
    headSha: "a".repeat(40),
    installationId: 1,
    repositoryId: 1,
    runAttempt: 2,
    workflowRunId: 4_201,
    ...overrides,
  };
}

export function ciEconomicsAttemptSummaryFixture(
  overrides: Partial<CiEconomicsAttemptSummary> = {},
): CiEconomicsAttemptSummary {
  return {
    attempt: ciEconomicsAttemptIdentityFixture(),
    contractHash: "b".repeat(64),
    jobCount: 2,
    plannedRoute: "selected",
    recordedAt: "2026-09-04T12:00:00.000000Z",
    snapshotDigest: "c".repeat(64),
    subjectId: "d".repeat(64),
    ...overrides,
  };
}

export function ciEconomicsAttemptPageFixture(
  overrides: Partial<CiEconomicsAttemptPage> = {},
): CiEconomicsAttemptPage {
  return {
    items: [ciEconomicsAttemptSummaryFixture()],
    nextCursor: null,
    ok: true,
    schemaVersion: "ci-economics-attempt-page/v1",
    ...overrides,
  };
}

export function ciEconomicsAttemptJobsFixture(
  overrides: Partial<CiEconomicsAttemptJobs> = {},
): CiEconomicsAttemptJobs {
  const attempt = ciEconomicsAttemptIdentityFixture();
  return {
    attempt,
    attemptWall: exactDuration(15_000),
    contractHash: "b".repeat(64),
    definitionVersion: "ci-economics-measurement/v1",
    jobs: [
      {
        conclusion: "success",
        labels: ["backend", "linux"],
        name: "Backend tests",
        providerJobId: 101,
        runner: {
          runnerGroupId: 7,
          runnerGroupName: "example runners",
          runnerId: 31,
          runnerName: "runner-31",
        },
        semanticHash: "e".repeat(64),
        timing: {
          completedAt: "2026-09-04T11:59:58.000000Z",
          createdAt: "2026-09-04T11:59:40.000000Z",
          startedAt: "2026-09-04T11:59:43.000000Z",
        },
      },
      {
        conclusion: "success",
        labels: ["frontend", "linux"],
        name: "Frontend checks",
        providerJobId: 102,
        runner: {
          runnerGroupId: null,
          runnerGroupName: null,
          runnerId: null,
          runnerName: null,
        },
        semanticHash: "f".repeat(64),
        timing: {
          completedAt: "2026-09-04T11:59:59.000000Z",
          createdAt: "2026-09-04T11:59:41.000000Z",
          startedAt: "2026-09-04T11:59:44.000000Z",
        },
      },
    ],
    nextJobId: null,
    observationSetHash: "1".repeat(64),
    ok: true,
    plannedRoute: "selected",
    queue: exactDuration(6_000),
    recordedAt: "2026-09-04T12:00:00.000000Z",
    retainUntil: "2026-12-03T12:00:00.000000Z",
    runnerOccupancy: exactDuration(30_000),
    schemaVersion: "ci-economics-attempt-jobs/v1",
    snapshotDigest: "c".repeat(64),
    subjectId: "d".repeat(64),
    ...overrides,
  };
}

function exactDuration(knownValueMs: number): CiEconomicsAttemptJobs["queue"] {
  return {
    knownJobCount: 2,
    knownValueMs,
    quality: "exact",
    reasonCode: null,
    totalJobCount: 2,
  };
}
