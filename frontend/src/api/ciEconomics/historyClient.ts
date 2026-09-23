import {
  type HistoryCommand,
  type HistoryMutation,
  historyCommandSchema,
  historyMutationSchema,
  sameHistoryConfiguration,
} from "./historySchema";
import { type HistoryStatus, historyStatusSchema } from "./historyStatusSchema";
import { positiveInteger, sameScope } from "./sourceSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

type HistoryScope = Pick<HistoryCommand, "installationId" | "repositoryId">;

export async function fetchHistoryStatus(
  scope: HistoryScope,
  signal?: AbortSignal,
): Promise<EconomicsResult<HistoryStatus>> {
  if (
    !positiveInteger.safeParse(scope.installationId).success ||
    !positiveInteger.safeParse(scope.repositoryId).success
  )
    return { kind: "invalid-response" };
  return requestEconomics({
    path: `/api/v2/economics/repositories/${scope.installationId}/${scope.repositoryId}/history`,
    schema: historyStatusSchema,
    maximumBytes: 16 * 1024,
    signal,
    admits: (status) => sameScope(status, scope),
  });
}

export async function configureHistory(
  input: HistoryCommand,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<EconomicsResult<HistoryMutation>> {
  const parsed = historyCommandSchema.safeParse(input);
  if (!parsed.success || !/^[A-Za-z0-9_-]{43}$/.test(csrfToken))
    return { kind: "invalid-response" };
  const command = parsed.data;
  return requestEconomics({
    path: "/api/v2/economics/history",
    schema: historyMutationSchema,
    maximumBytes: 8192,
    signal,
    csrfToken,
    body: command,
    successStatuses: [200, 400, 409],
    admits: (result, status) =>
      result.operationId === command.operationId &&
      (result.snapshot === null
        ? status === (result.outcome === "invalid_population" ? 400 : 409)
        : status === 200 &&
          sameScope(result.snapshot, command) &&
          result.snapshot.configurationRevision === command.expectedRevision + 1 &&
          sameHistoryConfiguration(result.snapshot.configuration, command.configuration)),
  });
}
