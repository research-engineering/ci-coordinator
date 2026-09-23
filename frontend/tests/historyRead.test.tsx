import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fetchHistoryStatus } from "../src/api/ciEconomics/historyClient";
import type { HistoryStatus } from "../src/api/ciEconomics/historyStatusSchema";
import type { EconomicsResult } from "../src/api/ciEconomics/transport";
import { useHistoryStatus } from "../src/features/ciEconomics/useHistoryStatus";
import {
  HISTORY_SCOPE,
  historyConfiguration,
  historyDataset,
  historyStatus,
} from "./historyFixture";

vi.mock("../src/api/ciEconomics/historyClient", () => ({ fetchHistoryStatus: vi.fn() }));
const read = vi.mocked(fetchHistoryStatus);
beforeEach(() => {
  vi.useFakeTimers();
  read.mockReset();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("history wrapper preserves a failed poll as stale evidence and does not keep retrying", async () => {
  read
    .mockResolvedValueOnce({ kind: "ready", value: historyStatus() })
    .mockResolvedValueOnce({ kind: "unauthenticated" })
    .mockResolvedValue({ kind: "ready", value: historyStatus(null) });
  const hook = renderHook(() => useHistoryStatus(HISTORY_SCOPE));
  await act(async () => {});
  expect(hook.result.current.state.value).toEqual(historyStatus());
  await act(async () => vi.advanceTimersByTimeAsync(15_000));
  expect(hook.result.current.state.failure?.kind).toBe("unauthenticated");
  expect(hook.result.current.state.value).toEqual(historyStatus());
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(read).toHaveBeenCalledTimes(2);
  await act(async () => hook.result.current.refresh());
  expect(hook.result.current.state.value).toEqual(historyStatus(null));
  expect(hook.result.current.state.failure).toBeUndefined();
});
test("late historical responses cannot overwrite a new repository", async () => {
  const old = Promise.withResolvers<EconomicsResult<HistoryStatus>>();
  read
    .mockReturnValueOnce(old.promise)
    .mockResolvedValue({ kind: "ready", value: { ...historyStatus(null), repositoryId: 2 } });
  const hook = renderHook(({ scope }) => useHistoryStatus(scope), {
    initialProps: { scope: HISTORY_SCOPE },
  });
  const signal = read.mock.calls[0]?.[1];
  hook.rerender({ scope: { ...HISTORY_SCOPE, repositoryId: 2 } });
  await act(async () => {});
  expect(signal?.aborted).toBe(true);
  await act(async () => old.resolve({ kind: "ready", value: historyStatus() }));
  expect(hook.result.current.state.value?.repositoryId).toBe(2);
});

test.each([true, false])(
  "hidden queued history refresh waits for visibility after enabled=%s",
  async (enabled) => {
    const visibility = vi.spyOn(document, "visibilityState", "get");
    const pending = Promise.withResolvers<EconomicsResult<HistoryStatus>>();
    const value = historyStatus(historyDataset({ ...historyConfiguration(), enabled }));
    read.mockReturnValueOnce(pending.promise).mockResolvedValue({ kind: "ready", value });
    const hook = renderHook(() => useHistoryStatus(HISTORY_SCOPE));
    act(() => {
      hook.result.current.refresh();
      hook.result.current.refresh();
    });
    visibility.mockReturnValue("hidden");
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => pending.resolve({ kind: "ready", value }));
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(read).toHaveBeenCalledTimes(1);
    visibility.mockReturnValue("visible");
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(read).toHaveBeenCalledTimes(2);
  },
);

test("inactive panel preserves its last value without polling and refreshes on return", async () => {
  read.mockResolvedValue({ kind: "ready", value: historyStatus() });
  const hook = renderHook(({ active }) => useHistoryStatus(HISTORY_SCOPE, active), {
    initialProps: { active: true },
  });
  await act(async () => {});
  hook.rerender({ active: false });
  await act(async () => {
    hook.result.current.refresh();
    await vi.advanceTimersByTimeAsync(60_000);
  });
  expect(read).toHaveBeenCalledTimes(1);
  expect(hook.result.current.state.value).toEqual(historyStatus());
  hook.rerender({ active: true });
  await act(async () => {});
  expect(read).toHaveBeenCalledTimes(2);
});
