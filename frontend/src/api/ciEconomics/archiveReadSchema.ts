import { z } from "zod";
import type { components } from "../generated";
import {
  archiveAttemptDetailSchema,
  archiveDetailSchema,
  archiveJobSchema,
  archiveRecordSchema,
} from "./archiveRecordSchema.ts";
import { observationInstant, observationTimestamp } from "./observationSchema.ts";
import { boundedScalarText } from "./schema";
import { digest, positiveInteger, repositoryScopeSchema, sameScope } from "./sourceSchema.ts";

export const archiveQuerySchema = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    generation: positiveInteger,
    kind: z.enum(["records", "jobs", "gaps", "detail"]),
    limit: z.number().int().min(1).max(50).default(50),
    createdFrom: observationTimestamp.nullable().default(null),
    createdThrough: observationTimestamp.nullable().default(null),
    workflowId: positiveInteger.nullable().default(null),
    workflowRunId: positiveInteger.nullable().default(null),
    runAttempt: positiveInteger.nullable().default(null),
    jobName: boundedScalarText(512)
      .refine((value) => !value.includes("\u0000"))
      .nullable()
      .default(null),
  })
  .refine(
    (q) =>
      (q.workflowRunId === null) === (q.runAttempt === null) &&
      (!(q.kind === "jobs" || q.kind === "detail") || q.workflowRunId !== null) &&
      (q.kind !== "gaps" || q.workflowRunId === null) &&
      (q.kind === "records" ||
        (q.createdFrom === null && q.createdThrough === null && q.workflowId === null)) &&
      (q.kind === "jobs" || q.kind === "records" || q.jobName === null) &&
      (q.createdFrom === null ||
        q.createdThrough === null ||
        (observationInstant(q.createdFrom) ?? "") <= (observationInstant(q.createdThrough) ?? "")),
  );

export const archiveDetailQuerySchema = z.strictObject({
  ...repositoryScopeSchema.shape,
  generation: positiveInteger,
  kind: z.literal("detail"),
  limit: z.literal(1),
  createdFrom: z.null(),
  createdThrough: z.null(),
  workflowId: z.null(),
  workflowRunId: positiveInteger,
  runAttempt: positiveInteger,
  jobName: z.null(),
});

const windowSchema = z.strictObject({
  ...repositoryScopeSchema.shape,
  createdFrom: observationTimestamp,
  createdThrough: observationTimestamp,
  windowFrom: observationTimestamp,
  windowThrough: observationTimestamp,
  cycleStartedAt: observationTimestamp,
  pageNumber: z.number().int().min(1).max(10),
});
const gapSchema = z
  .strictObject({
    gapId: digest,
    recordedAt: observationTimestamp,
    configurationRevision: positiveInteger,
    reason: z.enum([
      "provider_truncated",
      "provider_not_found",
      "job_population_incomplete",
      "incomparable",
      "provider_deferred",
      "retry_exhausted",
    ]),
    workflowRunId: positiveInteger.nullable(),
    runAttempt: positiveInteger.nullable(),
    sourceWindow: windowSchema.nullable(),
    runCreatedAt: observationTimestamp.nullable(),
    retained: archiveRecordSchema.nullable(),
    retrySupported: z.boolean(),
    resolution: z.enum([
      "not_evaluated",
      "missing",
      "retained_complete",
      "retained_incomplete",
      "retained_conflicting",
    ]),
  })
  .refine((gap) => {
    const retained = gap.retained;
    const expected =
      gap.workflowRunId === null
        ? "not_evaluated"
        : retained === null
          ? "missing"
          : retained.hasConflict || retained.header.population === "conflict"
            ? "retained_conflicting"
            : retained.header.population === "complete"
              ? "retained_complete"
              : "retained_incomplete";
    return (
      (gap.workflowRunId === null) === (gap.runAttempt === null) &&
      (gap.reason === "provider_truncated") === (gap.workflowRunId === null) &&
      (gap.sourceWindow === null) === (gap.runCreatedAt !== null) &&
      gap.retrySupported === (gap.sourceWindow === null && gap.runCreatedAt !== null) &&
      (gap.runCreatedAt === null ||
        (observationInstant(gap.runCreatedAt) ?? "") <=
          (observationInstant(gap.recordedAt) ?? "")) &&
      gap.resolution === expected &&
      (retained === null ||
        (retained.header.attempt.workflowRunId === gap.workflowRunId &&
          retained.header.attempt.runAttempt === gap.runAttempt &&
          (gap.runCreatedAt === null ||
            observationInstant(gap.runCreatedAt) ===
              observationInstant(retained.header.runCreatedAt))))
    );
  });

export const archiveReadSchema = z
  .strictObject({
    coverage: z.literal("retained_local_rows"),
    providerCompleteness: z.literal("not_established"),
    query: archiveQuerySchema,
    configurationRevision: positiveInteger,
    dataRevision: positiveInteger,
    observedAt: observationTimestamp,
    records: z.array(archiveRecordSchema).max(50),
    jobs: z.array(archiveJobSchema).max(50),
    gaps: z.array(gapSchema).max(50),
    detail: archiveDetailSchema.nullable(),
    nextCursor: z.string().min(1).max(4096).nullable(),
  })
  .refine((page) => {
    const q = page.query;
    const kind = q.kind;
    if (kind === "records" && (page.jobs.length || page.gaps.length || page.detail !== null))
      return false;
    if (kind === "gaps" && (page.records.length || page.jobs.length || page.detail !== null))
      return false;
    if ((kind === "jobs" || kind === "detail") && page.records.length !== 1) return false;
    if (kind === "jobs" && (page.gaps.length || page.detail !== null)) return false;
    if (
      kind === "detail" &&
      (page.jobs.length || page.gaps.length || page.detail === null || page.nextCursor !== null)
    )
      return false;
    const items = kind === "jobs" ? page.jobs : kind === "gaps" ? page.gaps : page.records;
    if (items.length > q.limit || (items.length === 0 && page.nextCursor !== null)) return false;
    const observed = observationInstant(page.observedAt) ?? "";
    const recordKeys = page.records.map(
      (record) =>
        [
          observationInstant(record.header.runCreatedAt) ?? "",
          record.header.attempt.workflowRunId,
          record.header.attempt.runAttempt,
        ] as const,
    );
    if (
      recordKeys.some((key, index) => {
        const prior = recordKeys[index - 1];
        return (
          prior !== undefined &&
          !(
            key[0] > prior[0] ||
            (key[0] === prior[0] &&
              (key[1] > prior[1] || (key[1] === prior[1] && key[2] > prior[2])))
          )
        );
      }) ||
      page.jobs.some(
        (job, index) =>
          index > 0 && job.providerJobId <= (page.jobs[index - 1]?.providerJobId ?? 0),
      ) ||
      page.gaps.some((gap, index) => index > 0 && gap.gapId <= (page.gaps[index - 1]?.gapId ?? ""))
    )
      return false;
    if (
      kind === "detail" &&
      JSON.stringify(page.detail) !== JSON.stringify(page.records[0]?.detail)
    )
      return false;
    return (
      page.records.every(({ header, firstImportedAt }) => {
        const created = observationInstant(header.runCreatedAt) ?? "";
        return (
          sameScope(q, header.attempt) &&
          created <= observed &&
          (observationInstant(firstImportedAt) ?? "") <= observed &&
          (q.workflowId === null || q.workflowId === header.workflowId) &&
          (q.workflowRunId === null ||
            (header.attempt.workflowRunId === q.workflowRunId &&
              header.attempt.runAttempt === q.runAttempt)) &&
          (q.createdFrom === null || created >= (observationInstant(q.createdFrom) ?? "")) &&
          (q.createdThrough === null || created <= (observationInstant(q.createdThrough) ?? ""))
        );
      }) &&
      page.jobs.every((job) => q.jobName === null || q.jobName === job.name) &&
      page.gaps.every(
        (gap) =>
          (gap.sourceWindow === null || sameScope(q, gap.sourceWindow)) &&
          gap.configurationRevision <= page.configurationRevision &&
          (observationInstant(gap.recordedAt) ?? "") <= observed &&
          (gap.retained === null ||
            (sameScope(q, gap.retained.header.attempt) &&
              (observationInstant(gap.retained.firstImportedAt) ?? "") <= observed &&
              (observationInstant(gap.retained.header.runCreatedAt) ?? "") <= observed)),
      )
    );
  }) satisfies z.ZodType<components["schemas"]["HistoryReadResponse"]>;

export type ArchiveQuery = z.infer<typeof archiveQuerySchema>;
export type ArchiveRead = z.infer<typeof archiveReadSchema>;
export const archiveDetailReadSchema = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-history-attempt-detail/v1"),
    coverage: z.literal("retained_local_rows"),
    providerCompleteness: z.literal("not_established"),
    query: archiveDetailQuerySchema,
    configurationRevision: positiveInteger,
    dataRevision: positiveInteger,
    observedAt: observationTimestamp,
    record: archiveRecordSchema,
    detail: archiveDetailSchema,
    detailPayload: archiveAttemptDetailSchema.nullable(),
  })
  .refine(
    (value) =>
      value.record.header.attempt.workflowRunId === value.query.workflowRunId &&
      value.record.header.attempt.runAttempt === value.query.runAttempt &&
      sameScope(value.query, value.record.header.attempt) &&
      JSON.stringify(value.detail) === JSON.stringify(value.record.detail) &&
      (value.detailPayload === null ||
        (value.detail.state === "retained" &&
          value.record.header.population === "complete" &&
          !value.record.hasConflict &&
          value.detail.firstImportedAt !== null &&
          (observationInstant(value.detail.firstImportedAt) ?? "") <=
            (observationInstant(value.observedAt) ?? "") &&
          (observationInstant(value.record.firstImportedAt) ?? "") <=
            (observationInstant(value.observedAt) ?? "") &&
          (observationInstant(value.record.header.runCreatedAt) ?? "") <=
            (observationInstant(value.observedAt) ?? "") &&
          (value.detail.expiresAt === null ||
            (observationInstant(value.detail.expiresAt) ?? "") >
              (observationInstant(value.observedAt) ?? "")) &&
          value.detailPayload.attempt.headSha === value.record.header.attempt.headSha &&
          value.detailPayload.attempt.workflowRunId === value.query.workflowRunId &&
          value.detailPayload.attempt.runAttempt === value.query.runAttempt &&
          sameScope(value.query, value.detailPayload.attempt) &&
          value.detailPayload.jobs.length === value.record.jobCount)),
  ) satisfies z.ZodType<components["schemas"]["HistoryAttemptDetailResponse"]>;
export type ArchiveDetailQuery = z.infer<typeof archiveDetailQuerySchema>;
export type ArchiveDetailRead = z.infer<typeof archiveDetailReadSchema>;

export function sameArchiveQuery(left: ArchiveQuery, right: ArchiveQuery): boolean {
  return Object.entries(left).every(([key, value]) => {
    const actual = right[key as keyof ArchiveQuery];
    return (key === "createdFrom" || key === "createdThrough") && value !== null && actual !== null
      ? observationInstant(String(value)) === observationInstant(String(actual))
      : value === actual;
  });
}
