import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { EconomicsResult } from "../src/api/ciEconomics/transport";
import { useEconomicsRead } from "../src/features/ciEconomics/useEconomicsRead";

test.each(["refresh", "reader", "epoch"] as const)(
  "%s rejects an old completion after the newer read settles in the same hook",
  async (change) => {
    const pending: {
      signal: AbortSignal;
      resolve: (result: EconomicsResult<number>) => void;
    }[] = [];
    const read = (signal: AbortSignal) =>
      new Promise<EconomicsResult<number>>((resolve) => pending.push({ signal, resolve }));
    const hook = renderHook(({ reader, epoch }) => useEconomicsRead(reader, epoch), {
      initialProps: { reader: read, epoch: 0 },
    });
    await waitFor(() => expect(pending).toHaveLength(1));
    if (change === "refresh") act(() => hook.result.current.refresh());
    else if (change === "epoch") hook.rerender({ reader: read, epoch: 1 });
    else hook.rerender({ reader: (signal) => read(signal), epoch: 0 });
    await waitFor(() => expect(pending).toHaveLength(2));
    const [old, current] = pending;
    if (!old || !current) throw new Error("Both reads must be outstanding");
    expect(old.signal.aborted).toBe(true);
    expect(current.signal.aborted).toBe(false);
    await act(async () => current.resolve({ kind: "ready", value: 2 }));
    expect(hook.result.current.state).toEqual({
      kind: "settled",
      result: { kind: "ready", value: 2 },
    });
    await act(async () => old.resolve({ kind: "ready", value: 1 }));
    expect(hook.result.current.state).toEqual({
      kind: "settled",
      result: { kind: "ready", value: 2 },
    });
    hook.unmount();
    expect(current.signal.aborted).toBe(true);
  },
);

test.each(["refresh", "epoch"] as const)(
  "%s invalidates visible data until its own read settles",
  async (change) => {
    const read = vi.fn(async (): Promise<EconomicsResult<number>> => ({ kind: "ready", value: 5 }));
    const hook = renderHook(({ epoch }) => useEconomicsRead(read, epoch), {
      initialProps: { epoch: 0 },
    });
    await waitFor(() =>
      expect(hook.result.current.state).toEqual({
        kind: "settled",
        result: { kind: "ready", value: 5 },
      }),
    );
    if (change === "epoch") hook.rerender({ epoch: 1 });
    else act(() => hook.result.current.refresh());
    expect(hook.result.current.state).toEqual({ kind: "loading" });
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(hook.result.current.state.kind).toBe("settled"));
  },
);
test("unexpected rejection has a finite visible failure and unmount aborts outstanding work", async () => {
  const signals: AbortSignal[] = [];
  const read = async (signal: AbortSignal): Promise<EconomicsResult<number>> => {
    signals.push(signal);
    throw new Error("transport failed");
  };
  const hook = renderHook(() => useEconomicsRead(read));
  await waitFor(() =>
    expect(hook.result.current.state).toEqual({
      kind: "settled",
      result: { kind: "network-failure" },
    }),
  );
  hook.unmount();
  expect(signals[0]?.aborted).toBe(true);
});
