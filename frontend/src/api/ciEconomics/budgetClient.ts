import type { WorkbenchScope } from "../workbench/client";
import {
  type BudgetCommand,
  type BudgetMutation,
  type BudgetPolicies,
  type BudgetSignals,
  budgetCommandSchema,
  budgetKey,
  budgetMutationSchema,
  budgetOutcome,
  budgetPoliciesSchema,
  budgetSignalsSchema,
  signalIdentityIsCanonical,
} from "./budgetSchema";
import {
  digest,
  positiveInteger,
  sameScope,
  validRepositoryScope as validScope,
} from "./sourceSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

export async function fetchBudgetPolicies(
  scope: WorkbenchScope,
  signal?: AbortSignal,
): Promise<EconomicsResult<BudgetPolicies>> {
  if (!validScope(scope)) return { kind: "invalid-response" };
  return requestEconomics({
    path: `${path(scope)}/budget-policies`,
    schema: budgetPoliciesSchema,
    maximumBytes: 32 * 1024,
    signal,
    admits: (page) => sameScope(page, scope),
  });
}

export async function configureBudgetPolicy(
  input: BudgetCommand,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<EconomicsResult<BudgetMutation>> {
  const parsed = budgetCommandSchema.safeParse(input);
  if (!parsed.success || !/^[A-Za-z0-9_-]{43}$/.test(csrfToken))
    return { kind: "invalid-response" };
  const command = parsed.data;
  return requestEconomics({
    path: "/api/v2/economics/budget-policies",
    schema: budgetMutationSchema,
    maximumBytes: 4096,
    signal,
    csrfToken,
    body: command,
    successStatuses: [200, 409],
    admits: (result, status) =>
      result.operationId === command.operationId &&
      (result.policy === null
        ? status === 409
        : status === 200 &&
          sameScope(result.policy, command) &&
          result.policy.policyKey === command.policyKey &&
          result.policy.revision === command.expectedRevision + 1 &&
          JSON.stringify(result.policy.configuration) === JSON.stringify(command.configuration)),
  });
}

export type SignalFilter = {
  readonly afterCursor?: string | undefined;
  readonly policyKey?: string | undefined;
  readonly revision?: number | undefined;
  readonly outcome?: BudgetSignals["items"][number]["outcome"] | undefined;
};

export async function fetchBudgetSignals(
  scope: WorkbenchScope,
  filter: SignalFilter,
  signal?: AbortSignal,
): Promise<EconomicsResult<BudgetSignals>> {
  if (
    !validScope(scope) ||
    (filter.afterCursor !== undefined && !digest.safeParse(filter.afterCursor).success) ||
    (filter.policyKey !== undefined && !budgetKey.safeParse(filter.policyKey).success) ||
    (filter.revision !== undefined &&
      (filter.policyKey === undefined || !positiveInteger.safeParse(filter.revision).success)) ||
    (filter.outcome !== undefined && !budgetOutcome.safeParse(filter.outcome).success)
  )
    return { kind: "invalid-response" };
  const query = new URLSearchParams({ limit: "20" });
  if (filter.afterCursor !== undefined) query.set("afterCursor", filter.afterCursor);
  if (filter.policyKey !== undefined) query.set("policyKey", filter.policyKey);
  if (filter.revision !== undefined) query.set("revision", String(filter.revision));
  if (filter.outcome !== undefined) query.set("outcome", filter.outcome);
  return requestEconomics({
    path: `${path(scope)}/budget-signals?${query}`,
    schema: budgetSignalsSchema,
    maximumBytes: 128 * 1024,
    signal,
    admits: async (page) =>
      sameScope(page, scope) &&
      page.items.length <= 20 &&
      page.items.every(
        (item) =>
          (filter.afterCursor === undefined || item.signalId > filter.afterCursor) &&
          (filter.policyKey === undefined || item.policy.policyKey === filter.policyKey) &&
          (filter.revision === undefined || item.policy.revision === filter.revision) &&
          (filter.outcome === undefined || item.outcome === filter.outcome),
      ) &&
      (await Promise.all(page.items.map(signalIdentityIsCanonical))).every(Boolean),
  });
}

function path(scope: WorkbenchScope): string {
  return `/api/v2/economics/repositories/${scope.installationId}/${scope.repositoryId}`;
}
