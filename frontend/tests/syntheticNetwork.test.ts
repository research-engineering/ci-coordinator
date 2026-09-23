// @vitest-environment node

import type { BrowserContext, WebSocketRoute } from "@playwright/test";
import { expect, test, vi } from "vitest";
import { installSyntheticNetwork } from "../dev/network";
import { createScenario } from "../dev/scenarios";

test.each(["fulfilled", "rejected"] as const)(
  "synthetic socket closure retains its promise and reports a %s outcome",
  async (outcome) => {
    const pending = Promise.withResolvers<void>();
    const close = vi.fn(() => pending.promise);
    const dispose = vi.fn(async () => {});
    const context = {
      on: vi.fn(),
      route: vi.fn<BrowserContext["route"]>().mockResolvedValue({
        dispose,
        [Symbol.asyncDispose]: dispose,
      }),
      routeWebSocket: vi.fn<BrowserContext["routeWebSocket"]>().mockResolvedValue(undefined),
    };
    const onError = vi.fn<(error: unknown) => void>();
    const network = await installSyntheticNetwork(
      context as unknown as BrowserContext,
      "http://127.0.0.1:5173",
      createScenario("empty"),
      onError,
    );
    const handler = context.routeWebSocket.mock.calls[0]?.[1];
    if (!handler) throw new Error("Missing WebSocket route handler");
    const completion: unknown = handler({ close } as unknown as WebSocketRoute);
    expect(close).toHaveBeenCalledExactlyOnceWith();
    expect(completion).toBeInstanceOf(Promise);
    expect(network.blocked).toEqual(["websocket"]);
    expect(onError).not.toHaveBeenCalled();
    let settled = false;
    const observedCompletion = Promise.resolve(completion).then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(settled).toBe(false);
    const failure = new Error("Socket close failed");
    if (outcome === "fulfilled") pending.resolve();
    else pending.reject(failure);
    await observedCompletion;
    expect(settled).toBe(true);
    expect(onError.mock.calls).toEqual(outcome === "rejected" ? [[failure]] : []);
  },
);
