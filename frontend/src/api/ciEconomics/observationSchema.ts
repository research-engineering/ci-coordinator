import { z } from "zod";
import type { components, operations } from "../generated";
import { canonicalUtcInstant } from "./schema";
import { positiveInteger, repositoryScopeSchema } from "./sourceSchema";

export type ObservationConfiguration = components["schemas"]["ObservationConfigurationPayload"];
export type ObservationSnapshot = components["schemas"]["ObservationSnapshotPayload"];
export type ObservationCommand =
  operations["configure_ci_observation"]["requestBody"]["content"]["application/json"];
export type ObservationMutation = components["schemas"]["ObservationMutationResponse"];

export function observationInstant(value: string): string | undefined {
  return canonicalUtcInstant(value.endsWith("+00:00") ? `${value.slice(0, -6)}Z` : value);
}

export function observationMicroseconds(value: string): bigint | undefined {
  const instant = observationInstant(value);
  if (instant === undefined) return undefined;
  return BigInt(Date.parse(`${instant.slice(0, 19)}Z`)) * 1000n + BigInt(instant.slice(20, 26));
}

export function observationPayloadInstant(value: string): string | undefined {
  const instant = observationInstant(value);
  if (instant === undefined) return undefined;
  const fraction = instant.slice(20, 26);
  return `${instant.slice(0, 19)}${fraction === "000000" ? "" : `.${fraction}`}+00:00`;
}

export const observationTimestamp = z
  .string()
  .max(32)
  .refine((value) => observationInstant(value) !== undefined);
const operationId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/);
export const observationCounter = z.number().int().safe().nonnegative();
export const observationLane = z.enum(["recent", "backfill"]);

export const observationConfigurationSchema: z.ZodType<ObservationConfiguration> = z.strictObject({
  enabled: z.boolean(),
  selector: z
    .strictObject({
      kind: z.enum(["all", "selected"]),
      workflowIds: z.array(positiveInteger).min(1).max(32).nullable(),
    })
    .refine((selector) =>
      selector.kind === "all"
        ? selector.workflowIds === null
        : selector.workflowIds !== null &&
          new Set(selector.workflowIds).size === selector.workflowIds.length,
    ),
  backfillDays: z.number().int().min(0).max(6),
});

export const observationSnapshotSchema: z.ZodType<ObservationSnapshot> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    schemaVersion: z.literal("ci-economics-observation/v1"),
    revision: positiveInteger,
    configuration: observationConfigurationSchema,
    configuredAt: observationTimestamp,
  })
  .refine((snapshot) => {
    const ids = snapshot.configuration.selector.workflowIds;
    return ids === null || ids.every((id, index) => index === 0 || id > (ids[index - 1] ?? id));
  });

export const observationCommandSchema: z.ZodType<ObservationCommand> = z.strictObject({
  ...repositoryScopeSchema.shape,
  expectedRevision: observationCounter.max(Number.MAX_SAFE_INTEGER - 1),
  configuration: observationConfigurationSchema,
  operationId,
});

export const observationMutationSchema: z.ZodType<ObservationMutation> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-observation-mutation/v1"),
    operationId,
    outcome: z.enum([
      "committed",
      "replayed",
      "revision_conflict",
      "operation_conflict",
      "capacity_reached",
    ]),
    snapshot: observationSnapshotSchema.nullable(),
  })
  .refine(
    (result) =>
      (result.outcome === "committed" || result.outcome === "replayed") ===
      (result.snapshot !== null),
  );

export function sameObservationConfiguration(
  left: ObservationConfiguration,
  right: ObservationConfiguration,
): boolean {
  const leftIds = left.selector.workflowIds;
  const rightIds = right.selector.workflowIds;
  return (
    left.enabled === right.enabled &&
    left.backfillDays === right.backfillDays &&
    left.selector.kind === right.selector.kind &&
    (leftIds === null || rightIds === null
      ? leftIds === rightIds
      : leftIds.length === rightIds.length && leftIds.every((id) => rightIds.includes(id)))
  );
}
