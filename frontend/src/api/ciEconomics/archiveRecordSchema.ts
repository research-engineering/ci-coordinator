import { z } from "zod";
import type { components } from "../generated";
import { detailRetentionSchema } from "./historySchema.ts";
import {
  observationCounter,
  observationMicroseconds,
  observationTimestamp,
} from "./observationSchema.ts";
import { boundedScalarText, ciEconomicsAttemptIdentitySchema } from "./schema.ts";
import { positiveInteger } from "./sourceSchema.ts";

const conclusion = z.enum([
  "success",
  "failure",
  "neutral",
  "cancelled",
  "skipped",
  "timed_out",
  "action_required",
  "stale",
  "startup_failure",
]);
const instant = observationTimestamp;
const text = boundedScalarText(512).refine((value) => !value.includes("\u0000"));

export const archiveDetailSchema = z
  .strictObject({
    state: z.enum(["not_imported", "retained", "expired"]),
    firstImportedAt: instant.nullable(),
    expiresAt: instant.nullable(),
    appliedPolicy: detailRetentionSchema.nullable(),
    policySource: z.enum(["service_default", "repository_override"]).nullable(),
    policyRevision: positiveInteger.nullable(),
    content: z.enum(["not_imported", "expired", "unavailable_format"]),
  })
  .refine((value) => {
    if (value.content !== (value.state === "retained" ? "unavailable_format" : value.state))
      return false;
    if (value.state === "not_imported")
      return (
        value.firstImportedAt === null &&
        value.expiresAt === null &&
        value.appliedPolicy === null &&
        value.policySource === null &&
        value.policyRevision === null
      );
    if (
      value.firstImportedAt === null ||
      value.appliedPolicy === null ||
      value.policySource === null ||
      value.policyRevision === null
    )
      return false;
    if (value.state === "retained" && value.appliedPolicy.mode === "disabled") return false;
    if (value.appliedPolicy.mode !== "days") return value.expiresAt === null;
    const first = observationMicroseconds(value.firstImportedAt);
    return (
      first !== undefined &&
      value.expiresAt !== null &&
      observationMicroseconds(value.expiresAt) ===
        first + BigInt(value.appliedPolicy.days) * 86400000000n
    );
  });

const headerSchema = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-archive-statistics/v1"),
    attempt: ciEconomicsAttemptIdentitySchema,
    workflowId: positiveInteger,
    workflowPath: boundedScalarText(1024).nullable(),
    workflowBlobSha: z
      .string()
      .regex(/^[0-9a-f]{40}$/)
      .nullable(),
    event: boundedScalarText(64),
    conclusion: conclusion.nullable(),
    runCreatedAt: instant,
    population: z.enum(["complete", "partial", "unavailable", "conflict"]),
    providerJobTotal: observationCounter.nullable(),
  })
  .refine((value) => value.workflowBlobSha === null || value.workflowPath !== null);

export const archiveRecordSchema = z
  .strictObject({
    header: headerSchema,
    jobCount: observationCounter.max(2000),
    hasConflict: z.boolean(),
    firstImportedAt: instant,
    detail: archiveDetailSchema,
    upstreamAvailability: z.literal("not_checked"),
  })
  .refine(
    ({ header, jobCount }) =>
      (header.providerJobTotal === null || jobCount <= header.providerJobTotal) &&
      (header.population !== "complete" || header.providerJobTotal === jobCount) &&
      (header.population !== "unavailable" ||
        (jobCount === 0 && header.providerJobTotal === null)) &&
      (header.population !== "partial" ||
        ((jobCount > 0 || header.providerJobTotal !== null) &&
          header.providerJobTotal !== jobCount)),
  ) satisfies z.ZodType<components["schemas"]["HistoryRecordSummary"]>;

export const archiveJobSchema = z
  .strictObject({
    providerJobId: positiveInteger,
    name: text,
    conclusion,
    createdAt: instant.nullable(),
    startedAt: instant.nullable(),
    completedAt: instant.nullable(),
    labels: z
      .array(boundedScalarText(128))
      .max(32)
      .refine((labels) => new Set(labels).size === labels.length),
    runnerId: positiveInteger.nullable(),
    runnerGroupId: positiveInteger.nullable(),
  })
  .refine((value) => value.runnerId !== null || value.runnerGroupId === null) satisfies z.ZodType<
  components["schemas"]["ArchivedJobStatistics"]
>;

const archiveStepDetailSchema = z.strictObject({
  number: positiveInteger,
  status: z.literal("completed"),
  conclusion: conclusion.nullable(),
  startedAt: instant.nullable(),
  completedAt: instant.nullable(),
});

const archiveJobDetailSchema = z
  .strictObject({
    providerJobId: positiveInteger,
    steps: z.array(archiveStepDetailSchema).max(256),
  })
  .refine((job) =>
    job.steps.every((step, index) => {
      const previous = job.steps[index - 1];
      return previous === undefined || step.number > previous.number;
    }),
  );

export const archiveAttemptDetailSchema = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-archive-detail/v1"),
    attempt: ciEconomicsAttemptIdentitySchema,
    jobs: z.array(archiveJobDetailSchema).max(2000),
  })
  .refine((detail) =>
    detail.jobs.every((job, index) => {
      const previous = detail.jobs[index - 1];
      return previous === undefined || job.providerJobId > previous.providerJobId;
    }),
  ) satisfies z.ZodType<components["schemas"]["ArchivedAttemptDetail"]>;

export type ArchiveRecord = z.infer<typeof archiveRecordSchema>;
export type ArchiveJob = z.infer<typeof archiveJobSchema>;
export type ArchiveAttemptDetail = z.infer<typeof archiveAttemptDetailSchema>;
