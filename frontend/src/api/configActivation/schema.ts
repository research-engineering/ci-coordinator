import { z } from "zod";
import type { components } from "../generated";

export type ConfigActivation = components["schemas"]["ConfigActivationAcceptedBody"];
type ConfigControlError = components["schemas"]["ConfigControlErrorBody"];

export const configActivationSchema: z.ZodType<ConfigActivation> = z.strictObject({
  duplicate: z.boolean(),
  epochId: z.string().regex(/^[0-9a-f]{64}$/),
  ok: z.literal(true),
  revision: z.number().int().safe().positive(),
  schemaVersion: z.literal("ci-config-epoch-activation-result/v1"),
});

export const configControlErrorSchema: z.ZodType<ConfigControlError> = z.strictObject({
  diagnostics: z.array(
    z.strictObject({
      code: z.string(),
      instancePointer: z.string(),
      phase: z.enum([
        "compile",
        "decode",
        "feasibility",
        "parse",
        "semantics",
        "source",
        "structure",
      ]),
      ruleId: z.string(),
    }),
  ),
  error: z.enum([
    "attestation_invalid",
    "conflict",
    "coverage_reducing",
    "coverage_unproven",
    "forbidden",
    "invalid_config",
    "overloaded",
    "revision_conflict",
    "target_unavailable",
    "unauthenticated",
    "unavailable",
  ]),
  ok: z.literal(false),
});

export type ConfigControlErrorCode = ConfigControlError["error"];
