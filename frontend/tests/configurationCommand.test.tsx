import { webcrypto } from "node:crypto";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ConfigurationCommand } from "../src/api/configLifecycle/client";
import { useConfigurationCommand } from "../src/features/configuration/useConfigurationCommand";
import { configEpoch, configJson, rollbackCommand, validatedSource } from "./configurationFixture";
import { controlPlaneSessionFixture } from "./fixture";

const KINDS = ["registration", "rollback"] as const;

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function commandFor(kind: (typeof KINDS)[number]): ConfigurationCommand {
  return kind === "registration"
    ? { kind, draft: validatedSource(), operationId: "registration-one" }
    : rollbackCommand();
}

function successFor(kind: (typeof KINDS)[number]): Response {
  return kind === "registration"
    ? configJson(
        {
          schemaVersion: "ci-config-epoch-registration-result/v1",
          ok: true,
          epochId: configEpoch().epochId,
          duplicate: false,
        },
        201,
      )
    : configJson({
        schemaVersion: "ci-config-epoch-activation-result/v1",
        ok: true,
        epochId: configEpoch().epochId,
        duplicate: false,
        revision: 5,
      });
}

for (const kind of KINDS) {
  test.each([-1, 0, 1])(
    `${kind} response at expiry offset %i preserves the exact outcome`,
    async (offset) => {
      const now = Date.now();
      const expiry = now + 60_000;
      const clock = vi.spyOn(Date, "now").mockReturnValue(now);
      const response = Promise.withResolvers<Response>();
      const requests: Request[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn((request: Request) => {
          requests.push(request);
          return response.promise;
        }),
      );
      const command = commandFor(kind);
      const session = controlPlaneSessionFixture({
        expiresAt: new Date(expiry).toISOString(),
        roles: ["activate", "configure", "read"],
      });
      const { result } = renderHook(() => useConfigurationCommand(session));
      let completed = Promise.resolve();
      act(() => {
        completed = result.current.submit(command);
      });
      await waitFor(() => expect(requests).toHaveLength(1));
      expect(result.current.state).toEqual({ kind: "pending", command });
      const request = requests[0];
      if (!request) throw new Error("Command request missing");
      expect(new URL(request.url).pathname).toBe(
        kind === "registration" ? "/api/v1/config/epochs" : "/api/v1/config/rollbacks",
      );
      expect(request.headers.get("x-csrf-token")).toBe(session.csrfToken);
      const bytes = await request.clone().text();
      clock.mockReturnValue(expiry + offset);
      await act(async () => {
        response.resolve(successFor(kind));
        await completed;
      });

      const state = result.current.state;
      expect(state.kind).toBe(offset < 0 ? "complete" : "uncertain");
      if (state.kind !== "complete" && state.kind !== "uncertain")
        throw new Error("Completed request retained a nonterminal transport state");
      expect(state.command).toBe(command);
      expect(result.current.locked).toBe(offset >= 0);
      expect(result.current.readRevision).toBe(offset < 0 ? 1 : 0);
      expect(requests).toHaveLength(1);
      expect(await request.clone().text()).toBe(bytes);
      if (offset >= 0) {
        await act(async () => {
          await result.current.submit(command);
        });
        expect(requests).toHaveLength(1);
        expect(result.current.state).toBe(state);
      }
    },
  );

  test.each(["abort", "unmount"] as const)(
    `${kind} suppresses an awaited late response after %s`,
    async (stop) => {
      const now = Date.now();
      const clock = vi.spyOn(Date, "now").mockReturnValue(now);
      const NativeController = globalThis.AbortController;
      const controllers: AbortController[] = [];
      vi.stubGlobal(
        "AbortController",
        class extends NativeController {
          constructor() {
            super();
            controllers.push(this);
          }
        },
      );
      const response = Promise.withResolvers<Response>();
      const requests: Request[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn((request: Request) => {
          requests.push(request);
          return response.promise;
        }),
      );
      const command = commandFor(kind);
      const session = controlPlaneSessionFixture({
        expiresAt: new Date(now + 60_000).toISOString(),
        roles: ["activate", "configure", "read"],
      });
      const { result, unmount } = renderHook(() => useConfigurationCommand(session));
      let completed = Promise.resolve();
      act(() => {
        completed = result.current.submit(command);
      });
      await waitFor(() => expect(requests).toHaveLength(1));
      const pending = result.current.state;
      expect(pending).toEqual({ kind: "pending", command });
      if (stop === "unmount") unmount();
      else controllers[0]?.abort();
      expect(controllers[0]?.signal.aborted).toBe(true);
      expect(requests[0]?.signal.aborted).toBe(true);
      clock.mockReturnValue(now + 60_000);
      await act(async () => {
        response.resolve(successFor(kind));
        await completed;
      });

      expect(result.current.state).toBe(pending);
      expect(result.current.readRevision).toBe(0);
      expect(requests).toHaveLength(1);
    },
  );
}
