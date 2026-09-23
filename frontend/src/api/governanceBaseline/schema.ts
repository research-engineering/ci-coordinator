import { z } from "zod";
import type { components } from "../generated";
import { governanceRepositorySchema, governanceRulesSchema } from "../governanceObservation/schema";
import { governanceBaselineReasonIsAdmitted } from "./reason";

export type GovernanceBaselineApproval =
  components["schemas"]["GovernanceBaselineApprovalResponse"];
export type GovernanceBaselineError = components["schemas"]["GovernanceBaselineErrorResponse"];
export type GovernanceBaselinePointer = components["schemas"]["GovernanceBaselinePointerResponse"];
export type GovernanceBaselineRead = components["schemas"]["GovernanceBaselineReadResponse"];
export type GovernanceBaselineRecord = components["schemas"]["GovernanceBaselineRecordResponse"];

const positiveInteger = z.number().int().safe().positive();
const sha256 = z.string().regex(/^[0-9a-f]{64}$/);
const baselineId = z.string().regex(/^governance-baseline:[0-9a-f]{64}$/);
const scope = z.strictObject({
  installationId: positiveInteger,
  repositoryId: positiveInteger,
});

export const governanceBaselinePointerSchema: z.ZodType<GovernanceBaselinePointer> = z.strictObject(
  {
    baselineId,
    stateDigest: sha256,
    version: positiveInteger,
  },
);

const governanceBaselineStateSchema = z.strictObject({
  apiVersion: boundedText(64, true),
  repository: governanceRepositorySchema,
  rules: governanceRulesSchema,
  stateDigest: sha256,
});

export const governanceBaselineRecordSchema: z.ZodType<GovernanceBaselineRecord> = z
  .strictObject({
    actor: boundedText(256, false),
    approvedAt: z.iso.datetime({ offset: true }),
    auditEventId: z.string().regex(/^audit_[0-9a-f]{32}$/),
    authority: z.literal("approved_expected_state"),
    observedAt: z.iso.datetime({ offset: true }),
    operationId: boundedText(256, false),
    pointer: governanceBaselinePointerSchema,
    reason: z.string().refine(governanceBaselineReasonIsAdmitted, "reason is not canonical"),
    state: governanceBaselineStateSchema,
    supersedes: governanceBaselinePointerSchema.nullable(),
  })
  .superRefine((value, context) => {
    if (
      value.pointer.stateDigest !== value.state.stateDigest ||
      Date.parse(value.approvedAt) < Date.parse(value.observedAt)
    ) {
      context.addIssue({ code: "custom", message: "baseline record identity is inconsistent" });
    }
    if (
      (value.pointer.version === 1) !== (value.supersedes === null) ||
      (value.supersedes !== null && value.supersedes.version + 1 !== value.pointer.version)
    ) {
      context.addIssue({ code: "custom", message: "baseline predecessor is inconsistent" });
    }
  });

export const governanceBaselineReadSchema: z.ZodType<GovernanceBaselineRead> = z
  .strictObject({
    baseline: governanceBaselineRecordSchema.nullable(),
    ok: z.literal(true),
    scope,
    state: z.enum(["active", "absent"]),
  })
  .superRefine((value, context) => {
    if ((value.state === "active") !== (value.baseline !== null)) {
      context.addIssue({ code: "custom", message: "baseline read state is inconsistent" });
    }
    if (value.baseline && !sameScope(value.scope, value.baseline.state.repository.scope)) {
      context.addIssue({ code: "custom", message: "baseline read crosses repository scope" });
    }
  });

export const governanceBaselineApprovalSchema: z.ZodType<GovernanceBaselineApproval> = z
  .strictObject({
    baseline: governanceBaselineRecordSchema,
    ok: z.literal(true),
    requestOperationId: boundedText(256, false),
    scope,
    state: z.enum(["accepted", "duplicate", "unchanged"]),
  })
  .superRefine((value, context) => {
    if (
      !sameScope(value.scope, value.baseline.state.repository.scope) ||
      (value.state !== "unchanged" && value.baseline.operationId !== value.requestOperationId)
    ) {
      context.addIssue({ code: "custom", message: "baseline approval identity is inconsistent" });
    }
  });

export const governanceBaselineErrorSchema: z.ZodType<GovernanceBaselineError> = z.strictObject({
  error: z.enum([
    "unauthenticated",
    "forbidden",
    "stale",
    "baseline_conflict",
    "operation_conflict",
    "overloaded",
    "unavailable",
  ]),
  ok: z.literal(false),
});

function boundedText(maximumBytes: number, canonical: boolean) {
  return z
    .string()
    .min(1)
    .max(maximumBytes)
    .refine((value) => !value.includes("\0"), "text contains NUL")
    .refine(
      (value) =>
        !Array.from(value).some((character) => {
          const codePoint = character.codePointAt(0);
          return codePoint !== undefined && codePoint >= 0xd800 && codePoint <= 0xdfff;
        }),
      "text contains a lone surrogate",
    )
    .refine((value) => !canonical || value === value.trim(), "text is not canonical")
    .refine(
      (value) => new TextEncoder().encode(value).byteLength <= maximumBytes,
      "text exceeds its byte bound",
    );
}

function sameScope(
  left: { readonly installationId: number; readonly repositoryId: number },
  right: { readonly installationId: number; readonly repositoryId: number },
): boolean {
  return left.installationId === right.installationId && left.repositoryId === right.repositoryId;
}
