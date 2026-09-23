import { z } from "zod";
import type { components, operations } from "../generated";
import {
  type CiEconomicsAttemptIdentity,
  canonicalUtcInstant,
  ciEconomicsAttemptIdentitySchema,
  sameAttemptIdentity,
} from "./schema";

export type ProviderSource = components["schemas"]["EconomicsProviderSourceResponse"];
export type SourceDiscovery =
  operations["discover_ci_economics_sources"]["responses"][200]["content"]["application/json"];
export type SourceRegistration =
  operations["register_ci_economics_source"]["responses"][201]["content"]["application/json"];
export type SourceDiscoveryInput =
  operations["discover_ci_economics_sources"]["requestBody"]["content"]["application/json"];

export const positiveInteger = z.number().int().safe().positive();
export const digest = z.string().regex(/^[0-9a-f]{64}$/);
export const utcInstant = z.string().refine((value) => canonicalUtcInstant(value) !== undefined);
const secondInstant = utcInstant.refine((value) => /^.{19}Z$/.test(value));
export const repositoryScopeSchema = z.strictObject({
  installationId: positiveInteger,
  repositoryId: positiveInteger,
});

export const providerSourceSchema: z.ZodType<ProviderSource> = z.strictObject({
  sourceKind: z.literal("provider_run"),
  sourceId: digest,
  attempt: ciEconomicsAttemptIdentitySchema,
  runCreatedAt: utcInstant,
  providerApiVersion: z.iso.date(),
  sourceEvidenceDigest: digest,
});

export const sourceDiscoveryInputSchema: z.ZodType<SourceDiscoveryInput> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    createdFrom: secondInstant,
    createdThrough: secondInstant,
    pageNumber: z.number().int().min(1).max(10),
  })
  .refine((value) => {
    const width = Date.parse(value.createdThrough) - Date.parse(value.createdFrom);
    return width >= 0 && width <= 604_800_000;
  }, "discovery window must span at most seven days");

export const sourceDiscoverySchema: z.ZodType<SourceDiscovery> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    schemaVersion: z.literal("ci-economics-source-discovery/v2"),
    ok: z.literal(true),
    createdFrom: utcInstant,
    createdThrough: utcInstant,
    pageNumber: z.number().int().min(1).max(10),
    providerTotal: z.number().int().safe().nonnegative(),
    termination: z.enum(["next_page", "exhausted", "truncated"]),
    sources: z.array(providerSourceSchema).max(100),
  })
  .superRefine((value, context) => {
    const from = canonicalUtcInstant(value.createdFrom);
    const through = canonicalUtcInstant(value.createdThrough);
    const width = Date.parse(value.createdThrough) - Date.parse(value.createdFrom);
    const end = (value.pageNumber - 1) * 100 + value.sources.length;
    const unique = new Set(value.sources.map((source) => source.attempt.workflowRunId));
    const terminationValid =
      value.termination === "exhausted"
        ? end === value.providerTotal
        : end < value.providerTotal &&
          (value.termination === "truncated" ||
            (value.sources.length === 100 && value.pageNumber < 10));
    if (
      !from ||
      !through ||
      width < 0 ||
      width > 604_800_000 ||
      !from.endsWith(".000000Z") ||
      !through.endsWith(".000000Z") ||
      unique.size !== value.sources.length ||
      !terminationValid ||
      value.sources.some((source) => {
        const created = canonicalUtcInstant(source.runCreatedAt);
        return !sameScope(value, source.attempt) || !created || created < from || created > through;
      })
    ) {
      context.addIssue({
        code: "custom",
        message: "discovery scope, window or population is inconsistent",
      });
    }
  });

export const sourceRegistrationSchema: z.ZodType<SourceRegistration> = z.strictObject({
  schemaVersion: z.literal("ci-economics-source-registration/v2"),
  source: providerSourceSchema,
  outcome: z.enum([
    "registered",
    "replayed",
    "source_conflict",
    "outside_source_window",
    "capacity_reached",
  ]),
});

export function validRepositoryScope(scope: {
  readonly installationId: number;
  readonly repositoryId: number;
}): boolean {
  return (
    positiveInteger.safeParse(scope.installationId).success &&
    positiveInteger.safeParse(scope.repositoryId).success
  );
}

export function sameScope(
  left: { readonly installationId: number; readonly repositoryId: number },
  right: { readonly installationId: number; readonly repositoryId: number },
): boolean {
  return left.installationId === right.installationId && left.repositoryId === right.repositoryId;
}

export function sameSourceIdentity(left: ProviderSource, right: ProviderSource): boolean {
  return left.sourceId === right.sourceId && sameAttemptIdentity(left.attempt, right.attempt);
}

export async function sourceIdentityIsCanonical(source: ProviderSource): Promise<boolean> {
  const attempt: CiEconomicsAttemptIdentity = source.attempt;
  const canonical = JSON.stringify({
    attempt: {
      headSha: attempt.headSha,
      installationId: attempt.installationId,
      repositoryId: attempt.repositoryId,
      runAttempt: attempt.runAttempt,
      workflowRunId: attempt.workflowRunId,
    },
    schemaVersion: "ci-economics-provider-run-source/v1",
  });
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return (
    Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("") ===
    source.sourceId
  );
}
