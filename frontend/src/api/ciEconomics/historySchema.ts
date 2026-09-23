import { z } from "zod";
import type { components, operations } from "../generated";
import { observationCounter, observationInstant, observationTimestamp } from "./observationSchema";
import { positiveInteger, repositoryScopeSchema } from "./sourceSchema";

export type HistoryConfiguration = components["schemas"]["HistoryConfiguration"];
export type HistoryDataset = components["schemas"]["HistoryDatasetPayload"];
export type HistoryCommand =
  operations["configure_ci_history"]["requestBody"]["content"]["application/json"];
export type HistoryMutation = components["schemas"]["HistoryMutationResponse"];
export type DetailRetention = components["schemas"]["DetailRetentionPayload"];

export const detailRetentionSchema: z.ZodType<DetailRetention> = z.discriminatedUnion("mode", [
  z.strictObject({ mode: z.literal("disabled") }),
  z.strictObject({ mode: z.literal("forever") }),
  z.strictObject({
    mode: z.literal("days"),
    days: z.number().int().min(1).max(36_500),
    anchor: z.literal("first_successful_detail_import"),
  }),
]);
const operationId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/);
const counters = {
  attempts: observationCounter,
  jobs: observationCounter,
  gaps: observationCounter,
  canonicalBytes: observationCounter,
};
export const historyConfigurationSchema: z.ZodType<HistoryConfiguration> = z.strictObject({
  enabled: z.boolean(),
  workflowIds: z
    .array(positiveInteger)
    .min(1)
    .max(32)
    .refine((ids) => new Set(ids).size === ids.length)
    .nullable(),
  detailRetention: detailRetentionSchema.nullable(),
  quota: z.strictObject({
    attempts: positiveInteger,
    jobs: positiveInteger,
    gaps: positiveInteger,
    canonicalBytes: positiveInteger,
  }),
});

export const historyDatasetSchema: z.ZodType<HistoryDataset> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    schemaVersion: z.literal("ci-economics-history-dataset/v1"),
    generation: positiveInteger,
    configurationRevision: positiveInteger,
    dataRevision: positiveInteger,
    configuredAt: observationTimestamp,
    state: z.enum(["active", "paused", "erasing", "erased"]),
    configuration: historyConfigurationSchema,
    usage: z.strictObject(counters),
  })
  .refine(
    (value) =>
      value.configuration.enabled === (value.state === "active") &&
      (value.state !== "erased" || Object.values(value.usage).every((count) => count === 0)) &&
      (value.configuration.workflowIds === null ||
        value.configuration.workflowIds.every(
          (id, index, ids) => index === 0 || id > (ids[index - 1] ?? id),
        )),
  );

export const historyCommandSchema: z.ZodType<HistoryCommand> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    expectedRevision: observationCounter.max(Number.MAX_SAFE_INTEGER - 1),
    configuration: historyConfigurationSchema,
    initialCreatedFrom: observationTimestamp
      .refine((value) => observationInstant(value)?.slice(20, 26) === "000000")
      .nullable(),
    rescan: z.boolean(),
    operationId,
  })
  .refine(
    (value) =>
      (value.expectedRevision === 0) === (value.initialCreatedFrom !== null) &&
      (value.expectedRevision !== 0 || !value.rescan),
  );

export const historyMutationSchema: z.ZodType<HistoryMutation> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-history-mutation/v1"),
    operationId,
    outcome: z.enum([
      "committed",
      "replayed",
      "revision_conflict",
      "operation_conflict",
      "capacity_reached",
      "dataset_fenced",
      "invalid_population",
    ]),
    snapshot: historyDatasetSchema.nullable(),
  })
  .refine(
    (result) =>
      (result.outcome === "committed" || result.outcome === "replayed") ===
      (result.snapshot !== null),
  );

export function sameDetailRetention(
  left: DetailRetention | null,
  right: DetailRetention | null,
): boolean {
  if (left === null || right === null) return left === right;
  return (
    left.mode === right.mode &&
    (left.mode !== "days" || (right.mode === "days" && left.days === right.days))
  );
}

export function sameHistoryConfiguration(
  left: HistoryConfiguration,
  right: HistoryConfiguration,
): boolean {
  return (
    left.enabled === right.enabled &&
    sameDetailRetention(left.detailRetention, right.detailRetention) &&
    left.quota.attempts === right.quota.attempts &&
    left.quota.jobs === right.quota.jobs &&
    left.quota.gaps === right.quota.gaps &&
    left.quota.canonicalBytes === right.quota.canonicalBytes &&
    (left.workflowIds === null || right.workflowIds === null
      ? left.workflowIds === right.workflowIds
      : left.workflowIds.length === right.workflowIds.length &&
        left.workflowIds.every((id) => right.workflowIds?.includes(id)))
  );
}
