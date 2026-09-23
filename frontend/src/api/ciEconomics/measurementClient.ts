import { type ReportPointer, reportPointerSchema } from "./catalogSchema";
import {
  type ReportBudget,
  type ReportComparison,
  reportBudgetSchema,
  reportComparisonSchema,
} from "./comparisonSchema";
import {
  type AttemptMeasurements,
  attemptMeasurementsSchema,
  counterSchema,
  type ReportCounter,
  type RetainedReport,
  retainedReportSchema,
  safeUnsigned,
} from "./measurementSchema";
import { canonicalUtcInstant, sameAttemptIdentity } from "./schema";
import {
  type ProviderSource,
  providerSourceSchema,
  sameScope,
  sameSourceIdentity,
  sourceIdentityIsCanonical,
} from "./sourceSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

export async function fetchAttemptMeasurements(
  source: ProviderSource,
  signal?: AbortSignal,
): Promise<EconomicsResult<AttemptMeasurements>> {
  if (!providerSourceSchema.safeParse(source).success || !(await sourceIdentityIsCanonical(source)))
    return { kind: "invalid-response" };
  const attempt = source.attempt;
  return requestEconomics({
    path: `${repositoryPath(source)}/attempts/${attempt.workflowRunId}/${attempt.runAttempt}/measurements?headSha=${attempt.headSha}`,
    schema: attemptMeasurementsSchema,
    maximumBytes: 16 * 1024,
    signal,
    admits: (value) =>
      sameAttemptIdentity(value.source.attempt, attempt) &&
      (value.source.sourceKind === "reconciliation" || sameSourceIdentity(source, value.source)),
  });
}

export async function fetchMeasurementReport(
  source: ProviderSource,
  pointer: ReportPointer,
  signal?: AbortSignal,
): Promise<EconomicsResult<RetainedReport>> {
  if (
    !providerSourceSchema.safeParse(source).success ||
    !reportPointerSchema.safeParse(pointer).success ||
    !(await sourceIdentityIsCanonical(source))
  )
    return { kind: "invalid-response" };
  return requestEconomics({
    path: `${repositoryPath(source)}/reports/${pointer.reportId}`,
    schema: retainedReportSchema,
    maximumBytes: 256 * 1024,
    signal,
    admits: (value) =>
      sameSourceIdentity(source, value.source) &&
      value.reportId === pointer.reportId &&
      value.reportDigest === pointer.reportDigest &&
      canonicalUtcInstant(value.receivedAt) === canonicalUtcInstant(pointer.receivedAt) &&
      canonicalUtcInstant(value.retainUntil) === canonicalUtcInstant(pointer.retainUntil),
  });
}

export async function compareMeasurementReports(
  baseline: RetainedReport,
  treatment: RetainedReport,
  signal?: AbortSignal,
): Promise<EconomicsResult<ReportComparison>> {
  if (
    !retainedReportSchema.safeParse(baseline).success ||
    !retainedReportSchema.safeParse(treatment).success ||
    !sameScope(baseline.source.attempt, treatment.source.attempt)
  )
    return { kind: "invalid-response" };
  const query = new URLSearchParams({
    baselineReportId: baseline.reportId,
    treatmentReportId: treatment.reportId,
  });
  return requestEconomics({
    path: `${repositoryPath(baseline.source)}/report-comparisons?${query}`,
    schema: reportComparisonSchema,
    maximumBytes: 512 * 1024,
    signal,
    admits: (value) =>
      sameRetainedReport(baseline, value.baseline) &&
      sameRetainedReport(treatment, value.treatment),
  });
}

export async function evaluateMeasurementBudget(
  report: RetainedReport,
  counter: ReportCounter,
  maximumUs: number,
  signal?: AbortSignal,
): Promise<EconomicsResult<ReportBudget>> {
  if (
    !retainedReportSchema.safeParse(report).success ||
    !counterSchema.safeParse(counter).success ||
    !safeUnsigned.safeParse(maximumUs).success
  )
    return { kind: "invalid-response" };
  const query = new URLSearchParams({ counter, maximumUs: String(maximumUs) });
  return requestEconomics({
    path: `${repositoryPath(report.source)}/reports/${report.reportId}/budget?${query}`,
    schema: reportBudgetSchema,
    maximumBytes: 256 * 1024,
    signal,
    admits: (value) =>
      sameRetainedReport(report, value.report) &&
      value.measurement.counter === counter &&
      value.maximumUs === maximumUs,
  });
}

function repositoryPath(source: ProviderSource): string {
  return `/api/v2/economics/repositories/${source.attempt.installationId}/${source.attempt.repositoryId}`;
}

function sameRetainedReport(expected: RetainedReport, actual: RetainedReport): boolean {
  return (
    expected.reportId === actual.reportId &&
    expected.reportDigest === actual.reportDigest &&
    sameSourceIdentity(expected.source, actual.source) &&
    canonicalUtcInstant(expected.receivedAt) === canonicalUtcInstant(actual.receivedAt) &&
    canonicalUtcInstant(expected.retainUntil) === canonicalUtcInstant(actual.retainUntil)
  );
}
