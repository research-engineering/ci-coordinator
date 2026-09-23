import { z } from "zod";
import type { components } from "../generated";
import {
  canonicalUtcInstant,
  ciEconomicsAttemptIdentitySchema,
  durationSchema,
  sameAttemptIdentity,
} from "./schema";
import { digest, positiveInteger, providerSourceSchema, utcInstant } from "./sourceSchema";

export type AttemptMeasurements = components["schemas"]["EconomicsMeasurementsResponse"];
export type RetainedReport = components["schemas"]["RetainedMeasurementReportResponse"];
export type ReportMeasurement = components["schemas"]["ReportCounterPayload"];
export type ReportCounter = ReportMeasurement["counter"];
export const COUNTER_ORDER = ["cpu_system", "cpu_user", "elapsed"] as const;
export const counterSchema = z.enum(COUNTER_ORDER);
export const safeUnsigned = z.number().int().safe().nonnegative();
export const safeSigned = z.number().int().safe();

const reconciliationSourceSchema = z.strictObject({
  sourceKind: z.literal("reconciliation"),
  sourceId: digest,
  attempt: ciEconomicsAttemptIdentitySchema,
  contractHash: digest,
  plannedRoute: z.enum(["selected", "full_ci_counterfactual", "unknown"]),
});
export const attemptMeasurementsSchema: z.ZodType<AttemptMeasurements> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-measurements/v2"),
    ok: z.literal(true),
    source: z.union([providerSourceSchema, reconciliationSourceSchema]),
    recordedAt: utcInstant,
    retainUntil: utcInstant,
    snapshotDigest: digest,
    definitionVersion: z.literal("ci-economics-measurement/v1"),
    observationSetHash: digest,
    queue: durationSchema,
    runnerOccupancy: durationSchema,
    attemptWall: durationSchema,
  })
  .refine((value) => {
    const recorded = canonicalUtcInstant(value.recordedAt);
    const retained = canonicalUtcInstant(value.retainUntil);
    return (
      recorded !== undefined &&
      retained !== undefined &&
      recorded < retained &&
      value.queue.totalJobCount === value.runnerOccupancy.totalJobCount &&
      value.queue.totalJobCount === value.attemptWall.totalJobCount
    );
  }, "retained measurements have inconsistent lifetime or population");

export const reportMeasurementSchema: z.ZodType<ReportMeasurement> = z
  .strictObject({
    counter: counterSchema,
    unit: z.literal("microsecond"),
    scope: z.enum(["waited_children", "reporter_interval"]),
    value: safeUnsigned.nullable(),
    unavailableReason: z
      .enum(["unsupported_platform", "counter_error", "out_of_range", "incomplete_scope"])
      .nullable(),
  })
  .refine(
    (value) =>
      (value.scope === "reporter_interval") === (value.counter === "elapsed") &&
      (value.value === null) === (value.unavailableReason !== null),
    "counter scope or availability contradicts its definition",
  );

export const retainedReportSchema: z.ZodType<RetainedReport> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-retained-report/v1"),
    ok: z.literal(true),
    reportId: digest,
    reportDigest: digest,
    source: providerSourceSchema,
    origin: z.strictObject({ producerClaimHash: digest, providerBindingDigest: digest }),
    receivedAt: utcInstant,
    retainUntil: utcInstant,
    payload: z.strictObject({
      schemaVersion: z.literal("ci-economics-job-report/v1"),
      method: z.literal("waited_children/v1"),
      attempt: ciEconomicsAttemptIdentitySchema,
      providerJobId: positiveInteger,
      checkRunId: positiveInteger,
      sampleKey: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/),
      producerDigest: digest,
      workload: z.strictObject({
        protectedInputsDigest: digest,
        runnerClassDigest: digest,
        cacheClassDigest: digest,
      }),
      reportedAt: utcInstant.refine((value) => canonicalUtcInstant(value) === value),
      commandExitCode: safeSigned,
      measurements: z
        .array(reportMeasurementSchema)
        .length(3)
        .refine((items) => items.every((item, index) => item.counter === COUNTER_ORDER[index])),
    }),
  })
  .refine((value) => {
    const created = canonicalUtcInstant(value.source.runCreatedAt);
    const received = canonicalUtcInstant(value.receivedAt);
    const retained = canonicalUtcInstant(value.retainUntil);
    return (
      sameAttemptIdentity(value.source.attempt, value.payload.attempt) &&
      created !== undefined &&
      received !== undefined &&
      retained !== undefined &&
      created <= received &&
      received < retained
    );
  }, "retained report scope, identity or lifetime is inconsistent");
