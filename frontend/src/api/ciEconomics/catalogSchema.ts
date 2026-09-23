import { z } from "zod";
import type { operations } from "../generated";
import { canonicalUtcInstant } from "./schema";
import {
  digest,
  type ProviderSource,
  positiveInteger,
  providerSourceSchema,
  repositoryScopeSchema,
  sameScope,
  utcInstant,
} from "./sourceSchema";

export type SourcePage =
  operations["list_ci_economics_provider_sources"]["responses"][200]["content"]["application/json"];
export type SourceItem = SourcePage["items"][number];
export type ReportPage =
  operations["list_ci_measurement_reports"]["responses"][200]["content"]["application/json"];
export type ReportPointer = ReportPage["items"][number];
export const CATALOG_PAGE_SIZE = 20;
export const sourceCursorSchema = z
  .string()
  .regex(/^[1-9][0-9]{0,15}\.[1-9][0-9]{0,15}$/)
  .refine((value) =>
    value.split(".").every((part) => positiveInteger.safeParse(Number(part)).success),
  );
const failureReason = z.enum([
  "provider_unavailable",
  "provider_binding_mismatch",
  "provider_malformed",
  "provider_incomplete",
  "provider_not_terminal",
  "provider_unstable",
  "unexpected_error",
  "evidence_conflict",
]);

const sourceItemSchema: z.ZodType<SourceItem> = z
  .strictObject({
    source: providerSourceSchema,
    status: z.enum(["pending", "leased", "deferred", "captured", "terminal_unavailable"]),
    attemptCount: z.number().int().min(0).max(100),
    maxAttempts: z.number().int().min(1).max(100),
    nextAttemptAt: utcInstant.nullable(),
    lastFailureReason: failureReason.nullable(),
    terminalReason: z
      .enum(["deadline_exceeded", "attempts_exhausted", "evidence_conflict"])
      .nullable(),
    completedAt: utcInstant.nullable(),
    retainUntil: utcInstant,
  })
  .superRefine((value, context) => {
    const terminal = value.status === "captured" || value.status === "terminal_unavailable";
    const scheduled = value.status === "pending" || value.status === "deferred";
    const retained = canonicalUtcInstant(value.retainUntil);
    const created = canonicalUtcInstant(value.source.runCreatedAt);
    const completed =
      value.completedAt === null ? undefined : canonicalUtcInstant(value.completedAt);
    const next =
      value.nextAttemptAt === null ? undefined : canonicalUtcInstant(value.nextAttemptAt);
    if (
      value.attemptCount > value.maxAttempts ||
      !retained ||
      !created ||
      created >= retained ||
      scheduled !== (value.nextAttemptAt !== null) ||
      terminal !== (value.completedAt !== null) ||
      (value.status === "terminal_unavailable") !== (value.terminalReason !== null) ||
      (value.status === "pending" &&
        (value.attemptCount !== 0 || value.lastFailureReason !== null)) ||
      (["leased", "deferred", "captured"].includes(value.status) && value.attemptCount === 0) ||
      (value.status === "deferred" && value.lastFailureReason === null) ||
      (completed !== undefined &&
        (completed < created || (value.status === "captured" && completed >= retained))) ||
      (next !== undefined && (next < created || next >= retained)) ||
      (value.lastFailureReason === "evidence_conflict") !==
        (value.terminalReason === "evidence_conflict") ||
      (value.terminalReason === "evidence_conflict" && value.attemptCount === 0) ||
      (value.terminalReason === "attempts_exhausted" && value.attemptCount !== value.maxAttempts)
    ) {
      context.addIssue({ code: "custom", message: "source collection projection is inconsistent" });
    }
  });

export const sourcePageSchema: z.ZodType<SourcePage> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    schemaVersion: z.literal("ci-economics-source-page/v2"),
    ok: z.literal(true),
    items: z.array(sourceItemSchema).max(100),
    nextCursor: sourceCursorSchema.nullable(),
  })
  .superRefine((page, context) => {
    const last = page.items.at(-1);
    if (
      page.items.some((item, index) => {
        const previous = page.items[index - 1];
        return (
          !sameScope(page, item.source.attempt) ||
          (previous !== undefined && !sourceIsBefore(item.source, sourceCursor(previous.source)))
        );
      }) ||
      (page.nextCursor !== null && (!last || page.nextCursor !== sourceCursor(last.source)))
    ) {
      context.addIssue({
        code: "custom",
        message: "source page scope, order or continuation is inconsistent",
      });
    }
  });

export const reportPointerSchema: z.ZodType<ReportPointer> = z
  .strictObject({
    reportId: digest,
    reportDigest: digest,
    receivedAt: utcInstant,
    retainUntil: utcInstant,
  })
  .refine((value) => {
    const received = canonicalUtcInstant(value.receivedAt);
    const retained = canonicalUtcInstant(value.retainUntil);
    return received !== undefined && retained !== undefined && received < retained;
  }, "report pointer retention is inconsistent");

export const reportPageSchema: z.ZodType<ReportPage> = z
  .strictObject({
    schemaVersion: z.literal("ci-measurement-report-page/v2"),
    ok: z.literal(true),
    source: providerSourceSchema,
    items: z.array(reportPointerSchema).max(100),
    nextCursor: digest.nullable(),
  })
  .superRefine((page, context) => {
    if (
      page.items.some((item, index) => {
        const previous = page.items[index - 1];
        return (
          previous !== undefined &&
          (previous.reportId >= item.reportId ||
            canonicalUtcInstant(previous.retainUntil) !== canonicalUtcInstant(item.retainUntil))
        );
      }) ||
      (page.nextCursor !== null && page.nextCursor !== page.items.at(-1)?.reportId)
    ) {
      context.addIssue({
        code: "custom",
        message: "report page order, retention or continuation is inconsistent",
      });
    }
  });

export function sourceCursor(source: ProviderSource): string {
  return `${source.attempt.workflowRunId}.${source.attempt.runAttempt}`;
}

export function sourceIsBefore(source: ProviderSource, cursor: string): boolean {
  const [run, attempt] = cursor.split(".").map(Number);
  return (
    run !== undefined &&
    attempt !== undefined &&
    (source.attempt.workflowRunId < run ||
      (source.attempt.workflowRunId === run && source.attempt.runAttempt < attempt))
  );
}
