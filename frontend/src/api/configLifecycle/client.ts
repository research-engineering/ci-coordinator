import type { components } from "../generated";
import { operationIdIsAdmitted } from "../shared/operationId";
import { epochIdentityMatches, validationMatches } from "./identity";
import {
  type ConfigRegistration,
  type ConfigRollback,
  type ConfigScope,
  type ConfigStatus,
  type ConfigValidation,
  registrationSchema,
  rollbackCommandSchema,
  rollbackSchema,
  type SourceFormat,
  scopeSchema,
  sourceSchema,
  statusSchema,
  validationSchema,
} from "./schema";
import { attempt, failure, jsonResult, type LifecycleResult, lifecycleRequest } from "./transport";

export interface ValidatedSource {
  readonly scope: ConfigScope;
  readonly source: string;
  readonly sourceFormat: SourceFormat;
  readonly validation: ConfigValidation;
}
export interface RegistrationCommand {
  readonly kind: "registration";
  readonly draft: ValidatedSource;
  readonly operationId: string;
}
export interface RollbackCommand {
  readonly kind: "rollback";
  readonly currentEpochId: string;
  readonly body: components["schemas"]["ConfigEpochRollbackBody"];
}
export type ConfigurationCommand = RegistrationCommand | RollbackCommand;

export function validateSource(
  scope: ConfigScope,
  source: string,
  sourceFormat: SourceFormat,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<LifecycleResult<ValidatedSource>> {
  return attempt(async () => {
    if (
      !scopeSchema.safeParse(scope).success ||
      !sourceSchema.safeParse({ source, sourceFormat }).success
    )
      return { kind: "invalid-request" };
    const body = {
      schemaVersion: "ci-config-epoch-validation/v1",
      source,
      sourceFormat,
    } satisfies components["schemas"]["ConfigEpochValidationBody"];
    const response = await lifecycleRequest("/api/v1/config/validations", signal, body, csrfToken);
    if (response.status !== 200) return failure(response);
    const validation = await jsonResult(response, validationSchema.parse);
    if (!(await validationMatches(scope, source, sourceFormat, validation)))
      return { kind: "invalid-response" };
    return { kind: "ready", value: { scope, source, sourceFormat, validation } };
  });
}

export function registerSource(
  command: RegistrationCommand,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<LifecycleResult<ConfigRegistration>> {
  return attempt(async () => {
    const { draft, operationId } = command;
    if (
      !scopeSchema.safeParse(draft.scope).success ||
      !sourceSchema.safeParse({ source: draft.source, sourceFormat: draft.sourceFormat }).success ||
      !validationSchema.safeParse(draft.validation).success ||
      !operationIdIsAdmitted(operationId) ||
      !(await validationMatches(draft.scope, draft.source, draft.sourceFormat, draft.validation))
    )
      return { kind: "invalid-request" };
    const body = {
      schemaVersion: "ci-config-epoch-registration/v1",
      source: draft.source,
      sourceFormat: draft.sourceFormat,
      operationId,
    } satisfies components["schemas"]["ConfigEpochRegistrationBody"];
    const response = await lifecycleRequest("/api/v1/config/epochs", signal, body, csrfToken);
    if (response.status !== 200 && response.status !== 201) return failure(response);
    const value = await jsonResult(response, registrationSchema.parse);
    return value.epochId === draft.validation.epochId &&
      value.duplicate === (response.status === 200)
      ? { kind: "ready", value }
      : { kind: "invalid-response" };
  });
}

export function rollbackConfig(
  command: RollbackCommand,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<LifecycleResult<ConfigRollback>> {
  return attempt(async () => {
    if (
      !rollbackCommandSchema.safeParse(command.body).success ||
      !/^[0-9a-f]{64}$/.test(command.currentEpochId) ||
      command.currentEpochId === command.body.targetEpochId
    )
      return { kind: "invalid-request" };
    const response = await lifecycleRequest(
      "/api/v1/config/rollbacks",
      signal,
      command.body,
      csrfToken,
    );
    if (response.status !== 200) return failure(response);
    const value = await jsonResult(response, rollbackSchema.parse);
    return value.epochId === command.body.targetEpochId &&
      value.revision === command.body.expectedRevision + 1
      ? { kind: "ready", value }
      : { kind: "invalid-response" };
  });
}

export function readConfigStatus(
  scope: ConfigScope,
  after: string | null,
  signal?: AbortSignal,
  limit = 50,
): Promise<LifecycleResult<ConfigStatus>> {
  return attempt(async () => {
    if (
      !scopeSchema.safeParse(scope).success ||
      !Number.isSafeInteger(limit) ||
      limit < 1 ||
      limit > 100 ||
      (after !== null && !/^[0-9a-f]{64}$/.test(after))
    )
      return { kind: "invalid-request" };
    const query = new URLSearchParams({ limit: String(limit) });
    if (after !== null) query.set("afterEpochId", after);
    const response = await lifecycleRequest(
      `/api/v1/config/repositories/${scope.installationId}/${scope.repositoryId}/status?${query}`,
      signal,
    );
    if (response.status !== 200) return failure(response);
    const value = await jsonResult(response, statusSchema.parse);
    if (
      value.installationId !== scope.installationId ||
      value.repositoryId !== scope.repositoryId ||
      value.epochs.length > limit ||
      value.epochs.some(
        (epoch, index) => epoch.epochId <= (value.epochs[index - 1]?.epochId ?? after ?? ""),
      ) ||
      (value.nextCursor !== null && value.nextCursor !== value.epochs.at(-1)?.epochId)
    )
      return { kind: "invalid-response" };
    for (const epoch of value.epochs) {
      if (!(await epochIdentityMatches(scope, epoch))) return { kind: "invalid-response" };
    }
    return { kind: "ready", value };
  });
}
