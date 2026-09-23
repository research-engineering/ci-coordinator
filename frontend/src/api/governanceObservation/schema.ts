import { z } from "zod";
import type { components } from "../generated";
import { canonicalJsonText } from "./canonicalJson";

export type GovernanceObservation = components["schemas"]["GovernanceObservationResponse"];
export type GovernanceObservationError =
  components["schemas"]["GovernanceObservationErrorResponse"];

const MAX_RULES = 1_000;
const MAX_RULE_BYTES = 1_048_576;
const MAX_AGGREGATE_RULE_BYTES = 2_097_152;
const MAX_DEFAULT_BRANCH_BYTES = 512;
const JSON_LIMITS = { maximumDepth: 16, maximumNodes: 16_384 } as const;
const encoder = new TextEncoder();
const positiveInteger = z.number().int().safe().positive();
const forbiddenGitBranchCharacters = new Set([" ", "~", "^", ":", "?", "*", "[", "\\"]);
const repositoryComponent = boundedText(512).refine(
  (value) => value !== "." && value !== ".." && !value.includes("/") && !value.includes("\0"),
  "repository component is not canonical",
);
const gitBranchName = boundedText(MAX_DEFAULT_BRANCH_BYTES).refine(
  isGitBranchName,
  "default branch is not a canonical Git branch name",
);

export const effectiveGovernanceRuleSchema = z
  .strictObject({
    canonicalJson: z.string().min(2),
    ruleType: boundedText(256),
    rulesetId: positiveInteger,
    rulesetSource: boundedText(1_025),
    rulesetSourceType: boundedText(256),
  })
  .superRefine((value, context) => {
    if (encoder.encode(value.canonicalJson).byteLength > MAX_RULE_BYTES) {
      context.addIssue({ code: "custom", message: "rule exceeds its canonical byte bound" });
      return;
    }
    let decoded: unknown;
    try {
      decoded = JSON.parse(value.canonicalJson) as unknown;
      if (!isRecord(decoded) || canonicalJsonText(decoded, JSON_LIMITS) !== value.canonicalJson) {
        throw new TypeError("rule is not one canonical JSON object");
      }
    } catch {
      context.addIssue({ code: "custom", message: "rule is not admitted canonical JSON" });
      return;
    }
    if (
      decoded["type"] !== value.ruleType ||
      decoded["ruleset_source_type"] !== value.rulesetSourceType ||
      decoded["ruleset_source"] !== value.rulesetSource ||
      decoded["ruleset_id"] !== value.rulesetId
    ) {
      context.addIssue({ code: "custom", message: "rule metadata contradicts its JSON" });
    }
  });

export const governanceRepositorySchema = z
  .strictObject({
    defaultBranch: gitBranchName,
    fullName: boundedText(1_025),
    name: repositoryComponent,
    owner: repositoryComponent,
    ownerId: positiveInteger,
    scope: z.strictObject({
      installationId: positiveInteger,
      repositoryId: positiveInteger,
    }),
  })
  .superRefine((value, context) => {
    if (value.fullName !== `${value.owner}/${value.name}`) {
      context.addIssue({ code: "custom", message: "repository identity is inconsistent" });
    }
  });

export const governanceRulesSchema = z
  .array(effectiveGovernanceRuleSchema)
  .max(MAX_RULES)
  .superRefine((rules, context) => {
    const encodedRules = rules.map((rule) => encoder.encode(rule.canonicalJson));
    const aggregateBytes = encodedRules.reduce((total, item) => total + item.byteLength, 0);
    if (aggregateBytes > MAX_AGGREGATE_RULE_BYTES) {
      context.addIssue({ code: "custom", message: "rule set exceeds its aggregate byte bound" });
    }
    for (let index = 1; index < encodedRules.length; index += 1) {
      const previous = encodedRules[index - 1];
      const current = encodedRules[index];
      if (!previous || !current || compareBytes(previous, current) >= 0) {
        context.addIssue({
          code: "custom",
          message: "rules are not unique canonical byte order",
        });
        break;
      }
    }
  });

export const governanceObservationSchema: z.ZodType<GovernanceObservation> = z.strictObject({
  apiVersion: z.literal("2026-03-10"),
  baselineState: z.literal("unbaselined"),
  consistency: z.literal("best_effort"),
  observedAt: z.iso.datetime({ offset: true }),
  ok: z.literal(true),
  repository: governanceRepositorySchema,
  rules: governanceRulesSchema,
  stateDigest: z.string().regex(/^[0-9a-f]{64}$/),
});

export const governanceObservationErrorSchema: z.ZodType<GovernanceObservationError> = z
  .strictObject({
    error: z.enum([
      "unauthenticated",
      "forbidden",
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

function boundedText(maximumBytes: number) {
  return z
    .string()
    .min(1)
    .refine((value) => value === value.trim(), "text is not canonical")
    .refine((value) => {
      try {
        canonicalJsonText(value);
        return true;
      } catch {
        return false;
      }
    }, "text contains an unpaired surrogate")
    .refine(
      (value) => encoder.encode(value).byteLength <= maximumBytes,
      "text exceeds its byte bound",
    );
}

function compareBytes(left: Uint8Array, right: Uint8Array): number {
  const sharedLength = Math.min(left.byteLength, right.byteLength);
  for (let index = 0; index < sharedLength; index += 1) {
    const difference = (left[index] ?? 0) - (right[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return left.byteLength - right.byteLength;
}

function isGitBranchName(value: string): boolean {
  const components = value.split("/");
  return !(
    value.startsWith("-") ||
    value.endsWith(".") ||
    value.includes("..") ||
    value.includes("@{") ||
    components.some(
      (component) =>
        component.length === 0 || component.startsWith(".") || component.endsWith(".lock"),
    ) ||
    [...value].some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || codePoint === 127 || forbiddenGitBranchCharacters.has(character);
    })
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
