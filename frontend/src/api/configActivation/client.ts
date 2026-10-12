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
  readonly reviewOperationId: string;
  readonly targetEpochId: string;
}

// CI-018 v3 selector for one GitHub-reviewed registered epoch; it carries no proposal manifest.
export interface RegisteredEpochActivationCommand {
  readonly scope: RepositoryScope;
  readonly expectedRevision: number | null;
  readonly operationId: string;
  readonly registeredEpochReviewOperationId: string;
  readonly expectedSourceHash: string;
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
  return postActivation(
    {
      expectedRevision: command.expectedRevision,
      installationId: command.scope.installationId,
      operationId: command.operationId,
      proposalManifestId: command.proposalManifestId,
      reviewOperationId: command.reviewOperationId,
      repositoryId: command.scope.repositoryId,
      schemaVersion: "ci-config-epoch-activation/v2",
      targetEpochId: command.targetEpochId,
    } satisfies components["schemas"]["ExplicitConfigEpochActivationBody"],
    csrfToken,
    signal,
    timeoutMs,
  );
}

export async function activateRegisteredEpoch(
  command: RegisteredEpochActivationCommand,
  csrfToken: string,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<ConfigActivationResult> {
  if (
    !commonActivationIsAdmitted(command) ||
    !operationIdIsAdmitted(command.registeredEpochReviewOperationId) ||
    !/^[0-9a-f]{64}$/.test(command.expectedSourceHash) ||
    !/^[A-Za-z0-9_-]{43}$/.test(csrfToken)
  ) {
    return { kind: "invalid-response" };
  }
  return postActivation(
    {
      expectedRevision: command.expectedRevision,
      expectedSourceHash: command.expectedSourceHash,
      installationId: command.scope.installationId,
      operationId: command.operationId,
      registeredEpochReviewOperationId: command.registeredEpochReviewOperationId,
      repositoryId: command.scope.repositoryId,
      schemaVersion: "ci-config-epoch-activation/v3",
      targetEpochId: command.targetEpochId,
    } satisfies components["schemas"]["RegisteredEpochConfigActivationBody"],
    csrfToken,
    signal,
    timeoutMs,
  );
}

async function postActivation(
  body:
    | components["schemas"]["ExplicitConfigEpochActivationBody"]
    | components["schemas"]["RegisteredEpochConfigActivationBody"],
  csrfToken: string,
  signal: AbortSignal | undefined,
  timeoutMs: number,
): Promise<ConfigActivationResult> {
  const targetEpochId = body.targetEpochId;
  const resultRevision = (body.expectedRevision ?? 0) + 1;
  try {
    const response = await boundedFetch(
      new Request(new URL("/api/v1/config/activations", globalThis.location.origin), {
        body: JSON.stringify(body),
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
      const activation = configActivationSchema.parse(await response.json());
      return activation.epochId === targetEpochId && activation.revision === resultRevision
        ? { kind: "complete", activation }
        : { kind: "invalid-response" };
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
    legacy_new_operation_unsupported: 409,
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
    commonActivationIsAdmitted(command) &&
    operationIdIsAdmitted(command.reviewOperationId) &&
    /^proposal:[0-9a-f]{32}$/.test(command.proposalManifestId)
  );
}

function commonActivationIsAdmitted(
  command: Pick<
    ConfigActivationCommand,
    "scope" | "expectedRevision" | "operationId" | "targetEpochId"
  >,
): boolean {
  return (
    Number.isSafeInteger(command.scope.installationId) &&
    command.scope.installationId > 0 &&
    Number.isSafeInteger(command.scope.repositoryId) &&
    command.scope.repositoryId > 0 &&
    (command.expectedRevision === null ||
      (Number.isSafeInteger(command.expectedRevision) && command.expectedRevision > 0)) &&
    operationIdIsAdmitted(command.operationId) &&
    /^[0-9a-f]{64}$/.test(command.targetEpochId)
  );
}
