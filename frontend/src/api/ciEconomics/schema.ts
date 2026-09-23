import { z } from "zod";
import type { operations } from "../generated";

type ListAttemptsOperation = operations["list_repository_ci_economics_attempts"];
type GetAttemptJobsOperation = operations["get_ci_economics_attempt_jobs"];

export type CiEconomicsAttemptPage =
  ListAttemptsOperation["responses"][200]["content"]["application/json"];
export type CiEconomicsAttemptSummary = CiEconomicsAttemptPage["items"][number];
export type CiEconomicsAttemptIdentity = CiEconomicsAttemptSummary["attempt"];
export type CiEconomicsAttemptJobs =
  GetAttemptJobsOperation["responses"][200]["content"]["application/json"];
export type CiEconomicsDuration = CiEconomicsAttemptJobs["queue"];
export type CiEconomicsError =
  ListAttemptsOperation["responses"][401]["content"]["application/json"];

export const CI_ECONOMICS_PAGE_SIZE = 50;
export const MAX_CI_ECONOMICS_PAGE_SIZE = 100;

const positiveInteger = z.number().int().safe().positive();
const nonNegativeInteger = z.number().int().safe().nonnegative();
const sha1 = z.string().regex(/^[0-9a-f]{40}$/);
const sha256 = z.string().regex(/^[0-9a-f]{64}$/);
const isoInstant = z.iso.datetime({ offset: true });
export const ciEconomicsAttemptCursorSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\.[0-9a-f]{64}$/)
  .refine(attemptCursorHasCanonicalInstant, "attempt cursor time is not canonical UTC");
const instant = isoInstant.refine(
  (value) => canonicalUtcInstant(value) !== undefined,
  "instant is not canonical UTC",
);
const plannedRoute = z.enum(["selected", "full_ci_counterfactual", "unknown"]);
const evidenceQuality = z.enum(["exact", "partial", "unknown", "conflict"]);
const workflowConclusion = z.enum([
  "success",
  "failure",
  "cancelled",
  "timed_out",
  "skipped",
  "neutral",
  "action_required",
  "startup_failure",
  "stale",
]);

export const ciEconomicsAttemptIdentitySchema: z.ZodType<CiEconomicsAttemptIdentity> =
  z.strictObject({
    headSha: sha1,
    installationId: positiveInteger,
    repositoryId: positiveInteger,
    runAttempt: positiveInteger,
    workflowRunId: positiveInteger,
  });

export const ciEconomicsAttemptSummarySchema: z.ZodType<CiEconomicsAttemptSummary> = z.strictObject(
  {
    attempt: ciEconomicsAttemptIdentitySchema,
    contractHash: sha256,
    jobCount: z.number().int().min(1).max(2_000),
    plannedRoute,
    recordedAt: instant,
    snapshotDigest: sha256,
    subjectId: sha256,
  },
);

export const durationSchema: z.ZodType<CiEconomicsDuration> = z
  .strictObject({
    knownJobCount: nonNegativeInteger,
    knownValueMs: nonNegativeInteger.nullable(),
    quality: evidenceQuality,
    reasonCode: boundedScalarText(128).nullable(),
    totalJobCount: nonNegativeInteger,
  })
  .superRefine((value, context) => {
    const complete = value.knownJobCount === value.totalJobCount;
    const hasValue = value.knownValueMs !== null;
    const valid =
      value.knownJobCount <= value.totalJobCount &&
      (value.quality === "exact"
        ? complete && hasValue && value.reasonCode === null
        : value.quality === "partial"
          ? !complete && value.knownJobCount > 0 && hasValue && value.reasonCode !== null
          : !hasValue && value.reasonCode !== null);
    if (!valid) {
      context.addIssue({ code: "custom", message: "duration evidence is inconsistent" });
    }
  });

const jobTimingSchema = z
  .strictObject({
    completedAt: instant.nullable(),
    createdAt: instant.nullable(),
    startedAt: instant.nullable(),
  })
  .superRefine((value, context) => {
    const known = [value.createdAt, value.startedAt, value.completedAt]
      .filter((item): item is string => item !== null)
      .map(canonicalUtcInstant);
    if (known.some((item) => item === undefined) || !isNonDescending(known as string[])) {
      context.addIssue({ code: "custom", message: "known job timestamps are not monotonic" });
    }
  });

const runnerSchema = z
  .strictObject({
    runnerGroupId: positiveInteger.nullable(),
    runnerGroupName: boundedScalarText(256).nullable(),
    runnerId: positiveInteger.nullable(),
    runnerName: boundedScalarText(256).nullable(),
  })
  .superRefine((value, context) => {
    if (
      (value.runnerId === null) !== (value.runnerName === null) ||
      (value.runnerGroupId === null) !== (value.runnerGroupName === null) ||
      (value.runnerId === null && value.runnerGroupId !== null)
    ) {
      context.addIssue({ code: "custom", message: "runner identity is inconsistent" });
    }
  });

const jobSchema = z
  .strictObject({
    conclusion: workflowConclusion,
    labels: z.array(boundedScalarText(128)).max(32),
    name: boundedScalarText(512),
    providerJobId: positiveInteger,
    runner: runnerSchema,
    semanticHash: sha256,
    timing: jobTimingSchema,
  })
  .superRefine((value, context) => {
    if (!isCanonicalTextSequence(value.labels)) {
      context.addIssue({ code: "custom", message: "job labels are not canonical" });
    }
  });

export const ciEconomicsAttemptPageSchema: z.ZodType<CiEconomicsAttemptPage> = z
  .strictObject({
    items: z.array(ciEconomicsAttemptSummarySchema).max(MAX_CI_ECONOMICS_PAGE_SIZE),
    nextCursor: ciEconomicsAttemptCursorSchema.nullable(),
    ok: z.literal(true),
    schemaVersion: z.literal("ci-economics-attempt-page/v1"),
  })
  .superRefine((value, context) => {
    const ordering = value.items.map(attemptSummaryCursorOrUndefined);
    if (ordering.some((item) => item === undefined)) {
      context.addIssue({ code: "custom", message: "attempt page contains an invalid instant" });
      return;
    }
    const admittedOrdering = ordering.filter((item): item is string => item !== undefined);
    if (!isAscending(admittedOrdering, true)) {
      context.addIssue({ code: "custom", message: "attempt page is not newest-first" });
    }
    const finalCursor = admittedOrdering.at(-1);
    if (value.nextCursor !== null && value.nextCursor !== finalCursor) {
      context.addIssue({ code: "custom", message: "attempt cursor does not identify the page" });
    }
  });

export const ciEconomicsAttemptJobsSchema: z.ZodType<CiEconomicsAttemptJobs> = z
  .strictObject({
    attempt: ciEconomicsAttemptIdentitySchema,
    attemptWall: durationSchema,
    contractHash: sha256,
    definitionVersion: z.literal("ci-economics-measurement/v1"),
    jobs: z.array(jobSchema).max(MAX_CI_ECONOMICS_PAGE_SIZE),
    nextJobId: positiveInteger.nullable(),
    observationSetHash: sha256,
    ok: z.literal(true),
    plannedRoute,
    queue: durationSchema,
    recordedAt: instant,
    retainUntil: instant,
    runnerOccupancy: durationSchema,
    schemaVersion: z.literal("ci-economics-attempt-jobs/v1"),
    snapshotDigest: sha256,
    subjectId: sha256,
  })
  .superRefine((value, context) => {
    const jobIds = value.jobs.map((job) => job.providerJobId);
    const total = value.queue.totalJobCount;
    const conflictCount = [value.queue, value.runnerOccupancy, value.attemptWall].filter(
      (duration) => duration.quality === "conflict",
    ).length;
    const recordedAt = canonicalUtcInstant(value.recordedAt);
    const retainUntil = canonicalUtcInstant(value.retainUntil);
    if (
      !isAscending(jobIds, false) ||
      (value.nextJobId !== null && value.nextJobId !== jobIds.at(-1)) ||
      recordedAt === undefined ||
      retainUntil === undefined ||
      retainUntil <= recordedAt ||
      total < 1 ||
      value.runnerOccupancy.totalJobCount !== total ||
      value.attemptWall.totalJobCount !== total ||
      (conflictCount !== 0 && conflictCount !== 3) ||
      value.jobs.length > total
    ) {
      context.addIssue({ code: "custom", message: "attempt economics projection is inconsistent" });
    }
  });

export const ciEconomicsErrorSchema: z.ZodType<CiEconomicsError> = z.strictObject({
  error: z.enum(["unauthenticated", "forbidden", "not_found", "unavailable"]),
  ok: z.literal(false),
});

export function sameAttemptIdentity(
  left: CiEconomicsAttemptIdentity,
  right: CiEconomicsAttemptIdentity,
): boolean {
  return (
    left.installationId === right.installationId &&
    left.repositoryId === right.repositoryId &&
    left.workflowRunId === right.workflowRunId &&
    left.runAttempt === right.runAttempt &&
    left.headSha === right.headSha
  );
}

export function sameAttemptEvidence(
  summary: CiEconomicsAttemptSummary,
  economics: CiEconomicsAttemptJobs,
): boolean {
  return (
    sameAttemptIdentity(summary.attempt, economics.attempt) &&
    summary.subjectId === economics.subjectId &&
    summary.contractHash === economics.contractHash &&
    summary.plannedRoute === economics.plannedRoute &&
    summary.snapshotDigest === economics.snapshotDigest &&
    canonicalUtcInstant(summary.recordedAt) === canonicalUtcInstant(economics.recordedAt) &&
    summary.jobCount === economics.queue.totalJobCount
  );
}

export function attemptSummaryCursor(summary: CiEconomicsAttemptSummary): string {
  const cursor = attemptSummaryCursorOrUndefined(summary);
  if (cursor === undefined) throw new TypeError("attempt summary contains an invalid instant");
  return cursor;
}

function attemptSummaryCursorOrUndefined(summary: CiEconomicsAttemptSummary): string | undefined {
  const timestamp = canonicalUtcInstant(summary.recordedAt);
  return timestamp === undefined ? undefined : `${timestamp}.${summary.subjectId}`;
}

export function boundedScalarText(maximumCodePoints: number) {
  return z
    .string()
    .min(1)
    .refine(isUnicodeScalarText, "text contains an unpaired surrogate")
    .refine(
      (value) => Array.from(value).length <= maximumCodePoints,
      "text exceeds its code-point bound",
    );
}

export function canonicalUtcInstant(value: string): string | undefined {
  if (!isoInstant.safeParse(value).success) return undefined;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z$/.exec(value);
  if (!match || match[1]?.startsWith("0000-")) return undefined;
  return `${match[1]}.${(match[2] ?? "").padEnd(6, "0")}Z`;
}

function attemptCursorHasCanonicalInstant(value: string): boolean {
  const timestamp = value.slice(0, 27);
  return canonicalUtcInstant(timestamp) === timestamp;
}

function isUnicodeScalarText(value: string): boolean {
  return !Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint !== undefined && codePoint >= 0xd800 && codePoint <= 0xdfff;
  });
}

function isAscending(values: readonly (number | string)[], descending: boolean): boolean {
  return values.every((value, index) => {
    const previous = values[index - 1];
    return previous === undefined || (descending ? previous > value : previous < value);
  });
}

function isNonDescending(values: readonly string[]): boolean {
  return values.every((value, index) => {
    const previous = values[index - 1];
    return previous === undefined || previous <= value;
  });
}

function isCanonicalTextSequence(values: readonly string[]): boolean {
  return values.every((value, index) => {
    const previous = values[index - 1];
    return previous === undefined || compareUnicodeScalarText(previous, value) < 0;
  });
}

function compareUnicodeScalarText(left: string, right: string): number {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0) ?? -1);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0) ?? -1);
  const commonLength = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < commonLength; index += 1) {
    const difference = (leftPoints[index] ?? -1) - (rightPoints[index] ?? -1);
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}
