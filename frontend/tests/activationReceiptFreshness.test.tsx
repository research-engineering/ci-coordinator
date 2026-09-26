import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { RepositoryActivationControl } from "../src/features/workbench/RepositoryActivationControl";
import { useConfigActivation } from "../src/features/workbench/useConfigActivation";
import { useRepositoryAttestation } from "../src/features/workbench/useRepositoryAttestation";
import {
  configActivationFixture,
  controlPlaneSessionFixture,
  workflowDiscoveryFixture,
} from "./fixture";

const A1 = { epochId: "a".repeat(64), revision: 1 };
const proposalTarget = workflowDiscoveryFixture().proposal.admittedEpochId;
if (proposalTarget === null) throw new Error("The reviewable fixture must have a target epoch");
const T2 = { epochId: proposalTarget, revision: 2 };
const C3 = { epochId: "c".repeat(64), revision: 3 };
const scope = { installationId: 1, repositoryId: 1, limit: 10 };
const session = controlPlaneSessionFixture();
const manifestId = "proposal:c0169591134297170c6402e83fc6b67f";
function binding() {
  return {
    scope,
    expectedRevision: 1 as number | null | undefined,
    manifestId,
    targetEpochId: T2.epochId,
    csrfToken: session.csrfToken,
    authorityRevision: 1,
    expiresAt: session.expiresAt,
    allowed: true,
    newCommandAllowed: true,
    onConfirmed: vi.fn(),
    onConflict: vi.fn(),
  };
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  history.replaceState(null, "", "/");
});

test.each(["network", "invalid", "unavailable", "overloaded"] as const)(
  "%s keeps Q across read loss and C/3; only an explicit retry confirms historical T/2",
  async (failure) => {
    const bodies: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        bodies.push(await request.text());
        if (bodies.length === 1) {
          if (failure === "network") throw new TypeError("lost receipt");
          if (failure === "invalid") return Response.json({});
          return Response.json({ ok: false, error: failure }, { status: 503 });
        }
        return Response.json(
          configActivationFixture({ duplicate: true, epochId: T2.epochId, revision: 2 }),
        );
      }),
    );
    const props = binding();
    const { result, rerender } = renderHook(useConfigActivation, { initialProps: props });
    act(() => {
      result.current.submit();
      result.current.submit();
    });
    await waitFor(() => expect(result.current.uncertain).toBe(true));
    expect(bodies).toHaveLength(1);
    rerender({ ...props, expectedRevision: undefined, newCommandAllowed: false });
    act(() => result.current.submit());
    rerender({
      ...props,
      expectedRevision: 3,
      newCommandAllowed: false,
      manifestId: "proposal:ffffffffffffffffffffffffffffffff",
      targetEpochId: C3.epochId,
    });
    expect(result.current.uncertain).toBe(true);
    expect(bodies).toHaveLength(1);
    act(() => result.current.retry());
    await waitFor(() => expect(props.onConfirmed).toHaveBeenCalledTimes(1));
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toBe(bodies[0]);
    expect(JSON.parse(bodies[1] ?? "")).toMatchObject({
      expectedRevision: 1,
      targetEpochId: "4".repeat(64),
      proposalManifestId: manifestId,
    });
    expect(props.onConfirmed).toHaveBeenCalledWith(
      expect.objectContaining({ epochId: T2.epochId, revision: 2, duplicate: true }),
    );
  },
);

test.each(["authority", "scope", "expiry", "unmount"] as const)(
  "a late successful activation cannot publish after %s retirement",
  async (change) => {
    const pending = Promise.withResolvers<Response>();
    let request: Request | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((value: Request) => {
        request = value;
        return pending.promise;
      }),
    );
    const props = binding();
    const view = renderHook(useConfigActivation, { initialProps: props });
    act(() => view.result.current.submit());
    await waitFor(() => expect(request).toBeDefined());
    if (change === "unmount") view.unmount();
    else
      view.rerender({
        ...props,
        ...(change === "authority"
          ? { authorityRevision: 2 }
          : change === "scope"
            ? { scope: { ...scope, repositoryId: 2 } }
            : { expiresAt: "2000-01-01T00:00:00Z" }),
      });
    await act(async () =>
      pending.resolve(Response.json(configActivationFixture({ epochId: T2.epochId, revision: 2 }))),
    );
    expect(props.onConfirmed).not.toHaveBeenCalled();
    if (change === "expiry") {
      expect(view.result.current.uncertain).toBe(true);
      expect(view.result.current.expired).toBe(true);
    } else expect(request?.signal.aborted).toBe(true);
  },
);

test("attestation retry preserves its original baseline and cannot authorize activation at C/3", async () => {
  const bodies: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      bodies.push(await request.text());
      if (bodies.length === 1) throw new TypeError("lost review");
      return Response.json({ ok: false, error: "already_reviewed" }, { status: 409 });
    }),
  );
  const props = {
    scope,
    expectedActive: A1,
    manifestId,
    csrfToken: session.csrfToken,
    authorityRevision: 1,
    expiresAt: session.expiresAt,
    allowed: true,
  };
  const view = renderHook(useRepositoryAttestation, { initialProps: props });
  act(() => {
    view.result.current.start("old-review");
    view.result.current.start("other-review");
  });
  await waitFor(() => expect(view.result.current.uncertain).toBe(true));
  view.rerender({ ...props, expectedActive: C3 });
  act(() => view.result.current.retry());
  await waitFor(() => expect(view.result.current.uncertain).toBe(false));
  expect(view.result.current.reviewed).toBe(false);
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toBe(bodies[0]);
  expect(JSON.parse(bodies[1] ?? "")).toMatchObject({
    operationId: "old-review",
    expectedActive: A1,
  });
});

test("a failed confirmation cannot erase uncertainty or permit a new activation", async () => {
  const bodies: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      bodies.push(await request.text());
      if (bodies.length === 1) throw new TypeError("lost receipt");
      return Response.json({ ok: false, error: "revision_conflict" }, { status: 409 });
    }),
  );
  const props = binding();
  const { result } = renderHook(useConfigActivation, { initialProps: props });
  act(() => result.current.submit());
  await waitFor(() => expect(result.current.uncertain).toBe(true));
  act(() => result.current.retry());
  await waitFor(() =>
    expect(result.current.state).toMatchObject({
      kind: "settled",
      result: { kind: "revision_conflict" },
    }),
  );
  act(() => result.current.submit());
  expect(result.current.uncertain).toBe(true);
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toBe(bodies[0]);
  expect(props.onConflict).not.toHaveBeenCalled();
  expect(props.onConfirmed).not.toHaveBeenCalled();
});

test.each([false, true])(
  "a well-shaped wrong target keeps Q uncertain and never notifies a floor, duplicate=%s",
  async (duplicate) => {
    const bodies: string[] = [];
    const receipt = configActivationFixture({ epochId: T2.epochId, revision: 2, duplicate });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        bodies.push(await request.text());
        return Response.json(
          bodies.length === 1 ? { ...receipt, epochId: "b".repeat(64) } : receipt,
        );
      }),
    );
    const props = binding();
    expect(props.targetEpochId).toBe("4".repeat(64));
    const { result } = renderHook(useConfigActivation, { initialProps: props });
    act(() => result.current.submit());
    await waitFor(() =>
      expect(result.current.state).toMatchObject({
        kind: "settled",
        uncertain: true,
        result: { kind: "invalid-response" },
        command: { targetEpochId: "4".repeat(64), expectedRevision: 1 },
      }),
    );
    expect(props.onConfirmed).not.toHaveBeenCalled();
    expect(props.onConflict).not.toHaveBeenCalled();
    act(() => result.current.submit());
    expect(bodies).toHaveLength(1);
    act(() => result.current.retry());
    await waitFor(() => expect(props.onConfirmed).toHaveBeenCalledWith(receipt));
    expect(result.current.uncertain).toBe(false);
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toBe(bodies[0]);
  },
);

test.each(["baseline", "review", "role", "expiry"] as const)(
  "a new activation requires current %s admission before any POST",
  (missing) => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const props = binding();
    const { result } = renderHook(useConfigActivation, {
      initialProps: {
        ...props,
        ...(missing === "baseline"
          ? { expectedRevision: undefined }
          : missing === "review"
            ? { newCommandAllowed: false }
            : missing === "role"
              ? { allowed: false }
              : { expiresAt: "2000-01-01T00:00:00Z" }),
      },
    });
    act(() => result.current.submit());
    expect(fetch).not.toHaveBeenCalled();
    expect(result.current.state.kind).toBe("idle");
  },
);

test("receipt is historical while the separately observed current configuration is C/3", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) =>
      request.url.endsWith("/start")
        ? Response.json({ ok: false, error: "already_reviewed" }, { status: 409 })
        : Response.json(
            configActivationFixture({ duplicate: true, epochId: T2.epochId, revision: 2 }),
          ),
    ),
  );
  const props = {
    scope,
    proposal: workflowDiscoveryFixture().proposal,
    session,
    expectedActive: A1,
    authorityRevision: 1,
    snapshotReadRevision: 0,
    snapshotLoading: false,
    onConfirmed: vi.fn(),
    onRefreshSnapshot: vi.fn(),
    onLockedChange: vi.fn(),
  };
  const view = render(<RepositoryActivationControl {...props} />);
  await userEvent.click(screen.getByRole("button", { name: "Verify authority" }));
  await userEvent.click(await screen.findByRole("button", { name: "Activate proposal" }));
  await screen.findByText("Activation already recorded");
  view.rerender(
    <RepositoryActivationControl {...props} expectedActive={undefined} snapshotLoading />,
  );
  expect(screen.getByText("Activation recorded at revision 2")).toBeVisible();
  view.rerender(
    <RepositoryActivationControl {...props} expectedActive={C3} snapshotReadRevision={1} />,
  );
  expect(screen.getByText("Activation recorded at revision 2")).toBeVisible();
  expect(screen.getByText(/Current observed revision 3/)).toBeVisible();
  expect(screen.queryByText(/now authoritative/)).not.toBeInTheDocument();
  expect(props.onConfirmed).toHaveBeenCalledWith(T2);
});

test("definitive conflict requires a fresh read and consumes the old callback hint only on explicit fresh verification", async () => {
  history.replaceState(
    null,
    "",
    "/?installationId=1&repositoryId=1&repositoryAttestation=reviewed&proposalManifestId=" +
      manifestId +
      "&reviewOperationId=old-review",
  );
  const bodies: { operationId: string; expectedActive?: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      bodies.push((await request.json()) as { operationId: string });
      return Response.json(
        {
          ok: false,
          error: request.url.endsWith("/start") ? "already_reviewed" : "attestation_invalid",
        },
        { status: 409 },
      );
    }),
  );
  const props = {
    scope,
    proposal: workflowDiscoveryFixture().proposal,
    session,
    expectedActive: A1,
    authorityRevision: 1,
    snapshotReadRevision: 0,
    snapshotLoading: false,
    onConfirmed: vi.fn(),
    onRefreshSnapshot: vi.fn(),
    onLockedChange: vi.fn(),
  };
  const view = render(<RepositoryActivationControl {...props} />);
  await userEvent.click(screen.getByRole("button", { name: "Confirm review" }));
  await userEvent.click(await screen.findByRole("button", { name: "Activate proposal" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Verify authority again" })).toBeDisabled(),
  );
  expect(bodies[0]?.operationId).toBe("old-review");
  view.rerender(
    <RepositoryActivationControl {...props} expectedActive={C3} snapshotReadRevision={1} />,
  );
  expect(bodies).toHaveLength(2);
  await userEvent.click(screen.getByRole("button", { name: "Verify authority again" }));
  await waitFor(() => expect(bodies).toHaveLength(3));
  expect(bodies[2]?.operationId).not.toBe("old-review");
  expect(bodies[2]?.expectedActive).toEqual(C3);
  expect(props.onConfirmed).not.toHaveBeenCalled();
  // A backend already_reviewed response is not proof of renewed manifest approval.
});
