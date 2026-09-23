import { createHash } from "node:crypto";
import type { ReportPage, SourceItem, SourcePage } from "../src/api/ciEconomics/catalogSchema";
import type { AttemptMeasurements, RetainedReport } from "../src/api/ciEconomics/measurementSchema";
import type { ProviderSource, SourceDiscovery } from "../src/api/ciEconomics/sourceSchema";

export const ECONOMICS_SCOPE = { installationId: 1, repositoryId: 1 };
export const ECONOMICS_CSRF = "s".repeat(43);
export const CREATED = "2026-09-08T08:00:00.000000Z";
export const RECEIVED = "2026-09-08T09:00:00.000000Z";
export const RETAINED = "2026-12-07T08:00:00.000000Z";
export const DISCOVERY_INPUT = {
  ...ECONOMICS_SCOPE,
  createdFrom: "2026-09-08T00:00:00Z",
  createdThrough: "2026-09-08T12:00:00Z",
  pageNumber: 1,
};

export function economicsSource(run = 4201, repositoryId = 1): ProviderSource {
  const attempt = {
    headSha: "a".repeat(40),
    installationId: 1,
    repositoryId,
    runAttempt: 2,
    workflowRunId: run,
  };
  const sourceId = hash({ attempt, schemaVersion: "ci-economics-provider-run-source/v1" });
  return {
    sourceKind: "provider_run",
    sourceId,
    attempt,
    runCreatedAt: CREATED,
    providerApiVersion: "2026-03-10",
    sourceEvidenceDigest: "b".repeat(64),
  };
}
export function economicsSourceItem(source = economicsSource()): SourceItem {
  return {
    source,
    status: "captured",
    attemptCount: 1,
    maxAttempts: 8,
    nextAttemptAt: null,
    lastFailureReason: null,
    terminalReason: null,
    completedAt: RECEIVED,
    retainUntil: RETAINED,
  };
}
export function economicsSourcePage(items = [economicsSourceItem()]): SourcePage {
  return {
    schemaVersion: "ci-economics-source-page/v2",
    ok: true,
    ...ECONOMICS_SCOPE,
    items,
    nextCursor: null,
  };
}
export function economicsDiscovery(sources = [economicsSource()]): SourceDiscovery {
  return {
    schemaVersion: "ci-economics-source-discovery/v2",
    ok: true,
    ...DISCOVERY_INPUT,
    providerTotal: sources.length,
    termination: "exhausted",
    sources,
  };
}
export function economicsReport(source = economicsSource()): RetainedReport {
  const payload: RetainedReport["payload"] = {
    schemaVersion: "ci-economics-job-report/v1",
    method: "waited_children/v1",
    attempt: source.attempt,
    providerJobId: 101,
    checkRunId: 102,
    sampleKey: "backend-tests",
    producerDigest: "c".repeat(64),
    workload: {
      protectedInputsDigest: "d".repeat(64),
      runnerClassDigest: "e".repeat(64),
      cacheClassDigest: "f".repeat(64),
    },
    reportedAt: RECEIVED,
    commandExitCode: 0,
    measurements: [
      {
        counter: "cpu_system",
        unit: "microsecond",
        scope: "waited_children",
        value: 10,
        unavailableReason: null,
      },
      {
        counter: "cpu_user",
        unit: "microsecond",
        scope: "waited_children",
        value: 20,
        unavailableReason: null,
      },
      {
        counter: "elapsed",
        unit: "microsecond",
        scope: "reporter_interval",
        value: 50,
        unavailableReason: null,
      },
    ],
  };
  return {
    schemaVersion: "ci-economics-retained-report/v1",
    ok: true,
    reportId: hash({
      schemaVersion: "ci-economics-job-report-identity/v1",
      attempt: source.attempt,
      providerJobId: payload.providerJobId,
      sampleKey: payload.sampleKey,
    }),
    reportDigest: hash(payload),
    source,
    payload,
    origin: { producerClaimHash: "1".repeat(64), providerBindingDigest: "2".repeat(64) },
    receivedAt: RECEIVED,
    retainUntil: RETAINED,
  };
}
export function economicsReportPage(report = economicsReport()): ReportPage {
  return {
    schemaVersion: "ci-measurement-report-page/v2",
    ok: true,
    source: report.source,
    items: [
      {
        reportId: report.reportId,
        reportDigest: report.reportDigest,
        receivedAt: report.receivedAt,
        retainUntil: report.retainUntil,
      },
    ],
    nextCursor: null,
  };
}
export function economicsMeasurements(source = economicsSource()): AttemptMeasurements {
  const duration = {
    knownJobCount: 1,
    knownValueMs: 100,
    quality: "exact" as const,
    reasonCode: null,
    totalJobCount: 1,
  };
  return {
    schemaVersion: "ci-economics-measurements/v2",
    ok: true,
    source,
    recordedAt: RECEIVED,
    retainUntil: RETAINED,
    snapshotDigest: "3".repeat(64),
    definitionVersion: "ci-economics-measurement/v1",
    observationSetHash: "4".repeat(64),
    queue: duration,
    runnerOccupancy: duration,
    attemptWall: duration,
  };
}
function hash(value: object): string {
  const canonical = (item: unknown): unknown =>
    Array.isArray(item)
      ? item.map(canonical)
      : item !== null && typeof item === "object"
        ? Object.fromEntries(
            Object.entries(item)
              .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
              .map(([key, child]) => [key, canonical(child)]),
          )
        : item;
  return createHash("sha256")
    .update(JSON.stringify(canonical(value)))
    .digest("hex");
}
