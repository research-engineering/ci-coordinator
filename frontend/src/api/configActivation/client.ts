import type { components } from "../generated";
import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { operationIdIsAdmitted } from "../shared/operationId";
import { caughtFailure } from "../shared/responseFailure";
import {
  type ConfigActivation,
  type ConfigControlErrorCode,
  configActivationSchema,
  configControlErrorSchema,
} from "./schema";

const REQUEST_TIMEOUT_MS = 30_000;
type RepositoryScope = components["schemas"]["RepositoryScopeResponse"];

export interface ConfigActivationCommand {
  readonly scope: RepositoryScope;
  readonly expectedRevision: number | null;
  readonly operationId: string;
  readonly proposalManifestId: string;
  readonly targetEpochId: string;
}

export type ConfigActivationResult =
  | { readonly kind: "complete"; readonly activation: ConfigActivation }
  | { readonly kind: ConfigControlErrorCode }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export type ConfigActivationFailure = Exclude<
  ConfigActivationResult,
  { readonly kind: "complete" }
>["kind"];

export async function activateConfig(
  command: ConfigActivationCommand,
  csrfToken: string,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<ConfigActivationResult> {
  if (!activationCommandIsAdmitted(command) || !/^[A-Za-z0-9_-]{43}$/.test(csrfToken)) {
    return { kind: "invalid-response" };
  }
  try {
    const response = await boundedFetch(
      new Request(new URL("/api/v1/config/activations", globalThis.location.origin), {
        body: JSON.stringify({
          expectedRevision: command.expectedRevision,
          installationId: command.scope.installationId,
          operationId: command.operationId,
          proposalManifestId: command.proposalManifestId,
          repositoryId: command.scope.repositoryId,
          schemaVersion: "ci-config-epoch-activation/v1",
          targetEpochId: command.targetEpochId,
        }),
        credentials: "same-origin",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
          "x-csrf-token": csrfToken,
        },
        method: "POST",
        signal: combinedSignal(signal, timeoutMs),
      }),
    );
    if (response.status === 200) {
      return { kind: "complete", activation: configActivationSchema.parse(await response.json()) };
    }
    return await activationFailure(response);
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

async function activationFailure(response: Response): Promise<ConfigActivationResult> {
  let error: ConfigControlErrorCode;
  try {
    error = configControlErrorSchema.parse(await response.json()).error;
  } catch {
    return { kind: "invalid-response" };
  }
  const statusByError: Readonly<Partial<Record<ConfigControlErrorCode, number>>> = {
    attestation_invalid: 409,
    conflict: 409,
    coverage_reducing: 409,
    coverage_unproven: 409,
    forbidden: 403,
    invalid_config: 413,
    overloaded: 503,
    revision_conflict: 409,
    target_unavailable: 404,
    unauthenticated: 401,
    unavailable: 503,
  };
  return response.status === statusByError[error] ? { kind: error } : { kind: "invalid-response" };
}

function activationCommandIsAdmitted(command: ConfigActivationCommand): boolean {
  return (
    Number.isSafeInteger(command.scope.installationId) &&
    command.scope.installationId > 0 &&
    Number.isSafeInteger(command.scope.repositoryId) &&
    command.scope.repositoryId > 0 &&
    (command.expectedRevision === null ||
      (Number.isSafeInteger(command.expectedRevision) && command.expectedRevision > 0)) &&
    operationIdIsAdmitted(command.operationId) &&
    /^proposal:[0-9a-f]{32}$/.test(command.proposalManifestId) &&
    /^[0-9a-f]{64}$/.test(command.targetEpochId)
  );
}
