import type { components } from "../generated";
import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { operationIdIsAdmitted } from "../shared/operationId";
import { caughtFailure } from "../shared/responseFailure";
import {
  type RepositoryAttestationError,
  repositoryAttestationErrorSchema,
  repositoryAttestationStartSchema,
} from "./schema";

const REQUEST_TIMEOUT_MS = 15_000;
type RepositoryScope = components["schemas"]["RepositoryScopeResponse"];

export interface ExpectedActiveEpoch {
  readonly epochId: string;
  readonly revision: number;
}

export interface RepositoryAttestationCommand {
  readonly scope: RepositoryScope;
  readonly operationId: string;
  readonly expectedManifestId: string;
  readonly expectedActive: ExpectedActiveEpoch | null;
}

export type RepositoryAttestationResult =
  | { readonly kind: "ready"; readonly authorizationUrl: string }
  | { readonly kind: RepositoryAttestationError }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export type RepositoryAttestationFailure = Exclude<
  RepositoryAttestationResult,
  { readonly kind: "ready" }
>["kind"];

export async function startRepositoryAttestation(
  command: RepositoryAttestationCommand,
  csrfToken: string,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<RepositoryAttestationResult> {
  if (!attestationCommandIsAdmitted(command) || !/^[A-Za-z0-9_-]{43}$/.test(csrfToken)) {
    return { kind: "invalid-response" };
  }
  try {
    const response = await boundedFetch(
      new Request(
        new URL("/api/v1/repository-attestations/github/start", globalThis.location.origin),
        {
          body: JSON.stringify({
            installationId: command.scope.installationId,
            repositoryId: command.scope.repositoryId,
            expectedActive: command.expectedActive,
            expectedManifestId: command.expectedManifestId,
            operationId: command.operationId,
          }),
          credentials: "same-origin",
          headers: {
            accept: "application/json",
            "content-type": "application/json",
            "x-csrf-token": csrfToken,
          },
          method: "POST",
          signal: combinedSignal(signal, timeoutMs),
        },
      ),
    );
    if (response.status === 200) {
      const result = repositoryAttestationStartSchema.parse(await response.json());
      return { kind: "ready", authorizationUrl: result.authorizationUrl };
    }
    return await attestationFailure(response);
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

async function attestationFailure(response: Response): Promise<RepositoryAttestationResult> {
  let error: RepositoryAttestationError;
  try {
    error = repositoryAttestationErrorSchema.parse(await response.json()).error;
  } catch {
    return { kind: "invalid-response" };
  }
  const statusByError: Readonly<Record<RepositoryAttestationError, number>> = {
    already_reviewed: 409,
    baseline_conflict: 409,
    blocked: 409,
    diff_limit: 422,
    epoch_conflict: 409,
    forbidden: 403,
    invalid_callback: 400,
    operation_conflict: 409,
    overloaded: 503,
    rate_limited: 429,
    replayed: 409,
    stale: 409,
    unauthenticated: 401,
    unavailable: 503,
  };
  return response.status === statusByError[error] ? { kind: error } : { kind: "invalid-response" };
}

function attestationCommandIsAdmitted(command: RepositoryAttestationCommand): boolean {
  const active = command.expectedActive;
  return (
    Number.isSafeInteger(command.scope.installationId) &&
    command.scope.installationId > 0 &&
    Number.isSafeInteger(command.scope.repositoryId) &&
    command.scope.repositoryId > 0 &&
    operationIdIsAdmitted(command.operationId) &&
    /^proposal:[0-9a-f]{32}$/.test(command.expectedManifestId) &&
    (active === null ||
      (/^[0-9a-f]{64}$/.test(active.epochId) &&
        Number.isSafeInteger(active.revision) &&
        active.revision > 0))
  );
}
