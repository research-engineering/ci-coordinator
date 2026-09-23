import { webcrypto } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ConfigurationPage } from "../src/features/configuration/ConfigurationPage";
import {
  configEpoch,
  configJson,
  configStatus,
  configurationSource,
  configValidation,
} from "./configurationFixture";
import { controlPlaneSessionFixture } from "./fixture";

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function props() {
  return {
    scope: { installationId: 1, repositoryId: 1, limit: 10 },
    active: true,
    session: controlPlaneSessionFixture({ roles: ["activate", "configure", "read"] }),
    authorityRevision: 1,
    onWorkflows: undefined,
  };
}

test("BOM is sent unchanged for the backend's forbidden-BOM diagnostic, never silently admitted", async () => {
  const source = `\uFEFF${configurationSource}`;
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      expect(await request.json()).toEqual({
        schemaVersion: "ci-config-epoch-validation/v1",
        source,
        sourceFormat: "json",
      });
      return configJson(
        {
          ok: false,
          error: "invalid_config",
          diagnostics: [
            {
              code: "decode.byte_order_mark_forbidden",
              phase: "decode",
              ruleId: "decode.byte-order-mark",
              instancePointer: "",
            },
          ],
        },
        422,
      );
    }),
  );
  render(<ConfigurationPage {...props()} />);
  fireEvent.change(screen.getByLabelText("Source format"), { target: { value: "json" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Source" }), { target: { value: source } });
  await userEvent.click(screen.getByRole("button", { name: "Validate source" }));
  await screen.findByText(/decode.byte_order_mark_forbidden/);
  expect(screen.getByRole("button", { name: "Review registration" })).toBeDisabled();
  expect(requests).toHaveLength(1);
});

test("provider-shaped diagnostic markup remains plain text", async () => {
  const markup = '<img src="x" alt="" onerror="alert(1)">';
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      configJson(
        {
          ok: false,
          error: "invalid_config",
          diagnostics: [
            { code: markup, phase: "semantics", ruleId: markup, instancePointer: markup },
          ],
        },
        422,
      ),
    ),
  );
  render(<ConfigurationPage {...props()} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Source" }), {
    target: { value: configurationSource },
  });
  await userEvent.click(screen.getByRole("button", { name: "Validate source" }));
  expect(await screen.findByLabelText("Validation diagnostics")).toHaveTextContent(markup);
  expect(document.querySelector(".configuration-workspace img")).toBeNull();
});

test.each(["edit", "hide"])("late validation cannot publish after %s", async (change) => {
  let release: ((response: Response) => void) | undefined;
  let request: Request | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((value: Request) => {
      request = value;
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    }),
  );
  const base = props();
  const view = render(<ConfigurationPage {...base} />);
  fireEvent.change(screen.getByLabelText("Source format"), { target: { value: "json" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Source" }), {
    target: { value: configurationSource },
  });
  await userEvent.click(screen.getByRole("button", { name: "Validate source" }));
  if (change === "edit")
    fireEvent.change(screen.getByRole("textbox", { name: "Source" }), {
      target: { value: `${configurationSource}\n` },
    });
  else view.rerender(<ConfigurationPage {...base} active={false} />);
  expect(request?.signal.aborted).toBe(true);
  await act(async () => {
    release?.(configJson(configValidation()));
  });
  expect(screen.getByRole("button", { name: "Review registration" })).toBeDisabled();
  expect(screen.queryByText("Source validated for repository 1.")).not.toBeInTheDocument();
});

test("a refreshed status invalidates rollback preview even when the selected epoch still exists", async () => {
  const status = configStatus();
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(configJson(status))
    .mockResolvedValueOnce(
      configJson({ ...status, active: { epochId: status.active?.epochId, revision: 6 } }),
    );
  vi.stubGlobal("fetch", fetch);
  render(<ConfigurationPage {...props()} />);
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await userEvent.click(
    await screen.findByRole("button", {
      name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}`,
    }),
  );
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Restore prior source" } });
  await userEvent.click(screen.getByRole("button", { name: "Review rollback" }));
  await userEvent.click(screen.getByRole("button", { name: "Refresh epochs" }));
  await screen.findByText("Active revision 6");
  expect(screen.queryByRole("button", { name: "Confirm rollback" })).not.toBeInTheDocument();
  expect(fetch.mock.calls.every(([request]) => (request as Request).method === "GET")).toBe(true);
});

test("a confirmed rollback receipt prevents a later lower status revision from becoming current", async () => {
  let mutations = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "GET") return configJson(configStatus());
      mutations += 1;
      return configJson({
        schemaVersion: "ci-config-epoch-activation-result/v1",
        ok: true,
        epochId: configEpoch().epochId,
        duplicate: false,
        revision: 5,
      });
    }),
  );
  render(<ConfigurationPage {...props()} />);
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await userEvent.click(
    await screen.findByRole("button", {
      name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}`,
    }),
  );
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Restore prior source" } });
  await userEvent.click(screen.getByRole("button", { name: "Review rollback" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm rollback" }));
  await screen.findByText("Rollback confirmed at revision 5.");
  await screen.findByText("Response identity or metadata could not be verified.");
  expect(screen.queryByRole("button", { name: /Inspect epoch/ })).not.toBeInTheDocument();
  expect(mutations).toBe(1);
});

test("configure without activate can inspect epochs but cannot review rollback", async () => {
  const fetch = vi.fn(async () => configJson(configStatus()));
  vi.stubGlobal("fetch", fetch);
  const base = props();
  render(<ConfigurationPage {...base} session={{ ...base.session, roles: ["configure"] }} />);
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await userEvent.click(
    await screen.findByRole("button", {
      name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}`,
    }),
  );
  expect(screen.queryByRole("button", { name: "Review rollback" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Download source" })).toBeEnabled();
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("active pointer change during pagination restarts the first page", async () => {
  const status = configStatus();
  const first = status.epochs[0];
  const last = status.epochs.at(-1);
  if (!first || !last) throw new Error("Fixture epoch missing");
  const queries: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const query = new URL(request.url).searchParams;
      queries.push(query.get("afterEpochId") ?? "first");
      const active = { epochId: status.active?.epochId, revision: queries.length === 1 ? 4 : 5 };
      return configJson(
        query.has("afterEpochId")
          ? { ...status, active, epochs: [last], nextCursor: null }
          : { ...status, active, epochs: [first], nextCursor: first.epochId },
      );
    }),
  );
  render(<ConfigurationPage {...props()} />);
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await screen.findByText("Active revision 4");
  await userEvent.click(screen.getByRole("button", { name: "Next epoch page" }));
  await waitFor(() => expect(queries).toEqual(["first", first.epochId, "first"]));
  await screen.findByText("Active revision 5");
  expect(screen.getByText("Page 1")).toBeInTheDocument();
});
