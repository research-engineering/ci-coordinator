import type { components } from "../generated";
import { governanceStateDigestMatches } from "../governanceObservation/identity";
import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { caughtFailure } from "../shared/responseFailure";
import { governanceComparisonMatchesEvidence } from "./identity";
import {
  type GovernanceComparison,
  type GovernanceComparisonError,
  governanceComparisonErrorSchema,
  governanceComparisonSchema,
} from "./schema";

type RepositoryScope = components["schemas"]["GovernanceScopeResponse"];

const REQUEST_TIMEOUT_MS = 30_000;
// A composed response carries two complete bounded states and duplicates rule metadata.
const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;

export type GovernanceComparisonResult =
  | { readonly kind: "ready"; readonly evidence: GovernanceComparison }
  | { readonly kind: "unauthenticated" }
  | { readonly kind: "forbidden" }
  | { readonly kind: "stale" }
  | { readonly kind: "not-found" }
  | { readonly kind: "rate-limited"; readonly retryAfterSeconds?: number }
  | {
      readonly kind: "unavailable";
      readonly reason: Extract<
        GovernanceComparisonError["error"],
        | "unavailable"
        | "malformed_provider_response"
        | "provider_binding_mismatch"
        | "observation_limit_exceeded"
      >;
    }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export async function fetchGovernanceComparison(
  scope: RepositoryScope,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<GovernanceComparisonResult> {
  if (!scopeIsAdmitted(scope)) return { kind: "invalid-response" };
  const path =
    `/api/v1/workbench/repositories/${scope.installationId}/${scope.repositoryId}` +
    "/governance-comparison";
  try {
    const response = await boundedFetch(
      new Request(new URL(path, globalThis.location.origin), {
        credentials: "same-origin",
        headers: { accept: "application/json" },
        signal: combinedSignal(signal, timeoutMs),
      }),
      MAX_RESPONSE_BYTES,
    );
    if (response.status !== 200) return await comparisonFailure(response);
    const evidence = governanceComparisonSchema.parse(await response.json());
    if (
      !sameScope(evidence.scope, scope) ||
      !(await governanceStateDigestMatches(evidence.observation)) ||
      (evidence.baseline !== null &&
        !(await governanceStateDigestMatches(evidence.baseline.state))) ||
      !governanceComparisonMatchesEvidence(evidence)
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", evidence };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

async function comparisonFailure(response: Response): Promise<GovernanceComparisonResult> {
  let body: GovernanceComparisonError;
  try {
    body = governanceComparisonErrorSchema.parse(await response.json());
  } catch {
    return { kind: "invalid-response" };
  }
  switch (body.error) {
    case "unauthenticated":
      return response.status === 401 ? { kind: "unauthenticated" } : { kind: "invalid-response" };
    case "forbidden":
      return response.status === 403 ? { kind: "forbidden" } : { kind: "invalid-response" };
    case "stale":
      return response.status === 409 ? { kind: "stale" } : { kind: "invalid-response" };
    case "not_found":
      return response.status === 404 ? { kind: "not-found" } : { kind: "invalid-response" };
    case "rate_limited":
      return response.status === 429
        ? {
            kind: "rate-limited",
            ...(body.retryAfterSeconds === null
              ? {}
              : { retryAfterSeconds: body.retryAfterSeconds }),
          }
        : { kind: "invalid-response" };
    case "unavailable":
    case "malformed_provider_response":
    case "provider_binding_mismatch":
    case "observation_limit_exceeded":
      return response.status === 503
        ? { kind: "unavailable", reason: body.error }
        : { kind: "invalid-response" };
  }
}

function scopeIsAdmitted(scope: RepositoryScope): boolean {
  return (
    Number.isSafeInteger(scope.installationId) &&
    scope.installationId > 0 &&
    Number.isSafeInteger(scope.repositoryId) &&
    scope.repositoryId > 0
  );
}

function sameScope(left: RepositoryScope, right: RepositoryScope): boolean {
  return left.installationId === right.installationId && left.repositoryId === right.repositoryId;
}
