import { z } from "zod";
import type { components } from "../generated";

export type InstallationCatalog = components["schemas"]["InstallationCatalogResponse"];
export type RepositoryPage = components["schemas"]["RepositoryPageResponse"];
export type ProviderInventoryError = components["schemas"]["ProviderInventoryErrorResponse"];

const safeInteger = z.number().int().safe();
const positiveInteger = safeInteger.positive();
const dateTime = z.iso.datetime({ offset: true });
const providerIdentifier = boundedCanonicalText(512);
const providerBranch = boundedCanonicalText(1_024);
const providerFullName = boundedCanonicalText(1_025);
const inventoryFailureReason = z.enum([
  "unavailable",
  "rate_limited",
  "not_found",
  "malformed_provider_response",
  "provider_binding_mismatch",
]);

export const providerInventoryErrorSchema: z.ZodType<ProviderInventoryError> = z
  .strictObject({
    error: z.enum([
      "unauthenticated",
      "forbidden",
      "unavailable",
      "rate_limited",
      "not_found",
      "malformed_provider_response",
      "provider_binding_mismatch",
      "suspended",
      "unsupported_account_type",
    ]),
    ok: z.literal(false),
    retryAfterSeconds: z.number().int().min(0).max(3_600).nullable(),
  })
  .superRefine((value, context) => {
    if (value.error !== "rate_limited" && value.retryAfterSeconds !== null) {
      context.addIssue({ code: "custom", message: "retry delay requires a rate limit" });
    }
  });

const installation = z
  .strictObject({
    accountId: positiveInteger,
    accountLogin: providerIdentifier,
    accountType: z.enum(["Organization", "User"]),
    installationId: positiveInteger,
    repositorySelection: z.enum(["all", "selected"]),
    state: z.enum(["active", "suspended", "unsupported"]),
  })
  .superRefine((value, context) => {
    if (
      (value.accountType === "Organization" && value.state === "unsupported") ||
      (value.accountType === "User" && value.state === "active")
    ) {
      context.addIssue({ code: "custom", message: "installation state contradicts account type" });
    }
  });

export const installationCatalogSchema: z.ZodType<InstallationCatalog> = z
  .strictObject({
    complete: z.boolean(),
    failures: z
      .array(
        z
          .strictObject({
            installationId: positiveInteger,
            reason: inventoryFailureReason,
            retryAfterSeconds: z.number().int().min(0).max(3_600).nullable(),
          })
          .superRefine((value, context) => {
            if (value.reason !== "rate_limited" && value.retryAfterSeconds !== null) {
              context.addIssue({ code: "custom", message: "retry delay requires a rate limit" });
            }
          }),
      )
      .max(100),
    installations: z.array(installation).max(100),
    page: z.number().int().min(1).max(10_000),
    perPage: z.number().int().min(1).max(100),
    hasNextPage: z.boolean(),
    consistency: z.literal("best_effort"),
    observedAt: dateTime,
    ok: z.literal(true),
  })
  .superRefine((value, context) => {
    if (value.complete !== (value.failures.length === 0)) {
      context.addIssue({ code: "custom", message: "catalog completeness contradicts failures" });
    }
    const itemCount = value.installations.length + value.failures.length;
    if (itemCount > value.perPage) {
      context.addIssue({ code: "custom", message: "catalog exceeds its aggregate item bound" });
    }
    if (value.hasNextPage && (itemCount === 0 || value.page === 10_000)) {
      context.addIssue({ code: "custom", message: "catalog continuation exceeds its bounds" });
    }
    const identities = [
      ...value.installations.map((item) => item.installationId),
      ...value.failures.map((item) => item.installationId),
    ];
    if (new Set(identities).size !== identities.length) {
      context.addIssue({ code: "custom", message: "catalog identities are not unique" });
    }
    if (
      !isAscending(value.installations.map((item) => item.installationId)) ||
      !isAscending(value.failures.map((item) => item.installationId))
    ) {
      context.addIssue({ code: "custom", message: "catalog identities are not canonical" });
    }
  });

const repository = z
  .strictObject({
    archived: z.boolean(),
    createdAt: dateTime.nullable(),
    defaultBranch: providerBranch,
    disabled: z.boolean(),
    fork: z.boolean(),
    fullName: providerFullName,
    name: providerIdentifier,
    nodeId: providerIdentifier,
    ownerId: positiveInteger,
    ownerLogin: providerIdentifier,
    scope: z.strictObject({ installationId: positiveInteger, repositoryId: positiveInteger }),
    visibility: z.enum(["public", "private", "internal"]),
    workbenchAuthorized: z.boolean(),
  })
  .superRefine((value, context) => {
    if (value.fullName !== `${value.ownerLogin}/${value.name}`) {
      context.addIssue({ code: "custom", message: "repository name projection is inconsistent" });
    }
  });

export const repositoryPageSchema: z.ZodType<RepositoryPage> = z
  .strictObject({
    consistency: z.literal("best_effort"),
    hasNextPage: z.boolean(),
    installation,
    observedAt: dateTime,
    ok: z.literal(true),
    page: z.number().int().min(1).max(10_000),
    perPage: z.number().int().min(1).max(100),
    repositories: z.array(repository).max(100),
    totalCount: safeInteger.nonnegative(),
  })
  .superRefine((value, context) => {
    const repositoryIds = value.repositories.map((item) => item.scope.repositoryId);
    if (new Set(repositoryIds).size !== repositoryIds.length) {
      context.addIssue({ code: "custom", message: "repository identities are not unique" });
    }
    if (
      value.repositories.some(
        (item) => item.scope.installationId !== value.installation.installationId,
      )
    ) {
      context.addIssue({ code: "custom", message: "repository page crosses installation scope" });
    }
    if (
      value.installation.state !== "active" ||
      value.repositories.some((item) => item.ownerId !== value.installation.accountId)
    ) {
      context.addIssue({ code: "custom", message: "repository page crosses installation account" });
    }
    const observedStart = (value.page - 1) * value.perPage;
    const observedEnd = observedStart + value.repositories.length;
    const contradictoryTerminal =
      !value.hasNextPage &&
      ((value.repositories.length > 0 && observedEnd < value.totalCount) ||
        (value.repositories.length === 0 && observedStart < value.totalCount));
    if (
      value.repositories.length > value.perPage ||
      (value.repositories.length > 0 && observedEnd > value.totalCount) ||
      (value.hasNextPage && (value.repositories.length === 0 || observedEnd >= value.totalCount)) ||
      (value.hasNextPage && value.page === 10_000) ||
      contradictoryTerminal
    ) {
      context.addIssue({ code: "custom", message: "repository pagination is contradictory" });
    }
  });

function boundedCanonicalText(maximumBytes: number) {
  return z
    .string()
    .min(1)
    .max(maximumBytes)
    .refine((value) => value === value.trim(), "text is not canonical")
    .refine(isUnicodeScalarText, "text contains an unpaired surrogate")
    .refine(
      (value) => new TextEncoder().encode(value).byteLength <= maximumBytes,
      "text exceeds its byte bound",
    );
}

function isUnicodeScalarText(value: string): boolean {
  for (const character of value) {
    const codePoint = character.codePointAt(0);
    if (codePoint !== undefined && codePoint >= 0xd800 && codePoint <= 0xdfff) return false;
  }
  return true;
}

function isAscending(values: readonly number[]): boolean {
  return values.every((value, index) => {
    const previous = values[index - 1];
    return previous === undefined || previous < value;
  });
}
