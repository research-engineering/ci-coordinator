import { z } from "zod";
import type { components } from "../generated";
import { archiveDetailSchema } from "./archiveRecordSchema";
import { observationCounter, observationInstant, observationTimestamp } from "./observationSchema";
import { digest, positiveInteger, repositoryScopeSchema } from "./sourceSchema";

const keySchema = z.strictObject({ workflowRunId: positiveInteger, runAttempt: positiveInteger });
export const retentionSelectionSchema = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    generation: positiveInteger,
    configurationRevision: positiveInteger,
    dataRevision: positiveInteger,
    defaultRevision: positiveInteger,
    importedThrough: observationTimestamp,
    action: z.enum(["apply_policy", "erase_details"]),
    keys: z.array(keySchema).min(1).max(100),
  })
  .refine(({ keys }) =>
    keys.every((key, index) => {
      const previous = keys[index - 1];
      return (
        previous === undefined ||
        key.workflowRunId > previous.workflowRunId ||
        (key.workflowRunId === previous.workflowRunId && key.runAttempt > previous.runAttempt)
      );
    }),
  );

const effectSchema = z.strictObject({
  key: keySchema,
  before: archiveDetailSchema,
  after: archiveDetailSchema,
  payloadBytes: observationCounter.max(8388608),
  deletePayload: z.boolean(),
});
const previewSchema = z
  .strictObject({
    selection: retentionSelectionSchema,
    effects: z.array(effectSchema).min(1).max(100),
    releasedBytes: observationCounter.max(838860800),
    deletedDetails: observationCounter.max(100),
    statisticsPreserved: z.literal(true),
  })
  .refine(
    (preview) =>
      JSON.stringify(preview.effects.map((effect) => effect.key)) ===
        JSON.stringify(preview.selection.keys) &&
      preview.releasedBytes ===
        preview.effects.reduce(
          (sum, effect) => sum + (effect.deletePayload ? effect.payloadBytes : 0),
          0,
        ) &&
      preview.deletedDetails === preview.effects.filter((effect) => effect.deletePayload).length &&
      preview.effects.every(
        (effect) => effect.before.firstImportedAt === effect.after.firstImportedAt,
      ),
  );

export const retentionPreviewSchema = z.strictObject({
  preview: previewSchema,
  reviewedDigest: digest,
}) satisfies z.ZodType<components["schemas"]["HistoryRetentionPreviewResponse"]>;
export const retentionRequestSchema = z.strictObject({
  selection: retentionSelectionSchema,
  reviewedDigest: digest,
  operationId: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/),
});
export const retentionResultSchema = z
  .strictObject({
    outcome: z.enum([
      "committed",
      "replayed",
      "revision_conflict",
      "operation_conflict",
      "review_conflict",
    ]),
    operationId: z.string(),
    preview: previewSchema.nullable(),
    dataRevision: positiveInteger.nullable(),
  })
  .refine(
    (value) =>
      ["committed", "replayed"].includes(value.outcome) === (value.preview !== null) &&
      (value.preview === null) === (value.dataRevision === null),
  ) satisfies z.ZodType<components["schemas"]["HistoryRetentionResultResponse"]>;
export type RetentionSelection = z.infer<typeof retentionSelectionSchema>;
export type RetentionPreview = z.infer<typeof retentionPreviewSchema>;
export type RetentionRequest = z.infer<typeof retentionRequestSchema>;
export type RetentionResult = z.infer<typeof retentionResultSchema>;

export function sameRetentionSelection(
  left: RetentionSelection,
  right: RetentionSelection,
): boolean {
  return (
    JSON.stringify({ ...left, importedThrough: observationInstant(left.importedThrough) }) ===
    JSON.stringify({ ...right, importedThrough: observationInstant(right.importedThrough) })
  );
}
