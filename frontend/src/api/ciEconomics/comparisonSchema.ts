import { z } from "zod";
import type { components } from "../generated";
import {
  COUNTER_ORDER,
  counterSchema,
  reportMeasurementSchema,
  retainedReportSchema,
  safeSigned,
  safeUnsigned,
} from "./measurementSchema";
import { digest, positiveInteger } from "./sourceSchema";

export type ReportComparison = components["schemas"]["MeasurementReportComparisonResponse"];
export type ReportBudget = components["schemas"]["MeasurementReportBudgetResponse"];
const mismatchOrder = [
  "repository_scope",
  "same_attempt",
  "source_sha",
  "sample_key",
  "producer",
  "protected_inputs",
  "runner_class",
  "cache_class",
  "command_outcome",
] as const;
const difference = z
  .strictObject({
    counter: counterSchema,
    unit: z.literal("microsecond"),
    scope: z.enum(["waited_children", "reporter_interval"]),
    reduction: safeSigned.nullable(),
    relativeReduction: z
      .strictObject({ numerator: safeSigned, denominator: positiveInteger })
      .nullable(),
  })
  .refine(
    (value) =>
      (value.scope === "reporter_interval") === (value.counter === "elapsed") &&
      (value.relativeReduction === null ||
        (value.reduction !== null && value.relativeReduction.numerator === value.reduction)),
  );

export const reportComparisonSchema: z.ZodType<ReportComparison> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-report-comparison/v1"),
    ok: z.literal(true),
    baseline: retainedReportSchema,
    treatment: retainedReportSchema,
    pairDigest: digest,
    mismatches: z
      .array(z.enum(mismatchOrder))
      .max(mismatchOrder.length)
      .refine((items) =>
        items.every(
          (item, index) =>
            index === 0 ||
            mismatchOrder.indexOf(item) > mismatchOrder.indexOf(items[index - 1] ?? item),
        ),
      ),
    differences: z.array(difference).max(3),
    coverageStatus: z.literal("not_verified"),
    causalStatus: z.literal("not_established"),
  })
  .superRefine((value, context) => {
    if (
      value.mismatches.length > 0
        ? value.differences.length !== 0
        : value.differences.length !== 3 ||
          value.differences.some((item, index) => item.counter !== COUNTER_ORDER[index])
    ) {
      context.addIssue({
        code: "custom",
        message: "comparison decision and differences contradict",
      });
    }
    for (const [index, item] of value.differences.entries()) {
      const before = value.baseline.payload.measurements[index]?.value;
      const after = value.treatment.payload.measurements[index]?.value;
      const known =
        before !== null && before !== undefined && after !== null && after !== undefined;
      if (
        known
          ? item.reduction !== before - after ||
            (before === 0
              ? item.relativeReduction !== null
              : item.relativeReduction?.denominator !== before)
          : item.reduction !== null || item.relativeReduction !== null
      ) {
        context.addIssue({
          code: "custom",
          message: "difference is not bound to its report counters",
        });
      }
    }
  });

export const reportBudgetSchema: z.ZodType<ReportBudget> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-report-budget/v1"),
    ok: z.literal(true),
    report: retainedReportSchema,
    measurement: reportMeasurementSchema,
    maximumUs: safeUnsigned,
    thresholdAuthority: z.literal("caller_supplied"),
    evaluationWindow: z.literal("exact_report"),
    outcome: z.enum(["breached", "within_budget", "insufficient_evidence"]),
  })
  .refine((value) => {
    const counter = value.report.payload.measurements.find(
      (item) => item.counter === value.measurement.counter,
    );
    return (
      counter !== undefined &&
      counter.value === value.measurement.value &&
      counter.unavailableReason === value.measurement.unavailableReason &&
      (counter.value === null
        ? value.outcome === "insufficient_evidence"
        : value.outcome !== "insufficient_evidence")
    );
  }, "budget result is not bound to the retained measurement");
