import {
  type ObservationCommand,
  type ObservationMutation,
  observationCommandSchema,
  observationMutationSchema,
  sameObservationConfiguration,
} from "./observationSchema";
import {
  type ObservationGaps,
  type ObservationStatus,
  observationGapIdentityIsCanonical,
  observationGapsSchema,
  observationStatusSchema,
  parseObservationGapCursor,
} from "./observationStatusSchema";
import { type ObservationWorkflows, observationWorkflowsSchema } from "./observationWorkflowSchema";
import { sameScope, validRepositoryScope as validScope } from "./sourceSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

type ObservationScope = Pick<ObservationCommand, "installationId" | "repositoryId">;

export async function fetchObservationStatus(
  scope: ObservationScope,
  signal?: AbortSignal,
): Promise<EconomicsResult<ObservationStatus>> {
  if (!validScope(scope)) return { kind: "invalid-response" };
  return requestEconomics({
    path: path(scope),
    schema: observationStatusSchema,
    maximumBytes: 16 * 1024,
    signal,
    admits: (status) => sameScope(status, scope),
  });
}

export async function configureObservation(
  input: ObservationCommand,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<EconomicsResult<ObservationMutation>> {
  const parsed = observationCommandSchema.safeParse(input);
  if (!parsed.success || !/^[A-Za-z0-9_-]{43}$/.test(csrfToken))
    return { kind: "invalid-response" };
  const command = parsed.data;
  return requestEconomics({
    path: "/api/v2/economics/observation",
    schema: observationMutationSchema,
    maximumBytes: 4096,
    signal,
    csrfToken,
    body: command,
    successStatuses: [200, 409],
    admits: (result, status) =>
      result.operationId === command.operationId &&
      (result.snapshot === null
        ? status === 409
        : status === 200 &&
          sameScope(result.snapshot, command) &&
          result.snapshot.revision === command.expectedRevision + 1 &&
          sameObservationConfiguration(result.snapshot.configuration, command.configuration)),
  });
}

export async function fetchObservationGaps(
  scope: ObservationScope,
  afterCursor: string | null,
  signal?: AbortSignal,
): Promise<EconomicsResult<ObservationGaps>> {
  const cursor = afterCursor === null ? undefined : parseObservationGapCursor(afterCursor);
  if (
    !validScope(scope) ||
    (afterCursor !== null && (cursor === undefined || !sameScope(cursor, scope)))
  )
    return { kind: "invalid-response" };
  const query = new URLSearchParams({ limit: "20" });
  if (afterCursor !== null) query.set("afterCursor", afterCursor);
  return requestEconomics({
    path: `${path(scope)}/gaps?${query}`,
    schema: observationGapsSchema,
    maximumBytes: 32 * 1024,
    signal,
    admits: async (page) =>
      sameScope(page, scope) &&
      page.items.length <= 20 &&
      page.items.every((item) => cursor === undefined || item.gapId > cursor.gapId) &&
      (await Promise.all(page.items.map(observationGapIdentityIsCanonical))).every(Boolean),
  });
}

export async function fetchObservationWorkflows(
  scope: ObservationScope,
  pageNumber: number,
  signal?: AbortSignal,
): Promise<EconomicsResult<ObservationWorkflows>> {
  if (!validScope(scope) || !Number.isInteger(pageNumber) || pageNumber < 1 || pageNumber > 20)
    return { kind: "invalid-response" };
  return requestEconomics({
    path: `${path(scope)}/workflows?pageNumber=${pageNumber}`,
    schema: observationWorkflowsSchema,
    maximumBytes: 1024 * 1024,
    signal,
    admits: (page) => sameScope(page, scope) && page.pageNumber === pageNumber,
  });
}

function path(scope: ObservationScope): string {
  return `/api/v2/economics/repositories/${scope.installationId}/${scope.repositoryId}/observation`;
}
