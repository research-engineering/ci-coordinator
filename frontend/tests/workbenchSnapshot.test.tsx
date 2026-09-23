import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useWorkbenchSnapshot } from "../src/features/workbench/useWorkbenchSnapshot";
import { workbenchFixture } from "./fixture";

afterEach(() => vi.unstubAllGlobals());

test("an absent scope remains idle without issuing a request", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => useWorkbenchSnapshot(undefined, 0));
  expect(result.current.state).toEqual({ kind: "idle" });
  expect(fetch).not.toHaveBeenCalled();
});

test.each(["scope", "limit", "authority", "refresh"] as const)(
  "a late response from an abandoned %s generation preserves the current snapshot",
  async (change) => {
    const first = Promise.withResolvers<Response>();
    const second = Promise.withResolvers<Response>();
    const fetch = vi.fn<(request: Request) => Promise<Response>>();
    fetch.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    vi.stubGlobal("fetch", fetch);
    const scope = { installationId: 1, repositoryId: 1, limit: 10 };
    const { result, rerender } = renderHook(
      (props) => useWorkbenchSnapshot(props.scope, props.authorityRevision),
      { initialProps: { scope, authorityRevision: 0 } },
    );
    expect(fetch).toHaveBeenCalledTimes(1);
    const previousSignal = fetch.mock.calls[0]?.[0].signal;
    expect(previousSignal?.aborted).toBe(false);

    const nextScope =
      change === "scope"
        ? { ...scope, repositoryId: 2 }
        : change === "limit"
          ? { ...scope, limit: 11 }
          : scope;
    if (change === "refresh") act(() => result.current.refresh());
    else rerender({ scope: nextScope, authorityRevision: change === "authority" ? 1 : 0 });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(previousSignal?.aborted).toBe(true);
    expect(result.current.state.kind).toBe("loading");

    await act(async () => {
      second.resolve(
        Response.json(
          workbenchFixture({
            ledgerRevision: 99,
            scope: {
              installationId: nextScope.installationId,
              repositoryId: nextScope.repositoryId,
            },
          }),
        ),
      );
    });
    expect(result.current.state).toMatchObject({
      kind: "settled",
      result: { kind: "ready", snapshot: { ledgerRevision: 99 } },
    });
    const current = result.current.state;

    await act(async () => {
      first.resolve(Response.json(workbenchFixture({ ledgerRevision: 1 })));
    });
    expect(result.current.state).toBe(current);
    expect(fetch).toHaveBeenCalledTimes(2);
  },
);
