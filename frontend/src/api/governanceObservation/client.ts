import type { components } from "../generated";
import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { caughtFailure } from "../shared/responseFailure";
import { governanceStateDigestMatches } from "./identity";
import {
  type GovernanceObservation,
  type GovernanceObservationError,
  governanceObservationErrorSchema,
  governanceObservationSchema,
} from "./schema";

type RepositoryScope = components["schemas"]["RepositoryScopeResponse"];

const REQUEST_TIMEOUT_MS = 30_000;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

export type GovernanceObservationResult =
  | { readonly kind: "ready"; readonly observation: GovernanceObservation }
  | { readonly kind: "unauthenticated" }
  | { readonly kind: "forbidden" }
  | { readonly kind: "not-found" }
  | { readonly kind: "rate-limited"; readonly retryAfterSeconds?: number }
  | {
      readonly kind: "unavailable";
      readonly reason: Extract<
        GovernanceObservationError["error"],
        | "unavailable"
        | "malformed_provider_response"
        | "provider_binding_mismatch"
        | "observation_limit_exceeded"
      >;
    }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export async function fetchGovernanceObservation(
  scope: RepositoryScope,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<GovernanceObservationResult> {
  if (
    !Number.isSafeInteger(scope.installationId) ||
    scope.installationId < 1 ||
    !Number.isSafeInteger(scope.repositoryId) ||
    scope.repositoryId < 1
  ) {
    return { kind: "invalid-response" };
  }
  const path =
    `/api/v1/workbench/repositories/${scope.installationId}/${scope.repositoryId}` +
    "/governance-observation";
  try {
    const response = await boundedFetch(
      new Request(new URL(path, globalThis.location.origin), {
        credentials: "same-origin",
        headers: { accept: "application/json" },
        signal: combinedSignal(signal, timeoutMs),
      }),
      MAX_RESPONSE_BYTES,
    );
    if (response.status !== 200) return await observationFailure(response);
    const observation = governanceObservationSchema.parse(await response.json());
    if (
      observation.repository.scope.installationId !== scope.installationId ||
      observation.repository.scope.repositoryId !== scope.repositoryId ||
      !(await governanceStateDigestMatches(observation))
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", observation };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

async function observationFailure(response: Response): Promise<GovernanceObservationResult> {
  let body: GovernanceObservationError;
  try {
    body = governanceObservationErrorSchema.parse(await response.json());
  } catch {
    return { kind: "invalid-response" };
  }
  switch (body.error) {
    case "unauthenticated":
      return response.status === 401 ? { kind: "unauthenticated" } : { kind: "invalid-response" };
    case "forbidden":
      return response.status === 403 ? { kind: "forbidden" } : { kind: "invalid-response" };
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
