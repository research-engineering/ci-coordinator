import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  configureBudgetPolicy,
  fetchBudgetPolicies,
  fetchBudgetSignals,
} from "../src/api/ciEconomics/budgetClient";
import {
  budgetCommandSchema,
  budgetMutationSchema,
  budgetPoliciesSchema,
  budgetSignalSchema,
  budgetSignalsSchema,
} from "../src/api/ciEconomics/budgetSchema";
import {
  budgetCommand,
  budgetPolicies,
  budgetPolicy,
  budgetSignal,
  budgetSignals,
} from "./economicsBudgetFixture";
import { ECONOMICS_CSRF, ECONOMICS_SCOPE as SOURCE_SCOPE } from "./economicsConsoleFixture";

const ECONOMICS_SCOPE = { ...SOURCE_SCOPE, limit: 10 };

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());

test("budget writes bind operation, current revision, configuration and CSRF", async () => {
  const fetch = vi.fn(async (_request: Request) =>
    Response.json({
      schemaVersion: "ci-economics-budget-mutation/v1",
      operationId: "configure-budget",
      outcome: "committed",
      policy: budgetPolicy(),
    }),
  );
  vi.stubGlobal("fetch", fetch);
  expect(await configureBudgetPolicy(budgetCommand(), ECONOMICS_CSRF)).toMatchObject({
    kind: "ready",
  });
  const request = fetch.mock.calls[0]?.[0];
  expect(request?.method).toBe("POST");
  expect(request?.headers.get("x-csrf-token")).toBe(ECONOMICS_CSRF);
  expect(await request?.json()).toEqual(budgetCommand());
});
test.each([
  { operationId: "other" },
  { policy: { ...budgetPolicy(), revision: 2 } },
  { policy: { ...budgetPolicy(), repositoryId: 2 } },
  { policy: { ...budgetPolicy(), policyKey: "other" } },
  {
    policy: {
      ...budgetPolicy(),
      configuration: { ...budgetPolicy().configuration, maximumUs: 21 },
    },
  },
  { outcome: "capacity_reached" },
  { policy: null },
])("rejects mutation operand substitution %j", async (delta) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        schemaVersion: "ci-economics-budget-mutation/v1",
        operationId: "configure-budget",
        outcome: "committed",
        policy: budgetPolicy(),
        ...delta,
      }),
    ),
  );
  expect((await configureBudgetPolicy(budgetCommand(), ECONOMICS_CSRF)).kind).toBe(
    "invalid-response",
  );
});
test.each(["revision_conflict", "operation_conflict", "capacity_reached"])(
  "retains confirmed conflict %s",
  async (outcome) => {
    const response = {
      schemaVersion: "ci-economics-budget-mutation/v1",
      operationId: "configure-budget",
      outcome,
      policy: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(response, { status: 409 })),
    );
    expect(await configureBudgetPolicy(budgetCommand(), ECONOMICS_CSRF)).toEqual({
      kind: "ready",
      value: response,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(response)),
    );
    expect((await configureBudgetPolicy(budgetCommand(), ECONOMICS_CSRF)).kind).toBe(
      "invalid-response",
    );
  },
);
test("validates policy and signal pages and uses bounded read-only queries", async () => {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(
        new URL(request.url).pathname.endsWith("/budget-policies")
          ? budgetPolicies()
          : budgetSignals(),
      );
    }),
  );
  expect((await fetchBudgetPolicies(ECONOMICS_SCOPE)).kind).toBe("ready");
  expect(
    (
      await fetchBudgetSignals(ECONOMICS_SCOPE, {
        policyKey: "backend-cpu",
        revision: 1,
        outcome: "within_budget",
      })
    ).kind,
  ).toBe("ready");
  expect(requests.every((request) => request.method === "GET")).toBe(true);
  expect(new URL(requests[1]?.url ?? "http://invalid").search).toBe(
    "?limit=20&policyKey=backend-cpu&revision=1&outcome=within_budget",
  );
});
test.each([
  { policyDigest: "0".repeat(64) },
  { signalId: "0".repeat(64) },
  { reportId: "0".repeat(64) },
  { policy: { ...budgetPolicy(), revision: 2 } },
  { outcome: "breached" },
  {
    policy: {
      ...budgetPolicy(),
      configuration: { ...budgetPolicy().configuration, enabled: false },
    },
  },
  { retainUntil: budgetSignal().receivedAt },
])("rejects retained signal identity and relation mutations %j", async (delta) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ ...budgetSignals(), items: [{ ...budgetSignal(), ...delta }] }),
    ),
  );
  expect((await fetchBudgetSignals(ECONOMICS_SCOPE, {})).kind).toBe("invalid-response");
});
test.each([
  { afterCursor: budgetSignal().signalId },
  { policyKey: "other" },
  { revision: 2, policyKey: "backend-cpu" },
  { outcome: "breached" as const },
])("binds every requested signal filter %j", async (filter) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(budgetSignals())),
  );
  expect((await fetchBudgetSignals(ECONOMICS_SCOPE, filter)).kind).toBe("invalid-response");
});
test("rejects invalid authority operands before fetch", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  for (const delta of [
    { expectedRevision: Number.MAX_SAFE_INTEGER },
    { operationId: "bad/key" },
    { repositoryId: 0 },
  ]) {
    expect(
      (await configureBudgetPolicy({ ...budgetCommand(), ...delta }, ECONOMICS_CSRF)).kind,
    ).toBe("invalid-response");
  }
  expect((await configureBudgetPolicy(budgetCommand(), "")).kind).toBe("invalid-response");
  expect((await fetchBudgetSignals(ECONOMICS_SCOPE, { revision: 1 })).kind).toBe(
    "invalid-response",
  );
  expect((await fetchBudgetSignals(ECONOMICS_SCOPE, { afterCursor: "bad" })).kind).toBe(
    "invalid-response",
  );
  expect((await fetchBudgetPolicies({ ...ECONOMICS_SCOPE, repositoryId: -1 })).kind).toBe(
    "invalid-response",
  );
  expect(fetch).not.toHaveBeenCalled();
});
test("closed shapes, distinct sorted identities and exact continuation protect catalog admission", () => {
  for (const page of [
    budgetPolicies([budgetPolicy(), budgetPolicy()]),
    budgetPolicies([{ ...budgetPolicy(), installationId: 2 }]),
    { ...budgetPolicies(), maximumPolicies: 17 },
    { ...budgetPolicies(), unexpected: null },
  ]) {
    expect(budgetPoliciesSchema.safeParse(page).success).toBe(false);
  }
  expect(
    budgetSignalsSchema.safeParse({ ...budgetSignals(), nextCursor: "0".repeat(64) }).success,
  ).toBe(false);
  expect(
    budgetSignalsSchema.safeParse(budgetSignals([budgetSignal(), budgetSignal()])).success,
  ).toBe(false);
  expect(
    budgetCommandSchema.safeParse({ ...budgetCommand(), expectedRevision: true }).success,
  ).toBe(false);
  expect(budgetMutationSchema.safeParse({}).success).toBe(false);
  const unavailable = {
    ...budgetSignal(),
    measurement: { ...budgetSignal().measurement, value: null, unavailableReason: "counter_error" },
    outcome: "insufficient_evidence",
  };
  expect(budgetSignalSchema.safeParse(unavailable).success).toBe(true);
  expect(budgetSignalSchema.safeParse({ ...unavailable, outcome: "within_budget" }).success).toBe(
    false,
  );
});
