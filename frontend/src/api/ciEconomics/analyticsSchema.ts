import { z } from "zod";
import type { components } from "../generated";
import { observationInstant, observationMicroseconds } from "./observationSchema";
import {
  observationCounter as count,
  observationTimestamp as instant,
} from "./observationSchema.ts";
import { boundedScalarText } from "./schema";
import { sameScope } from "./sourceSchema";
import { digest, positiveInteger as id, repositoryScopeSchema } from "./sourceSchema.ts";

const day = instant.refine((value) => /T00:00:00(?:\.0+)?(?:Z|\+00:00)$/.test(value));
export const purposeCategorySchema = z.enum(["lint", "typecheck", "test", "build", "deploy"]);
const purpose = purposeCategorySchema;
export const analyticsQuerySchema = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    generation: id,
    createdFrom: day,
    createdUntil: day,
    workflowId: id.nullable().default(null),
    jobName: boundedScalarText(512)
      .refine((value) => !value.includes("\u0000"))
      .nullable()
      .default(null),
    purpose: z
      .enum([...purpose.options, "mixed", "unknown"])
      .nullable()
      .default(null),
    horizonDays: z.number().int().min(1).max(30).default(7),
    minimumDailySamples: z.number().int().min(1).max(1000).default(3),
    degradationRelativeBps: z.number().int().min(1).max(100000).default(2000),
    degradationAbsoluteMs: z.number().int().min(1).max(86400000).default(1000),
    persistenceDays: z.number().int().min(2).max(14).default(3),
  })
  .refine((query) => {
    const width = Date.parse(query.createdUntil) - Date.parse(query.createdFrom);
    return width > 0 && width <= 366 * 86400000;
  });

const selected = z
  .strictObject({
    jobs: count,
    failures: count,
    cancellations: count,
    durationSamples: count,
    queueSamples: count,
    runnerMs: count,
    queueMs: count,
    inconsistentTimings: count,
    missingDuration: count,
    missingQueue: count,
    mixedJobs: count,
    unknownPurposeJobs: count,
  })
  .refine(
    (value) =>
      value.durationSamples + value.missingDuration + value.inconsistentTimings === value.jobs &&
      value.queueSamples + value.missingQueue + value.inconsistentTimings === value.jobs &&
      value.failures + value.cancellations <= value.jobs &&
      value.mixedJobs + value.unknownPurposeJobs <= value.jobs &&
      (value.durationSamples > 0 || value.runnerMs === 0) &&
      (value.queueSamples > 0 || value.queueMs === 0),
  );
const bucket = z
  .strictObject({
    day,
    coverage: z.enum(["unknown", "partial", "complete_retained"]),
    runs: count,
    attempts: count,
    matchingAttempts: count,
    completeAttempts: count,
    partialAttempts: count,
    unavailableAttempts: count,
    conflictAttempts: count,
    knownMissingJobs: count,
    unknownPopulationAttempts: count,
    conflictExcludedJobs: count,
    selected,
    observedRunnerMs: count.nullable(),
    observedQueueMs: count.nullable(),
  })
  .refine(
    (value) =>
      value.attempts ===
        value.completeAttempts +
          value.partialAttempts +
          value.unavailableAttempts +
          value.conflictAttempts &&
      value.matchingAttempts <= value.attempts &&
      value.runs <= value.attempts &&
      value.observedRunnerMs ===
        (value.selected.durationSamples > 0 ? value.selected.runnerMs : null) &&
      value.observedQueueMs === (value.selected.queueSamples > 0 ? value.selected.queueMs : null) &&
      value.coverage ===
        (value.attempts === 0
          ? "unknown"
          : value.completeAttempts === value.attempts
            ? "complete_retained"
            : "partial"),
  );
export const purposeMappingSchema = z.strictObject({
  ...repositoryScopeSchema.shape,
  generation: id,
  version: boundedScalarText(64),
  provenance: boundedScalarText(256),
  entries: z
    .array(
      z.strictObject({
        workflowId: id,
        jobName: boundedScalarText(512).refine((value) => !value.includes("\u0000")),
        purposes: z
          .array(purpose)
          .min(1)
          .max(5)
          .refine((values) => new Set(values).size === values.length),
      }),
    )
    .max(64)
    .refine(
      (entries) =>
        new Set(entries.map((entry) => JSON.stringify([entry.workflowId, entry.jobName]))).size ===
        entries.length,
    ),
});
const mapping = purposeMappingSchema;
const fold = z
  .strictObject({
    trainingUntil: instant,
    testUntil: instant,
    predictedRunnerMs: count,
    actualRunnerMs: count,
    lowerRunnerMs: count.nullable(),
    upperRunnerMs: count.nullable(),
    phase: z.enum(["calibration", "evaluation"]),
  })
  .refine(
    (value) =>
      (observationInstant(value.trainingUntil) ?? "") <
        (observationInstant(value.testUntil) ?? "") &&
      (value.phase === "calibration"
        ? value.lowerRunnerMs === null && value.upperRunnerMs === null
        : value.lowerRunnerMs !== null &&
          value.upperRunnerMs !== null &&
          value.lowerRunnerMs <= value.predictedRunnerMs &&
          value.predictedRunnerMs <= value.upperRunnerMs),
  );
const forecast = z
  .strictObject({
    modelVersion: z.literal("expanding-daily-mean/v1"),
    target: z.literal("retained_run_creation_occupancy"),
    backtestBasis: z.literal("current_archive_reconstructed_chronology"),
    status: z.enum(["available", "unavailable"]),
    reason: z.enum([
      "incomplete_daily_coverage",
      "insufficient_backtest",
      "poor_calibration",
      "incompatible_cohort",
      "conditional_on_unchanged_collection_and_workload",
    ]),
    cutoff: instant,
    horizonDays: z.number().int().min(1).max(30),
    predictedRunnerMs: count.nullable(),
    lowerRunnerMs: count.nullable(),
    upperRunnerMs: count.nullable(),
    backtestMaeMs: count.nullable(),
    empiricalCoverageBps: z.number().int().min(0).max(10000).nullable(),
    usableDays: count,
    durationSamples: count,
    folds: z.array(fold).max(366),
    monetaryEstimate: z.null(),
    monetaryUnavailableReason: z.literal("no_explicit_versioned_tariff"),
  })
  .refine((value) =>
    value.status === "available"
      ? value.predictedRunnerMs !== null &&
        value.lowerRunnerMs !== null &&
        value.upperRunnerMs !== null &&
        value.lowerRunnerMs <= value.predictedRunnerMs &&
        value.predictedRunnerMs <= value.upperRunnerMs &&
        value.backtestMaeMs !== null &&
        value.empiricalCoverageBps !== null &&
        value.empiricalCoverageBps >= 8000 &&
        value.usableDays >= 14 + value.horizonDays * 8 &&
        value.durationSamples >= value.usableDays &&
        value.folds.filter((item) => item.phase === "calibration").length === 4 &&
        value.folds.filter((item) => item.phase === "evaluation").length >= 4 &&
        value.reason === "conditional_on_unchanged_collection_and_workload"
      : value.predictedRunnerMs === null &&
        value.lowerRunnerMs === null &&
        value.upperRunnerMs === null &&
        value.reason !== "conditional_on_unchanged_collection_and_workload",
  )
  .refine((value) => {
    if (
      (value.backtestMaeMs === null) !== (value.empiricalCoverageBps === null) ||
      (value.backtestMaeMs !== null && value.folds.length === 0)
    )
      return false;
    let evaluation = false;
    return value.folds.every((item, index) => {
      const previous = value.folds[index - 1];
      const start = observationMicroseconds(item.trainingUntil),
        end = observationMicroseconds(item.testUntil),
        cutoff = observationMicroseconds(value.cutoff);
      if (
        start === undefined ||
        end === undefined ||
        cutoff === undefined ||
        (evaluation && item.phase === "calibration") ||
        end > cutoff ||
        end - start !== BigInt(value.horizonDays) * 86400000000n ||
        (previous !== undefined &&
          observationInstant(item.trainingUntil) !== observationInstant(previous.testUntil))
      )
        return false;
      evaluation ||= item.phase === "evaluation";
      return true;
    });
  });
const degradation = z.strictObject({
  modelVersion: z.literal("fixed-baseline-hysteresis/v1"),
  status: z.enum(["unavailable", "clear", "pending", "observed_slowdown", "recovered"]),
  reason: z.enum(["incompatible_cohort", "insufficient_samples", "observed_duration_only"]),
  baselineUntil: instant.nullable(),
  baselineMeanMs: count.nullable(),
  baselineSamples: count,
  events: z
    .array(
      z.strictObject({
        key: digest,
        day,
        state: z.enum(["observed_slowdown", "recovered"]),
        meanDurationMs: count,
      }),
    )
    .max(366),
  contributor: z.literal("unclassified"),
});

export const analyticsReportSchema = z
  .strictObject({
    schemaVersion: z.literal("ci-history-analytics/v1"),
    query: analyticsQuerySchema,
    observedAt: instant,
    dataRevision: id,
    configurationRevision: id,
    datasetState: z.enum(["active", "paused"]),
    providerCoverage: z.literal("unknown"),
    unit: z.literal("milliseconds"),
    mapping: mapping.nullable(),
    mappingDigest: digest.nullable(),
    runs: count,
    attempts: count,
    cohort: z.strictObject({
      profile: z.literal("workflow_blob_job_event/v1"),
      knownWorkflowVersions: count,
      unknownWorkflowAttempts: count,
      eventCount: count,
      definitionStable: z.boolean(),
      observedDefinitionChanges: z.boolean(),
      compatible: z.boolean(),
      contributors: z.literal("unclassified"),
    }),
    buckets: z.array(bucket).max(366),
    forecast,
    degradation,
  })
  .refine((report) => {
    const start = Date.parse(report.query.createdFrom);
    const end = Date.parse(report.query.createdUntil);
    return (
      report.buckets.length === (end - start) / 86400000 &&
      report.buckets.every((row, index) => Date.parse(row.day) === start + index * 86400000) &&
      (observationInstant(report.observedAt) ?? "") >=
        (observationInstant(report.query.createdUntil) ?? "") &&
      observationInstant(report.forecast.cutoff) ===
        observationInstant(report.query.createdUntil) &&
      report.forecast.horizonDays === report.query.horizonDays &&
      report.attempts === report.buckets.reduce((sum, row) => sum + row.attempts, 0) &&
      report.runs <= report.attempts &&
      report.buckets.reduce((sum, row) => sum + row.selected.jobs + row.conflictExcludedJobs, 0) <=
        1000000 &&
      (report.forecast.status !== "available" ||
        (report.forecast.usableDays === report.buckets.length &&
          report.forecast.durationSamples ===
            report.buckets.reduce((sum, row) => sum + row.selected.durationSamples, 0))) &&
      (report.mapping === null) === (report.mappingDigest === null) &&
      (report.mapping === null
        ? report.query.purpose === null || report.query.purpose === "unknown"
        : sameScope(report.mapping, report.query) &&
          report.mapping.generation === report.query.generation)
    );
  });
export const analyticsResponseSchema = z.discriminatedUnion("outcome", [
  z.strictObject({
    outcome: z.literal("available"),
    report: analyticsReportSchema,
    unavailable: z.null(),
  }),
  z.strictObject({
    outcome: z.literal("unavailable"),
    report: z.null(),
    unavailable: z.strictObject({
      reason: z.enum([
        "dataset_unavailable",
        "generation_changed",
        "query_budget_exceeded",
        "snapshot_changed",
        "purpose_mapping_unavailable",
        "future_window",
      ]),
    }),
  }),
]) satisfies z.ZodType<components["schemas"]["HistoryAnalyticsResponse"]>;

export type AnalyticsQuery = z.output<typeof analyticsQuerySchema>;
export type AnalyticsReport = z.output<typeof analyticsReportSchema>;
export type AnalyticsResponse = z.output<typeof analyticsResponseSchema>;
