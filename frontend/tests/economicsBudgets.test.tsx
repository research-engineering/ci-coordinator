import { webcrypto } from "node:crypto";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { BudgetCommand, BudgetPolicy } from "../src/api/ciEconomics/budgetSchema";
import { BudgetPoliciesPanel } from "../src/features/ciEconomics/BudgetPoliciesPanel";
import { BudgetSignalsPanel } from "../src/features/ciEconomics/BudgetSignalsPanel";
import {
  budgetPolicies,
  budgetPolicy,
  budgetSignal,
  budgetSignals,
} from "./economicsBudgetFixture";
import { ECONOMICS_SCOPE } from "./economicsConsoleFixture";
import { controlPlaneSessionFixture } from "./fixture";

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());
const scope = { ...ECONOMICS_SCOPE, limit: 10 };
const session = controlPlaneSessionFixture({ roles: ["read", "audit", "configure"] });

test("creates, edits and disables the same budget identity through revision CAS", async () => {
  let policies: BudgetPolicy[] = [];
  const commands: BudgetCommand[] = [];
  const writeSignals: AbortSignal[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "GET") return Response.json(budgetPolicies(policies));
      const command: BudgetCommand = await request.json();
      writeSignals.push(request.signal);
      commands.push(command);
      expect(request.headers.get("x-csrf-token")).toBe(session.csrfToken);
      const policy: BudgetPolicy = {
        schemaVersion: "ci-economics-budget-policy/v1",
        ...ECONOMICS_SCOPE,
        policyKey: command.policyKey,
        revision: command.expectedRevision + 1,
        configuration: command.configuration,
      };
      policies = [policy];
      return Response.json({
        schemaVersion: "ci-economics-budget-mutation/v1",
        operationId: command.operationId,
        outcome: "committed",
        policy,
      });
    }),
  );
  render(<BudgetPoliciesPanel scope={scope} session={session} />);
  await userEvent.click(await screen.findByRole("button", { name: "New policy" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Policy key" }), "backend-cpu");
  await userEvent.type(screen.getByRole("textbox", { name: "Sample key" }), "backend-tests");
  fireEvent.change(screen.getByRole("textbox", { name: "Producer SHA-256" }), {
    target: { value: "c".repeat(64) },
  });
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Counter" }), "cpu_user");
  fireEvent.change(screen.getByRole("textbox", { name: "Maximum (microseconds)" }), {
    target: { value: "20" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
  await userEvent.click(await screen.findByRole("button", { name: "Edit backend-cpu" }));
  expect(screen.getByRole("textbox", { name: "Policy key" })).toHaveAttribute("readonly");
  await userEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
  await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
  expect(await screen.findByText("Disabled")).toBeVisible();
  expect(commands.map((command) => command.expectedRevision)).toEqual([0, 1]);
  expect(commands.map((command) => command.configuration.enabled)).toEqual([true, false]);
  expect(new Set(commands.map((command) => command.operationId)).size).toBe(2);
  expect(commands[0]?.configuration).toEqual(budgetPolicy().configuration);
  expect(writeSignals).toHaveLength(2);
  expect(writeSignals.every((signal) => !signal.aborted)).toBe(true);
});

test("unmount cancels an unfinished budget write", async () => {
  const write = Promise.withResolvers<Response>();
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "GET") return Response.json(budgetPolicies());
      requests.push(request);
      request.signal.addEventListener(
        "abort",
        () => write.reject(new DOMException("aborted", "AbortError")),
        { once: true },
      );
      return write.promise;
    }),
  );
  const view = render(<BudgetPoliciesPanel scope={scope} session={session} />);
  await userEvent.click(await screen.findByRole("button", { name: "Edit backend-cpu" }));
  await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
  expect(requests).toHaveLength(1);
  expect(requests[0]?.signal.aborted).toBe(false);
  await act(async () => view.unmount());
  expect(requests[0]?.signal.aborted).toBe(true);
});

test.each(["network", "revision_conflict", "operation_conflict", "capacity_reached"])(
  "does not repeat an uncertain or rejected %s write",
  async (outcome) => {
    let writes = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "GET") return Response.json(budgetPolicies());
        writes += 1;
        if (outcome === "network") throw new TypeError("disconnected");
        const body: BudgetCommand = await request.json();
        return Response.json(
          {
            schemaVersion: "ci-economics-budget-mutation/v1",
            operationId: body.operationId,
            outcome,
            policy: null,
          },
          { status: 409 },
        );
      }),
    );
    render(<BudgetPoliciesPanel scope={scope} session={session} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit backend-cpu" }));
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save policy" })).toBeDisabled();
    expect(writes).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "Reload policies" }));
    expect(await screen.findByRole("button", { name: "Edit backend-cpu" })).toBeVisible();
    expect(writes).toBe(1);
  },
);
test.each(["network", "revision_conflict"])(
  "closing after %s requires a successful fresh read before another editor",
  async (outcome) => {
    let reads = 0;
    const refresh = Promise.withResolvers<Response>();
    const commands: BudgetCommand[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "GET") {
          reads += 1;
          if (reads === 1) return Response.json(budgetPolicies());
          if (reads === 2) return new Response(null, { status: 503 });
          return refresh.promise;
        }
        const command: BudgetCommand = await request.json();
        commands.push(command);
        if (outcome === "network") throw new TypeError("disconnected");
        return Response.json(
          {
            schemaVersion: "ci-economics-budget-mutation/v1",
            operationId: command.operationId,
            outcome,
            policy: null,
          },
          { status: 409 },
        );
      }),
    );
    render(<BudgetPoliciesPanel scope={scope} session={session} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit backend-cpu" }));
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "New policy" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Close editor" }));
    await screen.findByRole("button", { name: "Retry" });
    expect(screen.queryByRole("button", { name: "Edit backend-cpu" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New policy" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(reads).toBe(3));
    expect(screen.queryByRole("textbox", { name: "Policy key" })).not.toBeInTheDocument();
    expect(commands).toHaveLength(1);
    await act(async () =>
      refresh.resolve(Response.json(budgetPolicies([{ ...budgetPolicy(), revision: 2 }]))),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Edit backend-cpu" }));
    expect(screen.getByText("Expected revision 2")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(commands).toHaveLength(2));
    expect(commands.map((command) => command.expectedRevision)).toEqual([1, 2]);
    expect(commands[0]?.operationId).not.toBe(commands[1]?.operationId);
  },
);

test("pending writes cannot be replaced, closed or overtaken by a refresh", async () => {
  const write = Promise.withResolvers<Response>();
  let reads = 0;
  const fetch = vi.fn(async (request: Request) => {
    if (request.method === "GET") {
      reads += 1;
      return Response.json(budgetPolicies());
    }
    return write.promise;
  });
  vi.stubGlobal("fetch", fetch);
  render(<BudgetPoliciesPanel scope={scope} session={session} />);
  await userEvent.click(await screen.findByRole("button", { name: "Edit backend-cpu" }));
  await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
  for (const name of ["New policy", "Close editor", "Refresh evidence", "Saving"]) {
    expect(screen.getByRole("button", { name })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name }));
  }
  expect(reads).toBe(1);
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => write.resolve(new Response(null, { status: 503 })));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Refresh evidence" })).toBeEnabled();
});

test("audit-only access exposes no editing and capacity includes disabled policies", async () => {
  const policies = Array.from({ length: 16 }, (_, index) => ({
    ...budgetPolicy(),
    policyKey: `policy-${String(index).padStart(2, "0")}`,
    configuration: { ...budgetPolicy().configuration, enabled: false },
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(budgetPolicies(policies))),
  );
  const view = render(
    <BudgetPoliciesPanel
      scope={scope}
      session={controlPlaneSessionFixture({ roles: ["audit"] })}
    />,
  );
  await screen.findByText("16 / 16 policy identities");
  expect(screen.queryByRole("button", { name: "New policy" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Edit / })).not.toBeInTheDocument();
  view.rerender(<BudgetPoliciesPanel scope={scope} session={session} />);
  expect(screen.getByRole("button", { name: "New policy" })).toBeDisabled();
  expect(screen.getAllByRole("button", { name: /^Edit / })).toHaveLength(16);
});
test("historical signal outcomes remain separate from command failure and current filters", async () => {
  const signal = { ...budgetSignal(), commandExitCode: 1 };
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(
        budgetSignals(new URL(request.url).searchParams.has("outcome") ? [] : [signal]),
      );
    }),
  );
  render(<BudgetSignalsPanel scope={scope} />);
  expect(await screen.findByText("backend-cpu / revision 1")).toBeVisible();
  expect(screen.getByText("within budget")).toBeVisible();
  expect(screen.getByText("Command exit 1")).toBeVisible();
  await userEvent.click(screen.getByText("Evidence identity"));
  expect(screen.getByText("Policy digest")).toBeVisible();
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Outcome" }), "breached");
  expect(await screen.findByText("No retained signals match this view.")).toBeVisible();
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(new URL(requests[1]?.url ?? "http://invalid").search).toBe("?limit=20&outcome=breached");
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});
