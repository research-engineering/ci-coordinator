import { z } from "zod";
import type { components } from "../generated";

export type ConfigScope = components["schemas"]["RepositoryScopeResponse"];
export type ConfigValidation = components["schemas"]["ConfigEpochValidationAcceptedBody"];
export type ConfigStatus = components["schemas"]["ConfigEpochStatusBody"];
export type ConfigEpoch = components["schemas"]["ConfigEpochSummaryBody"];
export type ConfigRegistration = components["schemas"]["ConfigEpochAcceptedBody"];
export type ConfigRollback = components["schemas"]["ConfigActivationAcceptedBody"];
export type ConfigError = components["schemas"]["ConfigControlErrorBody"];
export type SourceFormat = ConfigEpoch["sourceFormat"];

export const MAX_SOURCE_BYTES = 2_097_152;
export const MAX_REASON_BYTES = 512;
const hash = z.string().regex(/^[0-9a-f]{64}$/);
const positive = z.number().int().safe().positive();
const resourceId = z.string().min(1).max(4096).refine(utf8Fits(4096));

export function utf8Fits(maximum: number) {
  return (text: string) => text.isWellFormed() && new TextEncoder().encode(text).length <= maximum;
}

export const sourceSchema = z.strictObject({
  source: z.string().min(1).max(MAX_SOURCE_BYTES).refine(utf8Fits(MAX_SOURCE_BYTES)),
  sourceFormat: z.enum(["json", "yaml-1.2"]),
});

export const scopeSchema: z.ZodType<ConfigScope> = z.strictObject({
  installationId: positive,
  repositoryId: positive,
});

export const validationSchema: z.ZodType<ConfigValidation> = z.strictObject({
  schemaVersion: z.literal("ci-config-epoch-validation-result/v1"),
  ok: z.literal(true),
  installationId: positive,
  repositoryId: positive,
  epochId: hash,
  sourceHash: hash,
  documentHash: hash,
  epochHash: hash,
  documentSchemaId: resourceId,
  documentProfileId: resourceId,
  semanticProfileId: resourceId,
  compiledSchemaId: resourceId,
});

export const statusSchema: z.ZodType<ConfigStatus> = z.strictObject({
  schemaVersion: z.literal("ci-config-epoch-status/v1"),
  installationId: positive,
  repositoryId: positive,
  active: z.strictObject({ epochId: hash, revision: positive }).nullable(),
  epochs: z
    .array(
      z.strictObject({
        epochId: hash,
        sourceFormat: z.enum(["json", "yaml-1.2"]),
        sourceHash: hash,
        documentHash: hash,
        epochHash: hash,
        sourceByteCount: positive.max(MAX_SOURCE_BYTES),
      }),
    )
    .max(100),
  nextCursor: hash.nullable(),
});

export const registrationSchema: z.ZodType<ConfigRegistration> = z.strictObject({
  schemaVersion: z.literal("ci-config-epoch-registration-result/v1"),
  ok: z.literal(true),
  epochId: hash,
  duplicate: z.boolean(),
});

export const rollbackSchema: z.ZodType<ConfigRollback> = z.strictObject({
  schemaVersion: z.literal("ci-config-epoch-activation-result/v1"),
  ok: z.literal(true),
  epochId: hash,
  duplicate: z.boolean(),
  revision: positive,
});

export const rollbackCommandSchema: z.ZodType<components["schemas"]["ConfigEpochRollbackBody"]> =
  z.strictObject({
    schemaVersion: z.literal("ci-config-epoch-rollback/v1"),
    installationId: positive,
    repositoryId: positive,
    targetEpochId: hash,
    expectedRevision: positive.max(Number.MAX_SAFE_INTEGER - 1),
    operationId: z
      .string()
      .min(1)
      .max(256)
      .refine(utf8Fits(256))
      .refine((s) => !s.includes("\0")),
    reason: z
      .string()
      .min(1)
      .max(MAX_REASON_BYTES)
      .refine(utf8Fits(MAX_REASON_BYTES))
      .refine((s) => s.trim().length > 0 && !s.includes("\0")),
  });

export const errorSchema: z.ZodType<ConfigError> = z.strictObject({
  ok: z.literal(false),
  error: z.enum([
    "unauthenticated",
    "forbidden",
    "invalid_config",
    "conflict",
    "revision_conflict",
    "target_unavailable",
    "attestation_invalid",
    "coverage_reducing",
    "coverage_unproven",
    "overloaded",
    "unavailable",
  ]),
  diagnostics: z.array(
    z.strictObject({
      code: z.string(),
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
      instancePointer: z.string(),
    }),
  ),
});
