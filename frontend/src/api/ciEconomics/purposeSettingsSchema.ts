import { z } from "zod";
import { purposeMappingSchema } from "./analyticsSchema";
import { positiveInteger, repositoryScopeSchema, sameScope } from "./sourceSchema";

export const purposeSettingsQuerySchema = z.strictObject({
  ...repositoryScopeSchema.shape,
  generation: positiveInteger,
});
export const purposeSettingsSnapshotSchema = z
  .strictObject({
    ...purposeSettingsQuerySchema.shape,
    revision: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER),
    mapping: purposeMappingSchema.nullable(),
  })
  .refine((value) =>
    value.mapping === null
      ? value.revision === 0
      : value.revision > 0 &&
        sameScope(value, value.mapping) &&
        value.generation === value.mapping.generation &&
        value.mapping.version === `repository-settings:${value.revision}` &&
        value.mapping.provenance === "administrator-api/v1",
  );
export const purposeSettingsCommandSchema = z
  .strictObject({
    ...purposeSettingsQuerySchema.shape,
    expectedRevision: z
      .number()
      .int()
      .min(0)
      .max(Number.MAX_SAFE_INTEGER - 1),
    operationId: z
      .string()
      .min(1)
      .max(128)
      .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]*$/),
    entries: purposeMappingSchema.shape.entries,
  })
  .refine((value) => new TextEncoder().encode(JSON.stringify(value)).length <= 262144);
export const purposeSettingsReadSchema = z.discriminatedUnion("outcome", [
  z.strictObject({
    outcome: z.literal("available"),
    snapshot: purposeSettingsSnapshotSchema,
    unavailable: z.null(),
  }),
  z.strictObject({
    outcome: z.literal("unavailable"),
    snapshot: z.null(),
    unavailable: z.strictObject({
      reason: z.enum(["dataset_unavailable", "generation_changed", "snapshot_changed"]),
    }),
  }),
]);
export const purposeSettingsWriteSchema = z
  .strictObject({
    operationId: z.string().min(1).max(128),
    outcome: z.enum([
      "committed",
      "replayed",
      "revision_conflict",
      "operation_conflict",
      "generation_changed",
      "dataset_fenced",
    ]),
    snapshot: purposeSettingsSnapshotSchema.nullable(),
  })
  .refine(
    (value) => (value.snapshot !== null) === ["committed", "replayed"].includes(value.outcome),
  );

export type PurposeSettingsQuery = z.infer<typeof purposeSettingsQuerySchema>;
export type PurposeSettingsSnapshot = z.infer<typeof purposeSettingsSnapshotSchema>;
export type PurposeSettingsCommand = z.infer<typeof purposeSettingsCommandSchema>;
export type PurposeSettingsRead = z.infer<typeof purposeSettingsReadSchema>;
export type PurposeSettingsWrite = z.infer<typeof purposeSettingsWriteSchema>;
