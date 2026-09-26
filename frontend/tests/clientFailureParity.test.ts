import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { z } from "zod";
import {
  fetchCiEconomicsAttemptJobs,
  fetchCiEconomicsAttempts,
} from "../src/api/ciEconomics/client";
import { requestEconomics } from "../src/api/ciEconomics/transport";
import { ResponseLimitError } from "../src/api/shared/boundedFetch";
import { fetchWorkbenchSnapshot } from "../src/api/workbench/client";
import { RetainedEpochs } from "../src/features/configuration/RetainedEpochs";
import { SourceEditor } from "../src/features/configuration/SourceEditor";
import { useConfigurationStatus } from "../src/features/configuration/useConfigurationStatus";
import {
  ciEconomicsAttemptJobsFixture,
  ciEconomicsAttemptPageFixture,
  ciEconomicsAttemptSummaryFixture,
} from "./ciEconomicsFixture";
import { configEpoch, configJson, configStatus, configurationSource } from "./configurationFixture";
import { controlPlaneSessionFixture, workbenchFixture } from "./fixture";

const scope = { installationId: 1, repositoryId: 1, limit: 10 };
const lifecycleScope = { installationId: 1, repositoryId: 1 };
const sharedReads = [
  {
    name: "workbench",
    read: (signal?: AbortSignal, timeout = 15000) => fetchWorkbenchSnapshot(scope, signal, timeout),
    body: workbenchFixture,
  },
  {
    name: "economics attempts",
    read: (signal?: AbortSignal, timeout = 15000) =>
      fetchCiEconomicsAttempts(scope, null, signal, timeout),
    body: ciEconomicsAttemptPageFixture,
  },
  {
    name: "economics jobs",
    read: (signal?: AbortSignal, timeout = 15000) =>
      fetchCiEconomicsAttemptJobs(ciEconomicsAttemptSummaryFixture(), null, signal, timeout),
    body: ciEconomicsAttemptJobsFixture,
  },
];
const options = {
  path: "/api/v2/economics/witness",
  schema: z.strictObject({ ok: z.literal(true) }),
  maximumBytes: 128,
  admits: () => true,
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test.each(sharedReads)(
  "$name: invalid local deadlines are not malformed remote responses; valid 2xx still passes",
  async ({ read, body }) => {
    const fetch = vi.fn(async () => Response.json(body()));
    vi.stubGlobal("fetch", fetch);
    for (const invalid of [0, -1, 0.5, Number.NaN, Number.POSITIVE_INFINITY, 2147483648]) {
      await expect(read(undefined, invalid)).resolves.toEqual({ kind: "network-failure" });
    }
    expect(fetch).not.toHaveBeenCalled();
    await expect(read()).resolves.toMatchObject({ kind: "ready" });
    expect(fetch).toHaveBeenCalledTimes(1);
  },
);

test("economics maximum-byte admission uses the same RangeError classification without calling fetch", async () => {
  const fetch = vi.fn(async () => Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetch);
  for (const invalid of [0, -1, 0.5, Number.NaN, 33554433]) {
    await expect(requestEconomics({ ...options, maximumBytes: invalid })).resolves.toEqual({
      kind: "network-failure",
    });
  }
  expect(fetch).not.toHaveBeenCalled();
  await expect(requestEconomics(options)).resolves.toEqual({ kind: "ready", value: { ok: true } });
});

test.each(sharedReads)(
  "$name: actual response decoding still rejects malformed JSON/schema/length/overflow and wrong scope",
  async ({ read, name }) => {
    const wrongScope =
      name === "workbench"
        ? workbenchFixture({ scope: { installationId: 1, repositoryId: 2 } })
        : name === "economics attempts"
          ? ciEconomicsAttemptPageFixture({
              items: [
                ciEconomicsAttemptSummaryFixture({
                  attempt: { ...ciEconomicsAttemptSummaryFixture().attempt, repositoryId: 2 },
                }),
              ],
            })
          : ciEconomicsAttemptJobsFixture({
              attempt: { ...ciEconomicsAttemptSummaryFixture().attempt, repositoryId: 2 },
            });
    for (const response of [
      new Response("{", { headers: { "content-type": "application/json" } }),
      Response.json({}),
      new Response("{}", {
        headers: { "content-type": "application/json", "content-length": "1" },
      }),
      new Response("{}", {
        headers: { "content-type": "application/json", "content-length": "999999999" },
      }),
      Response.json(wrongScope),
    ]) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => response),
      );
      await expect(read()).resolves.toEqual({ kind: "invalid-response" });
    }
  },
);

test.each(sharedReads)(
  "$name: syntax/schema/body-limit errors retain invalid-response precedence over abort",
  async ({ read }) => {
    for (const error of [
      new SyntaxError("bad JSON"),
      new z.ZodError([]),
      new ResponseLimitError("large"),
    ]) {
      const controller = new AbortController();
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          controller.abort();
          throw error;
        }),
      );
      await expect(read(controller.signal)).resolves.toEqual({ kind: "invalid-response" });
    }
    const controller = new AbortController();
    const aborted = new DOMException("cancelled", "AbortError");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        controller.abort();
        throw aborted;
      }),
    );
    await expect(read(controller.signal)).rejects.toBe(aborted);
  },
);

test("economics transport retains abort-first rejection, unlike the shared malformed-response precedence", async () => {
  for (const error of [
    new RangeError("local"),
    new SyntaxError("bad JSON"),
    new z.ZodError([]),
    new ResponseLimitError("large"),
  ]) {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        controller.abort();
        throw error;
      }),
    );
    await expect(requestEconomics({ ...options, signal: controller.signal })).rejects.toBe(error);
  }
});

test.each(sharedReads)(
  "$name: a real combined deadline abort maps to network-failure, not lifecycle cancellation",
  async ({ read }) => {
    const deadline = new AbortController();
    vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
    const entered = Promise.withResolvers<void>();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (request: Request) =>
          new Promise<Response>((_resolve, reject) => {
            request.signal.addEventListener("abort", () => reject(request.signal.reason), {
              once: true,
            });
            entered.resolve();
          }),
      ),
    );
    const response = read();
    await entered.promise;
    deadline.abort(new DOMException("expired", "TimeoutError"));
    await expect(response).resolves.toEqual({ kind: "network-failure" });
  },
);

test.each(["status", "validation", "export"] as const)(
  "the actual lifecycle %s caller absorbs cancellation without stale publication, downloads or unhandled rejection",
  async (operation) => {
    const requests: Request[] = [];
    const failed = Promise.withResolvers<void>();
    const unhandled: unknown[] = [];
    const record = (event: PromiseRejectionEvent) => unhandled.push(event.reason);
    window.addEventListener("unhandledrejection", record);
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn((request: Request) => {
        if (operation === "export" && new URL(request.url).pathname.endsWith("/status"))
          return Promise.resolve(configJson(configStatus()));
        requests.push(request);
        return new Promise<Response>((_resolve, reject) => {
          request.signal.addEventListener(
            "abort",
            () => {
              reject(new DOMException("cancelled", "AbortError"));
              failed.resolve();
            },
            { once: true },
          );
        });
      }),
    );
    try {
      if (operation === "status") {
        const view = renderHook(
          (enabled) => useConfigurationStatus(lifecycleScope, enabled, undefined, 0),
          {
            initialProps: true,
          },
        );
        await waitFor(() => expect(requests).toHaveLength(1));
        view.rerender(false);
        await act(async () => {
          await failed.promise;
        });
        expect(view.result.current.result).toBeUndefined();
      } else if (operation === "validation") {
        const props = {
          scope: lifecycleScope,
          session: controlPlaneSessionFixture(),
          active: true,
          locked: false,
          onRegister: vi.fn(),
        };
        const view = render(createElement(SourceEditor, props));
        await userEvent.selectOptions(screen.getByLabelText("Source format"), "json");
        fireEvent.change(screen.getByRole("textbox", { name: "Source" }), {
          target: { value: configurationSource },
        });
        await userEvent.click(screen.getByRole("button", { name: "Validate source" }));
        await waitFor(() => expect(requests).toHaveLength(1));
        view.rerender(createElement(SourceEditor, { ...props, active: false }));
        await act(async () => {
          await failed.promise;
        });
        expect(screen.queryByText(/Source validated/)).not.toBeInTheDocument();
        expect(props.onRegister).not.toHaveBeenCalled();
      } else {
        const props = {
          scope: lifecycleScope,
          active: true,
          minimumActive: undefined,
          readRevision: 0,
          canRollback: true,
          onRollback: vi.fn(),
        };
        const view = render(createElement(RetainedEpochs, props));
        await userEvent.click(
          await screen.findByRole("button", {
            name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}`,
          }),
        );
        await userEvent.click(screen.getByRole("button", { name: "Download source" }));
        await waitFor(() => expect(requests).toHaveLength(1));
        view.rerender(createElement(RetainedEpochs, { ...props, active: false }));
        await act(async () => {
          await failed.promise;
        });
        expect(screen.queryByText("Verified source download started.")).not.toBeInTheDocument();
      }
      expect(requests[0]?.signal.aborted).toBe(true);
      expect(click).not.toHaveBeenCalled();
      expect(unhandled).toEqual([]);
    } finally {
      window.removeEventListener("unhandledrejection", record);
    }
  },
);
