import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "../src/App";
import type { RollbackCommand } from "../src/api/configLifecycle/client";
import type { ExpectedActiveEpoch } from "../src/api/repositoryAttestation/client";
import type { WorkbenchSnapshot } from "../src/api/workbench/schema";
import { useConfigurationCommand } from "../src/features/configuration/useConfigurationCommand";
import { useConfigurationStatus } from "../src/features/configuration/useConfigurationStatus";
import { RepositoryWorkspace } from "../src/features/workbench/RepositoryWorkspace";
import { useWorkbenchSnapshot } from "../src/features/workbench/useWorkbenchSnapshot";
import { useWorkflowDiscovery } from "../src/features/workbench/useWorkflowDiscovery";
import { configEpoch, configJson, configStatus, validatedSource } from "./configurationFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "./fixture";

const scope = { installationId: 1, repositoryId: 1, limit: 10 };
const lifecycleScope = { installationId: 1, repositoryId: 1 };
const A1 = { epochId: "a".repeat(64), revision: 1 };
const B2 = { epochId: "b".repeat(64), revision: 2 };
const C3 = { epochId: "c".repeat(64), revision: 3 };
const proposalTarget = workflowDiscoveryFixture().proposal.admittedEpochId;
if (proposalTarget === null) throw new Error("The reviewable fixture must have a target epoch");
const T2 = { epochId: proposalTarget, revision: 2 };
const session = controlPlaneSessionFixture();

function snapshot(active: ExpectedActiveEpoch | null): WorkbenchSnapshot {
  return workbenchFixture({
    configEpochs:
      active === null
        ? []
        : [
            {
              active: true,
              activeRevision: active.revision,
              epochId: active.epochId,
              sourceFormat: "json",
              sourceHash: "d".repeat(64),
              documentHash: "e".repeat(64),
              epochHash: "f".repeat(64),
              documentSchemaId: "ci-repository-policy/v1",
              documentProfileId: "ci-policy-document/v1",
              semanticProfileId: "ci-repository-policy-semantics/v1",
              compiledSchemaId: "ci-compiled-repository-policy/v1",
            },
          ],
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  history.replaceState(null, "", "/workbench");
});

test("discovery ignores display limit and scope allocation but still replaces an explicit refresh", async () => {
  const replacement = Promise.withResolvers<Response>();
  const fetch = vi
    .fn<(request: Request) => Promise<Response>>()
    .mockImplementationOnce(async () => Response.json(workflowDiscoveryFixture()))
    .mockImplementation(() => replacement.promise);
  vi.stubGlobal("fetch", fetch);
  const { result, rerender, unmount } = renderHook(
    (value) => useWorkflowDiscovery(value, undefined),
    { initialProps: scope },
  );
  await waitFor(() => expect(result.current.state).toMatchObject({ result: { kind: "ready" } }));
  const original = result.current.state;
  const firstSignal = fetch.mock.calls[0]?.[0].signal;
  rerender({ ...scope, limit: 20 });
  expect(result.current.state).toBe(original);
  expect(firstSignal?.aborted).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(1);
  act(() => result.current.refresh());
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(firstSignal?.aborted).toBe(true);
  expect(result.current.state.kind).toBe("loading");
  unmount();
  await act(async () => replacement.resolve(Response.json(workflowDiscoveryFixture())));
});

test("actual advanced-access limit-only navigation preserves the exact uncertain activation command", async () => {
  history.replaceState(
    null,
    "",
    "/workbench?installationId=1&repositoryId=1&limit=10&view=workflows",
  );
  const delayedDiscovery = Promise.withResolvers<Response>();
  const bodies: string[] = [];
  const limits: string[] = [];
  let discoveryReads = 0;
  let active = A1;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/auth/session") return Response.json(session);
      if (url.pathname === "/api/v1/workbench/installations")
        return Response.json(installationCatalogFixture());
      if (url.pathname === "/api/v1/workbench/installations/1/repositories")
        return Response.json(repositoryPageFixture());
      if (url.pathname.endsWith("/workflow-discovery")) {
        discoveryReads++;
        return discoveryReads === 1
          ? Response.json(workflowDiscoveryFixture())
          : delayedDiscovery.promise;
      }
      if (url.pathname === "/api/v1/workbench/repositories/1/1") {
        limits.push(url.searchParams.get("limit") ?? "");
        return Response.json(snapshot(active));
      }
      if (url.pathname.endsWith("/start"))
        return Response.json({ ok: false, error: "already_reviewed" }, { status: 409 });
      if (url.pathname.endsWith("/activations")) {
        bodies.push(await request.text());
        if (bodies.length === 1) throw new TypeError("lost activation receipt");
        return Response.json({
          schemaVersion: "ci-config-epoch-activation-result/v1",
          ok: true,
          duplicate: true,
          epochId: T2.epochId,
          revision: 2,
        });
      }
      throw new Error(`Unexpected request: ${request.url}`);
    }),
  );
  const view = render(<App />);
  try {
    const verify = await screen.findByRole("button", { name: "Verify authority" });
    await waitFor(() => expect(verify).toBeEnabled());
    await userEvent.click(verify);
    await userEvent.click(await screen.findByRole("button", { name: "Activate proposal" }));
    await screen.findByText(/The outcome is unconfirmed/);
    active = C3;
    await userEvent.click(screen.getByRole("link", { name: "Repositories" }));
    await userEvent.click(screen.getByText("Advanced degraded access"));
    expect(screen.getByRole("spinbutton", { name: "Installation" })).toHaveValue(1);
    expect(screen.getByRole("spinbutton", { name: "Repository" })).toHaveValue(1);
    const limit = screen.getByRole("spinbutton", { name: "Items per section" });
    await userEvent.clear(limit);
    await userEvent.type(limit, "20");
    await userEvent.click(screen.getByRole("button", { name: "Load snapshot" }));
    await screen.findByRole("heading", { name: "Overview", level: 1 });
    await waitFor(() => expect(limits).toEqual(["10", "20"]));
    await userEvent.click(screen.getByRole("link", { name: "Workflows" }));
    expect(discoveryReads).toBe(1);
    expect(bodies).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Retry configuration activation" }));
    await screen.findByText("Activation already recorded");
    await screen.findByText(/Current observed revision 3/);
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toBe(bodies[0]);
    expect(JSON.parse(bodies[1] ?? "")).toMatchObject({
      targetEpochId: "4".repeat(64),
      expectedRevision: 1,
    });
  } finally {
    view.unmount();
    await act(async () => delayedDiscovery.resolve(Response.json(workflowDiscoveryFixture())));
  }
});

test.each([null, A1, { epochId: A1.epochId, revision: 2 }])(
  "a confirmed B/2 floor masks a missing, older or conflicting read: %j",
  async (stale) => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json(snapshot(A1)))
      .mockResolvedValueOnce(Response.json(snapshot(stale)))
      .mockResolvedValueOnce(Response.json(snapshot(C3)));
    vi.stubGlobal("fetch", fetch);
    const { result } = renderHook(() => useWorkbenchSnapshot(scope, 1));
    await waitFor(() => expect(result.current.state).toMatchObject({ result: { kind: "ready" } }));
    act(() => result.current.invalidate(B2));
    expect(result.current.state.kind).toBe("loading");
    await waitFor(() =>
      expect(result.current.state).toMatchObject({ result: { kind: "invalid-response" } }),
    );
    expect(result.current.minimumActive).toEqual(B2);
    act(() => result.current.refresh());
    await waitFor(() =>
      expect(result.current.state).toMatchObject({
        result: {
          kind: "ready",
          snapshot: { configEpochs: [{ epochId: C3.epochId, activeRevision: 3 }] },
        },
      }),
    );
    expect(fetch).toHaveBeenCalledTimes(3);
  },
);

test("read replacement fences old A/1; old receipts cannot lower B/2; E retires callbacks and floors", async () => {
  const old = Promise.withResolvers<Response>();
  const fetch = vi
    .fn()
    .mockImplementationOnce(() => old.promise)
    .mockImplementation(async () => Response.json(snapshot(B2)));
  vi.stubGlobal("fetch", fetch);
  const { result, rerender } = renderHook((authority) => useWorkbenchSnapshot(scope, authority), {
    initialProps: 1,
  });
  const oldSignal = (fetch.mock.calls[0]?.[0] as Request | undefined)?.signal;
  expect(oldSignal?.aborted).toBe(false);
  const retired = result.current.invalidate;
  act(() => result.current.invalidate(B2));
  await waitFor(() => expect(result.current.state).toMatchObject({ result: { kind: "ready" } }));
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => old.resolve(Response.json(snapshot(A1))));
  expect(result.current.state).toMatchObject({
    result: { snapshot: { configEpochs: [{ activeRevision: 2 }] } },
  });
  act(() => result.current.invalidate(A1));
  expect(result.current.minimumActive).toEqual(B2);
  await act(async () => rerender(2));
  expect(result.current.minimumActive).toBeUndefined();
  await waitFor(() => expect(result.current.state).toMatchObject({ result: { kind: "ready" } }));
  const count = fetch.mock.calls.length;
  act(() => retired(C3));
  expect(fetch).toHaveBeenCalledTimes(count);
  expect(result.current.readRevision).toBe(0);
});

test("equal-revision contradictory receipts fail closed, never selecting a fabricated active identity", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(snapshot(B2))),
  );
  const { result } = renderHook(() => useWorkbenchSnapshot(scope, 1));
  act(() => {
    result.current.invalidate(B2);
    result.current.invalidate({ ...A1, revision: 2 });
  });
  await waitFor(() =>
    expect(result.current.state).toMatchObject({ result: { kind: "invalid-response" } }),
  );
  expect(result.current.minimumConflict).toBe(true);
});

test("real rollback admission notifies the snapshot owner with literal B/2; registration supplies no active floor", async () => {
  const command: RollbackCommand = {
    kind: "rollback",
    currentEpochId: A1.epochId,
    body: {
      schemaVersion: "ci-config-epoch-rollback/v1",
      installationId: 1,
      repositoryId: 1,
      expectedRevision: 1,
      targetEpochId: B2.epochId,
      operationId: "rollback-B2",
      reason: "Restore retained policy",
    },
  };
  const draft = validatedSource();
  const changed = vi.fn();
  const fetch = vi.fn(async (request: Request) => {
    if (request.url.endsWith("/rollbacks"))
      return configJson({
        schemaVersion: "ci-config-epoch-activation-result/v1",
        ok: true,
        duplicate: false,
        epochId: B2.epochId,
        revision: 2,
      });
    return configJson(
      {
        schemaVersion: "ci-config-epoch-registration-result/v1",
        ok: true,
        duplicate: false,
        epochId: draft.validation.epochId,
      },
      201,
    );
  });
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => useConfigurationCommand(session, changed));
  await act(async () => result.current.submit(command));
  expect(result.current.state.kind).toBe("complete");
  expect(changed).toHaveBeenLastCalledWith({ epochId: "b".repeat(64), revision: 2 });
  await act(async () =>
    result.current.submit({ kind: "registration", draft, operationId: "register-one" }),
  );
  expect(result.current.state.kind).toBe("complete");
  expect(changed).toHaveBeenLastCalledWith(undefined);
  expect(changed).toHaveBeenCalledTimes(2);
});

test("shared and local configuration read revisions remain independent, including hidden reads", async () => {
  const fetch = vi.fn(async () =>
    configJson({
      schemaVersion: "ci-config-epoch-status/v1",
      installationId: 1,
      repositoryId: 1,
      active: B2,
      epochs: [],
      nextCursor: null,
    }),
  );
  vi.stubGlobal("fetch", fetch);
  const { result, rerender } = renderHook(
    ({ local, shared, enabled }) =>
      useConfigurationStatus(lifecycleScope, enabled, B2, local, shared),
    { initialProps: { local: 1, shared: 0, enabled: true } },
  );
  await waitFor(() => expect(result.current.result?.kind).toBe("ready"));
  rerender({ local: 0, shared: 1, enabled: true });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  rerender({ local: 0, shared: 2, enabled: false });
  expect(result.current.result).toBeUndefined();
  expect(fetch).toHaveBeenCalledTimes(2);
  rerender({ local: 0, shared: 2, enabled: true });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
});

test("Workspace refresh supplies B/2 to the real verification command without a navigation POST", async () => {
  let active = A1;
  const writes: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/workflow-discovery")) return Response.json(workflowDiscoveryFixture());
      if (path.endsWith("/start")) {
        writes.push(await request.json());
        return Response.json({ ok: false, error: "already_reviewed" }, { status: 409 });
      }
      return Response.json(snapshot(active));
    }),
  );
  const props = {
    authorityRevision: 1,
    scope,
    session,
    view: "overview" as const,
    tab: "plans" as const,
    onTab: vi.fn(),
    economicsTab: "registered" as const,
    onEconomicsTab: vi.fn(),
  };
  const view = render(<RepositoryWorkspace {...props} />);
  await screen.findByText("Ledger revision");
  active = B2;
  await userEvent.click(screen.getByRole("button", { name: "Refresh repository snapshot" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Refresh repository snapshot" })).toBeEnabled(),
  );
  view.rerender(<RepositoryWorkspace {...props} view="workflows" />);
  expect(writes).toEqual([]);
  await userEvent.click(await screen.findByRole("button", { name: "Verify authority" }));
  await screen.findByRole("button", { name: "Activate proposal" });
  expect(writes).toEqual([
    expect.objectContaining({ expectedActive: { epochId: "b".repeat(64), revision: 2 } }),
  ]);
});

test("an activation finishing under Configuration invalidates its independent status read without remounting the source draft", async () => {
  const pending = Promise.withResolvers<Response>();
  let active = A1;
  let reads = 0;
  let posted = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/workflow-discovery")) return Response.json(workflowDiscoveryFixture());
      if (path.endsWith("/start"))
        return Response.json({ ok: false, error: "already_reviewed" }, { status: 409 });
      if (path.endsWith("/activations")) {
        posted = true;
        return pending.promise;
      }
      if (path.endsWith("/status")) {
        reads++;
        return configJson({
          schemaVersion: "ci-config-epoch-status/v1",
          installationId: 1,
          repositoryId: 1,
          active,
          epochs: [],
          nextCursor: null,
        });
      }
      return Response.json(snapshot(active));
    }),
  );
  const props = {
    authorityRevision: 1,
    scope,
    session,
    view: "workflows" as const,
    tab: "plans" as const,
    onTab: vi.fn(),
    economicsTab: "registered" as const,
    onEconomicsTab: vi.fn(),
  };
  const view = render(<RepositoryWorkspace {...props} />);
  const verify = await screen.findByRole("button", { name: "Verify authority" });
  await waitFor(() => expect(verify).toBeEnabled());
  await userEvent.click(verify);
  await userEvent.click(await screen.findByRole("button", { name: "Activate proposal" }));
  await waitFor(() => expect(posted).toBe(true));
  view.rerender(<RepositoryWorkspace {...props} view="configuration" />);
  await userEvent.type(screen.getByRole("textbox", { name: "Source" }), "name: retained draft");
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await screen.findByText("Active revision 1");
  expect(reads).toBe(1);
  active = T2;
  await act(async () =>
    pending.resolve(
      Response.json({
        ok: true,
        duplicate: false,
        epochId: T2.epochId,
        revision: 2,
        schemaVersion: "ci-config-epoch-activation-result/v1",
      }),
    ),
  );
  await screen.findByText("Active revision 2");
  expect(reads).toBe(2);
  await userEvent.click(screen.getByRole("tab", { name: "Source" }));
  expect(screen.getByRole("textbox", { name: "Source" })).toHaveValue("name: retained draft");
});

test("real Workspace rollback replaces the snapshot consumed by Workflows, Overview and Audit", async () => {
  const target = configEpoch();
  const original = configStatus();
  let applied = false;
  let snapshots = 0;
  const starts: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/status"))
        return configJson({
          ...original,
          active: applied ? { epochId: target.epochId, revision: 5 } : original.active,
        });
      if (path.endsWith("/rollbacks")) {
        expect(await request.json()).toMatchObject({
          expectedRevision: 4,
          targetEpochId: target.epochId,
        });
        applied = true;
        return configJson({
          schemaVersion: "ci-config-epoch-activation-result/v1",
          ok: true,
          epochId: target.epochId,
          revision: 5,
          duplicate: false,
        });
      }
      if (path.endsWith("/workflow-discovery")) return Response.json(workflowDiscoveryFixture());
      if (path.endsWith("/start")) {
        starts.push(await request.json());
        return Response.json({ ok: false, error: "already_reviewed" }, { status: 409 });
      }
      snapshots++;
      return Response.json({
        ...snapshot(applied ? { epochId: target.epochId, revision: 5 } : original.active),
        ledgerRevision: applied ? 73 : 42,
      });
    }),
  );
  const props = {
    authorityRevision: 1,
    scope,
    session,
    view: "configuration" as const,
    tab: "plans" as const,
    onTab: vi.fn(),
    economicsTab: "registered" as const,
    onEconomicsTab: vi.fn(),
  };
  const view = render(<RepositoryWorkspace {...props} />);
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await userEvent.click(
    await screen.findByRole("button", { name: `Inspect epoch ${target.epochId.slice(0, 12)}` }),
  );
  await userEvent.type(screen.getByLabelText("Reason"), "Restore retained policy");
  await userEvent.click(screen.getByRole("button", { name: "Review rollback" }));
  await userEvent.click(await screen.findByRole("button", { name: "Confirm rollback" }));
  await screen.findByText("Rollback confirmed at revision 5.");
  await waitFor(() => expect(snapshots).toBe(2));
  view.rerender(<RepositoryWorkspace {...props} view="workflows" />);
  const verify = await screen.findByRole("button", { name: "Verify authority" });
  await waitFor(() => expect(verify).toBeEnabled());
  await userEvent.click(verify);
  await screen.findByRole("button", { name: "Activate proposal" });
  expect(starts).toEqual([
    expect.objectContaining({ expectedActive: { epochId: target.epochId, revision: 5 } }),
  ]);
  for (const visible of ["overview", "audit"] as const) {
    view.rerender(<RepositoryWorkspace {...props} view={visible} />);
    expect(screen.getByText("73", { selector: "strong" })).toBeVisible();
  }
  expect(snapshots).toBe(2);
});
