import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fetchObservationStatus } from "../src/api/ciEconomics/observationClient";
import type { ObservationStatus } from "../src/api/ciEconomics/observationStatusSchema";
import type { EconomicsResult } from "../src/api/ciEconomics/transport";
import { useObservationStatus } from "../src/features/ciEconomics/useObservationStatus";
import {
  OBSERVATION_CONFIG,
  OBSERVATION_SCOPE,
  observationSnapshot,
  observationStatus,
} from "./observationFixture";

vi.mock("../src/api/ciEconomics/observationClient", () => ({ fetchObservationStatus: vi.fn() }));
const read = vi.mocked(fetchObservationStatus);
beforeEach(() => {
  vi.useFakeTimers();
  read.mockReset();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("enabled polling is bounded, retains evidence on failure and waits for explicit recovery", async () => {
  read
    .mockResolvedValueOnce({ kind: "ready", value: observationStatus() })
    .mockResolvedValueOnce({ kind: "unavailable", reason: "unavailable" })
    .mockResolvedValue({
      kind: "ready",
      value: observationStatus(observationSnapshot({ ...OBSERVATION_CONFIG, enabled: false })),
    });
  const hook = renderHook(() => useObservationStatus(OBSERVATION_SCOPE));
  await act(async () => {});
  expect(hook.result.current.state.value).toEqual(observationStatus());
  await act(async () => vi.advanceTimersByTimeAsync(14_999));
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTimeAsync(1));
  expect(hook.result.current.state.failure?.kind).toBe("unavailable");
  expect(hook.result.current.state.value).toEqual(observationStatus());
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(read).toHaveBeenCalledTimes(2);
  await act(async () => hook.result.current.refresh());
  expect(hook.result.current.state.failure).toBeUndefined();
  expect(hook.result.current.state.value?.snapshot?.configuration.enabled).toBe(false);
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(read).toHaveBeenCalledTimes(3);
});
test("hidden pages stop automatic reads and visibility recovery never overlaps a pending read", async () => {
  const visibility = vi.spyOn(document, "visibilityState", "get");
  read.mockResolvedValue({ kind: "ready", value: observationStatus() });
  const hook = renderHook(() => useObservationStatus(OBSERVATION_SCOPE));
  await act(async () => {});
  visibility.mockReturnValue("hidden");
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(read).toHaveBeenCalledTimes(1);
  const pending = Promise.withResolvers<EconomicsResult<ObservationStatus>>();
  read.mockReturnValueOnce(pending.promise);
  visibility.mockReturnValue("visible");
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  act(() => {
    hook.result.current.refresh();
    hook.result.current.refresh();
  });
  expect(read).toHaveBeenCalledTimes(2);
  expect(hook.result.current.state.refreshing).toBe(true);
  expect(hook.result.current.state.value).toEqual(observationStatus());
  await act(async () => pending.resolve({ kind: "ready", value: observationStatus() }));
  expect(read).toHaveBeenCalledTimes(3);
});
test("scope switch aborts old work and rejects a late foreign completion", async () => {
  const old = Promise.withResolvers<EconomicsResult<ObservationStatus>>();
  read
    .mockReturnValueOnce(old.promise)
    .mockResolvedValue({ kind: "ready", value: { ...observationStatus(null), repositoryId: 2 } });
  const hook = renderHook(({ scope }) => useObservationStatus(scope), {
    initialProps: { scope: OBSERVATION_SCOPE },
  });
  const signal = read.mock.calls[0]?.[1];
  hook.rerender({ scope: { ...OBSERVATION_SCOPE, repositoryId: 2 } });
  expect(hook.result.current.state.value).toBeUndefined();
  await act(async () => {});
  expect(signal?.aborted).toBe(true);
  expect(hook.result.current.state.value?.repositoryId).toBe(2);
  await act(async () => old.resolve({ kind: "ready", value: observationStatus() }));
  expect(hook.result.current.state.value?.repositoryId).toBe(2);
});
test.each(["resolve", "reject"] as const)(
  "unmount ignores %s and cancels any future polling",
  async (outcome) => {
    const pending = Promise.withResolvers<EconomicsResult<ObservationStatus>>();
    read.mockReturnValue(pending.promise);
    const hook = renderHook(() => useObservationStatus(OBSERVATION_SCOPE));
    const signal = read.mock.calls[0]?.[1];
    hook.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () =>
      outcome === "resolve"
        ? pending.resolve({ kind: "ready", value: observationStatus() })
        : pending.reject(new Error("cancelled")),
    );
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(read).toHaveBeenCalledTimes(1);
  },
);
test("unexpected rejection is visible, retryable and not converted into zero data", async () => {
  read
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ kind: "ready", value: observationStatus(null) });
  const hook = renderHook(() => useObservationStatus(OBSERVATION_SCOPE));
  await act(async () => {});
  expect(hook.result.current.state.failure?.kind).toBe("network-failure");
  expect(hook.result.current.state.value).toBeUndefined();
  await act(async () => hook.result.current.refresh());
  expect(hook.result.current.state.value).toEqual(observationStatus(null));
});

test.each([true, false])(
  "queued observation refresh waits while hidden after enabled=%s",
  async (enabled) => {
    const visibility = vi.spyOn(document, "visibilityState", "get");
    const pending = Promise.withResolvers<EconomicsResult<ObservationStatus>>();
    const value = observationStatus(observationSnapshot({ ...OBSERVATION_CONFIG, enabled }));
    read.mockReturnValueOnce(pending.promise).mockResolvedValue({ kind: "ready", value });
    const hook = renderHook(() => useObservationStatus(OBSERVATION_SCOPE));
    act(() => hook.result.current.refresh());
    visibility.mockReturnValue("hidden");
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => pending.resolve({ kind: "ready", value }));
    expect(read).toHaveBeenCalledTimes(1);
    visibility.mockReturnValue("visible");
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(read).toHaveBeenCalledTimes(2);
  },
);
