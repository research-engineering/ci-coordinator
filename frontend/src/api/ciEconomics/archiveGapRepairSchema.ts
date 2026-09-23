import { z } from "zod";
import type { components } from "../generated";
import type { ArchiveRead } from "./archiveReadSchema";
import { digest, positiveInteger, repositoryScopeSchema } from "./sourceSchema";

export const gapRepairRequestSchema = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    generation: positiveInteger,
    expectedRevision: positiveInteger,
    gapIds: z.array(digest.length(64)).min(1).max(50),
    operationId: z
      .string()
      .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/)
      .refine((value) => value.trim() === value),
  })
  .refine(({ gapIds }) =>
    gapIds.every((id, index) => index === 0 || id > (gapIds[index - 1] ?? "")),
  );

const intervalSchema = z
  .strictObject({
    workflowRunId: positiveInteger,
    fromAttempt: positiveInteger,
    throughAttempt: positiveInteger,
  })
  .refine(
    (value) =>
      value.throughAttempt >= value.fromAttempt && value.throughAttempt - value.fromAttempt < 50,
  );
const receiptSchema = z
  .strictObject({
    request: gapRepairRequestSchema,
    intervals: z.array(intervalSchema).min(1).max(50),
  })
  .refine(
    ({ request, intervals }) =>
      intervals.length <= request.gapIds.length &&
      intervals.every(
        (value, index) =>
          index === 0 || value.workflowRunId > (intervals[index - 1]?.workflowRunId ?? 0),
      ) &&
      intervals.reduce((sum, value) => sum + value.throughAttempt - value.fromAttempt + 1, 0) <= 50,
  );

export const gapRepairResultSchema = z
  .strictObject({
    outcome: z.enum([
      "committed",
      "replayed",
      "operation_conflict",
      "revision_conflict",
      "generation_conflict",
      "dataset_fenced",
      "gap_not_found",
      "unsupported_gap",
      "inconsistent_source",
      "selection_too_wide",
      "workflow_unselected",
      "capacity_reached",
    ]),
    operationId: z.string().min(1).max(128),
    receipt: receiptSchema.nullable(),
  })
  .refine(
    (value) =>
      ["committed", "replayed"].includes(value.outcome) === (value.receipt !== null) &&
      (value.receipt === null || value.receipt.request.operationId === value.operationId),
  ) satisfies z.ZodType<components["schemas"]["HistoryGapRepairResult"]>;

export type GapRepairRequest = z.infer<typeof gapRepairRequestSchema>;
export type GapRepairResult = z.infer<typeof gapRepairResultSchema>;
export type GapRepairInterval = z.infer<typeof intervalSchema>;

export function selectedRepairIntervals(
  page: ArchiveRead,
  ids: readonly string[],
): GapRepairInterval[] | null {
  const selected = new Set(ids);
  const byRun = new Map<number, GapRepairInterval>();
  let found = 0;
  for (const gap of page.gaps) {
    if (!selected.has(gap.gapId)) continue;
    if (!gap.retrySupported || gap.workflowRunId === null || gap.runAttempt === null) return null;
    found += 1;
    const prior = byRun.get(gap.workflowRunId);
    byRun.set(gap.workflowRunId, {
      workflowRunId: gap.workflowRunId,
      fromAttempt: Math.min(prior?.fromAttempt ?? gap.runAttempt, gap.runAttempt),
      throughAttempt: Math.max(prior?.throughAttempt ?? gap.runAttempt, gap.runAttempt),
    });
  }
  const intervals = [...byRun.values()].sort((a, b) => a.workflowRunId - b.workflowRunId);
  return found === ids.length &&
    found > 0 &&
    selected.size === ids.length &&
    intervals.reduce((sum, value) => sum + value.throughAttempt - value.fromAttempt + 1, 0) <= 50
    ? intervals
    : null;
}
