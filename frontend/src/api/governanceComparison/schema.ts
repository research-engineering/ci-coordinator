import { z } from "zod";
import type { components } from "../generated";
import { governanceBaselineRecordSchema } from "../governanceBaseline/schema";
import { governanceObservationSchema } from "../governanceObservation/schema";

export type GovernanceComparison = components["schemas"]["GovernanceComparisonResponse"];
export type GovernanceComparisonError = components["schemas"]["GovernanceComparisonErrorResponse"];

const positiveInteger = z.number().int().safe().positive();
const nonNegativeInteger = z.number().int().min(0).max(1_000);
const sha256 = z.string().regex(/^[0-9a-f]{64}$/);
const coordinate = z.enum([
  "api_version",
  "repository.owner_id",
  "repository.owner",
  "repository.name",
  "repository.full_name",
  "repository.default_branch",
  "rules",
]);
const coordinateOrder = coordinate.options;
const scope = z.strictObject({
  installationId: positiveInteger,
  repositoryId: positiveInteger,
});

const exactComparisonSchema = z
  .strictObject({
    addedRuleCount: nonNegativeInteger,
    baselineStateDigest: sha256,
    changedCoordinates: z.array(coordinate).max(coordinateOrder.length),
    currentStateDigest: sha256,
    relation: z.enum(["matches", "differs"]),
    removedRuleCount: nonNegativeInteger,
  })
  .superRefine((value, context) => {
    const expected = coordinateOrder.filter((item) => value.changedCoordinates.includes(item));
    const rulesChanged = value.changedCoordinates.includes("rules");
    const equalProjection =
      value.changedCoordinates.length === 0 &&
      value.baselineStateDigest === value.currentStateDigest;
    if (
      !sameArray(value.changedCoordinates, expected) ||
      rulesChanged !== (value.addedRuleCount > 0 || value.removedRuleCount > 0) ||
      (value.relation === "matches" ? !equalProjection : value.changedCoordinates.length === 0)
    ) {
      context.addIssue({ code: "custom", message: "comparison projection is inconsistent" });
    }
  });

export const governanceComparisonSchema: z.ZodType<GovernanceComparison> = z
  .strictObject({
    baseline: governanceBaselineRecordSchema.nullable(),
    comparison: exactComparisonSchema.nullable(),
    observation: governanceObservationSchema,
    ok: z.literal(true),
    scope,
    state: z.enum(["unbaselined", "compared"]),
  })
  .superRefine((value, context) => {
    const compared = value.state === "compared";
    if (
      !sameScope(value.scope, value.observation.repository.scope) ||
      compared !== (value.baseline !== null && value.comparison !== null) ||
      (value.state === "unbaselined" && (value.baseline !== null || value.comparison !== null))
    ) {
      context.addIssue({ code: "custom", message: "comparison response shape is inconsistent" });
      return;
    }
    if (
      value.baseline &&
      value.comparison &&
      (!sameScope(value.scope, value.baseline.state.repository.scope) ||
        value.baseline.pointer.stateDigest !== value.comparison.baselineStateDigest ||
        value.observation.stateDigest !== value.comparison.currentStateDigest)
    ) {
      context.addIssue({ code: "custom", message: "comparison evidence identity is inconsistent" });
    }
  });

export const governanceComparisonErrorSchema: z.ZodType<GovernanceComparisonError> = z
  .strictObject({
    error: z.enum([
      "unauthenticated",
      "forbidden",
      "stale",
      "unavailable",
      "rate_limited",
      "not_found",
      "malformed_provider_response",
      "provider_binding_mismatch",
      "observation_limit_exceeded",
    ]),
    ok: z.literal(false),
    retryAfterSeconds: z.number().int().min(0).max(3_600).nullable(),
  })
  .superRefine((value, context) => {
    if (value.error !== "rate_limited" && value.retryAfterSeconds !== null) {
      context.addIssue({ code: "custom", message: "retry delay requires a rate limit" });
    }
  });

function sameScope(
  left: { readonly installationId: number; readonly repositoryId: number },
  right: { readonly installationId: number; readonly repositoryId: number },
): boolean {
  return left.installationId === right.installationId && left.repositoryId === right.repositoryId;
}

function sameArray<T>(left: readonly T[], right: readonly T[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}
