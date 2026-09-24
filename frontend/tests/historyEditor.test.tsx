import { webcrypto } from "node:crypto";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { type HistoryCommand, historyCommandSchema } from "../src/api/ciEconomics/historySchema";
import { observationCommandSchema } from "../src/api/ciEconomics/observationSchema";
import { HistoryEditor } from "../src/features/ciEconomics/HistoryEditor";
import { EconomicsConsole } from "./consoleSelectionHarness";
import { economicsSourcePage } from "./economicsConsoleFixture";
import { controlPlaneSessionFixture } from "./fixture";
import {
  HISTORY_SCOPE,
  historyConfiguration,
  historyDataset,
  historyMutation,
  historyStatus,
} from "./historyFixture";
import { observationMutation, observationStatus, observationWorkflows } from "./observationFixture";

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
});
afterEach(() => vi.unstubAllGlobals());
const session = controlPlaneSessionFixture({ roles: ["read", "audit", "configure"] });
function props() {
  return { scope: HISTORY_SCOPE, status: historyStatus(), session, stale: false, onSaved: vi.fn() };
}

test("repository creation date is an explicit UTC shortcut, not an automatic write", async () => {
  vi.stubGlobal("fetch", vi.fn());
  render(
    <HistoryEditor
      {...props()}
      status={historyStatus(null)}
      repositoryCreatedAt="2020-01-01T12:34:56Z"
    />,
  );
  expect(screen.getByLabelText("Import runs created since (UTC)")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Save history" })).toBeDisabled();
  await userEvent.click(
    screen.getByRole("button", { name: "Use repository creation date (2020-01-01)" }),
  );
  expect(screen.getByLabelText("Import runs created since (UTC)")).toHaveValue("2020-01-01");
  expect(screen.getByRole("button", { name: "Save history" })).toBeEnabled();
  expect(fetch).not.toHaveBeenCalled();
});

test("creation shortcut only offers a genuinely earlier bound and leaves manual dates available", async () => {
  vi.stubGlobal("fetch", vi.fn());
  const view = render(<HistoryEditor {...props()} repositoryCreatedAt="2019-12-01T12:00:00Z" />);
  await userEvent.click(screen.getByRole("button", { name: /Use repository creation date/ }));
  expect(screen.getByLabelText("Extend import back to (UTC)")).toHaveValue("2019-12-01");
  view.rerender(<HistoryEditor {...props()} repositoryCreatedAt="2025-01-01T00:00:00Z" />);
  expect(screen.queryByRole("button", { name: /Use repository creation date/ })).toBeNull();
  fireEvent.change(screen.getByLabelText("Extend import back to (UTC)"), {
    target: { value: "2019-11-01" },
  });
  expect(screen.getByLabelText("Extend import back to (UTC)")).toHaveValue("2019-11-01");
});

test.each([
  [undefined, false],
  ["2026-09-12T10:00:01Z", false],
  ["2026-09-12T10:00:00Z", true],
  ["2019-12-31T23:59:59Z", true],
] as const)("initial creation bound %s offers shortcut=%s", (createdAt, offered) => {
  vi.stubGlobal("fetch", vi.fn());
  render(
    <HistoryEditor {...props()} status={historyStatus(null)} repositoryCreatedAt={createdAt} />,
  );
  expect(Boolean(screen.queryByRole("button", { name: /Use repository creation date/ }))).toBe(
    offered,
  );
  expect(screen.getByLabelText("Import runs created since (UTC)")).toHaveValue("");
});

test("creation date equal to the existing lower bound cannot request an empty expansion", () => {
  vi.stubGlobal("fetch", vi.fn());
  render(<HistoryEditor {...props()} repositoryCreatedAt="2020-01-01T00:00:00Z" />);
  expect(screen.queryByRole("button", { name: /Use repository creation date/ })).toBeNull();
});

test("explicit range, retention, workflow and quota configure once then pause and rescan", async () => {
  const commands: HistoryCommand[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "GET") return Response.json(observationWorkflows());
      const command: HistoryCommand = await request.json();
      commands.push(command);
      expect(request.headers.get("x-csrf-token")).toBe(session.csrfToken);
      return Response.json(historyMutation(command));
    }),
  );
  render(<HistoryEditor {...props()} status={historyStatus(null)} />);
  expect(screen.getByRole("button", { name: "Save history" })).toBeDisabled();
  expect(
    screen.getByText(
      "Enabling detailed records applies to future imports. It does not revisit summary-only records; use the explicit rescan to retry their first import. Expired detail is not restored.",
    ),
  ).toBeVisible();
  fireEvent.change(screen.getByLabelText("Import runs created since (UTC)"), {
    target: { value: "2020-01-01" },
  });
  await userEvent.click(screen.getByRole("checkbox", { name: "Collection enabled" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Workflows" }), "selected");
  await userEvent.click(await screen.findByRole("checkbox", { name: /Full Check/ }));
  await userEvent.click(screen.getByText("Retention and capacity limits"));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Detailed records" }), "days");
  fireEvent.change(screen.getByRole("spinbutton", { name: "Detail retention (days)" }), {
    target: { value: "730" },
  });
  fireEvent.change(screen.getByRole("spinbutton", { name: "Retained jobs" }), {
    target: { value: "50000" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Save history" }));
  await screen.findByText(/Saved revision 1\./);
  await userEvent.click(screen.getByRole("checkbox", { name: "Collection enabled" }));
  await userEvent.click(screen.getByRole("button", { name: "Save history" }));
  await screen.findByText(/Saved revision 2\./);
  await userEvent.click(screen.getByRole("checkbox", { name: "Rescan the configured history" }));
  await userEvent.click(screen.getByRole("button", { name: "Save history" }));
  await screen.findByText(/Saved revision 3\./);
  expect(commands.map((command) => command.expectedRevision)).toEqual([0, 1, 2]);
  expect(commands.map((command) => command.initialCreatedFrom)).toEqual([
    "2020-01-01T00:00:00Z",
    null,
    null,
  ]);
  expect(commands.map((command) => command.rescan)).toEqual([false, false, true]);
  expect(commands[0]?.configuration.workflowIds).toEqual([101]);
  expect(commands[0]?.configuration.detailRetention).toEqual({
    mode: "days",
    days: 730,
    anchor: "first_successful_detail_import",
  });
  expect(commands[0]?.configuration.quota.jobs).toBe(50_000);
  expect(commands[0]).not.toHaveProperty("actor");
  expect(commands[0]).not.toHaveProperty("expandCreatedFrom");
  expect(new Set(commands.map((command) => command.operationId)).size).toBe(3);
});
test("an existing archive extends only to an earlier UTC date with unchanged settings", async () => {
  const commands: HistoryCommand[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const command: HistoryCommand = await request.json();
      commands.push(command);
      return Response.json(historyMutation(command));
    }),
  );
  const view = render(<HistoryEditor {...props()} />);
  const save = screen.getByRole("button", { name: "Save history" });
  const date = screen.getByLabelText("Extend import back to (UTC)");

  fireEvent.change(date, { target: { value: "2020-01-01" } });
  expect(save).toBeDisabled();
  fireEvent.change(date, { target: { value: "2019-12-01" } });
  expect(save).toBeEnabled();
  expect(screen.getByText(/rereads existing history/)).toBeVisible();
  await userEvent.click(save);

  expect(commands).toHaveLength(1);
  expect(commands[0]).toMatchObject({
    expectedRevision: 1,
    initialCreatedFrom: null,
    expandCreatedFrom: "2019-12-01T00:00:00Z",
    rescan: false,
    configuration: historyConfiguration(),
  });
  await screen.findByText(/Saved revision 2\./);
  expect(screen.getByLabelText("Extend import back to (UTC)")).toHaveValue("");
  fireEvent.change(date, { target: { value: "2019-12-15" } });
  expect(save).toBeDisabled();
  const refreshed = historyStatus(historyDataset(historyConfiguration(), 2));
  if (refreshed.scan === null) throw new Error("configured history requires scan progress");
  view.rerender(
    <HistoryEditor
      {...props()}
      status={{
        ...refreshed,
        scan: {
          ...refreshed.scan,
          createdFrom: "2019-12-01T00:00:00Z",
          windowFrom: "2019-12-01T00:00:00Z",
          windowThrough: "2019-12-08T00:00:00Z",
        },
      }}
    />,
  );
  fireEvent.change(date, { target: { value: "2019-11-01" } });
  expect(save).toBeEnabled();
});
test("expansion cannot be combined with a rescan or another settings edit", async () => {
  vi.stubGlobal("fetch", vi.fn());
  render(<HistoryEditor {...props()} />);
  const date = screen.getByLabelText("Extend import back to (UTC)");
  const save = screen.getByRole("button", { name: "Save history" });
  fireEvent.change(date, { target: { value: "2019-12-01" } });
  await userEvent.click(screen.getByRole("checkbox", { name: "Collection enabled" }));
  expect(save).toBeDisabled();
  await userEvent.click(screen.getByRole("checkbox", { name: "Collection enabled" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Rescan the configured history" }));
  expect(screen.getByLabelText("Extend import back to (UTC)")).toHaveValue("");
});
test.each(["inherit", "disabled", "forever"])(
  "retention choice %s preserves a distinct contract",
  async (mode) => {
    const commands: HistoryCommand[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const command: HistoryCommand = await request.json();
        commands.push(command);
        return Response.json(historyMutation(command));
      }),
    );
    render(<HistoryEditor {...props()} />);
    await userEvent.click(screen.getByText("Retention and capacity limits"));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Detailed records" }), mode);
    await userEvent.click(screen.getByRole("checkbox", { name: "Collection enabled" }));
    await userEvent.click(screen.getByRole("button", { name: "Save history" }));
    await screen.findByText(/Saved revision 2\./);
    expect(commands[0]?.configuration.detailRetention).toEqual(
      mode === "inherit" ? null : { mode },
    );
  },
);
test("polling preserves a dirty draft and requires explicit reload of a newer base", async () => {
  const input = props();
  const view = render(<HistoryEditor {...input} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Collection enabled" }));
  view.rerender(
    <HistoryEditor {...input} status={historyStatus(historyDataset(historyConfiguration(), 2))} />,
  );
  expect(screen.getByRole("checkbox", { name: "Collection enabled" })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Save history" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Reload saved configuration" }));
  expect(screen.getByRole("checkbox", { name: "Collection enabled" })).toBeChecked();
  expect(screen.getByText(/Saved revision 2\./)).toBeVisible();
});
test("lost acknowledgement freezes a command and only explicit identical retry resolves it", async () => {
  const pending = Promise.withResolvers<Response>();
  const commands: HistoryCommand[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const command: HistoryCommand = await request.json();
      commands.push(command);
      return commands.length === 1
        ? pending.promise
        : Response.json({ ...historyMutation(command), outcome: "replayed" });
    }),
  );
  render(<HistoryEditor {...props()} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Collection enabled" }));
  fireEvent.submit(screen.getByRole("form", { name: "Historical collection configuration" }));
  fireEvent.submit(screen.getByRole("form", { name: "Historical collection configuration" }));
  await waitFor(() => expect(commands).toHaveLength(1));
  await act(async () => pending.reject(new TypeError("offline")));
  await screen.findByText(/Save outcome unknown/);
  expect(screen.getByRole("checkbox", { name: "Collection enabled" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reload saved configuration" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Retry same operation" }));
  await screen.findByText(/Saved revision 2\./);
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
});
test.each([
  "revision_conflict",
  "operation_conflict",
  "capacity_reached",
  "dataset_fenced",
  "pending_work",
  "invalid_population",
])("rejection %s preserves the draft without pretending to save", async (outcome) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const command: HistoryCommand = await request.json();
      return Response.json(
        { ...historyMutation(command), snapshot: null, outcome },
        { status: outcome === "invalid_population" ? 400 : 409 },
      );
    }),
  );
  render(<HistoryEditor {...props()} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Collection enabled" }));
  await userEvent.click(screen.getByRole("button", { name: "Save history" }));
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.getByText(/Saved revision 1\./)).toBeVisible();
  expect(screen.getByRole("button", { name: "Save history" })).toBeDisabled();
});
test.each(["stale", "role", "erasing", "erased"])("%s state prevents every write", async (kind) => {
  const input = props();
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const snapshot = historyDataset({ ...historyConfiguration(), enabled: false });
  render(
    <HistoryEditor
      {...input}
      stale={kind === "stale"}
      session={kind === "role" ? undefined : session}
      status={
        kind === "erasing" || kind === "erased"
          ? historyStatus({
              ...snapshot,
              state: kind,
              usage: { attempts: 0, jobs: 0, gaps: 0, canonicalBytes: 0 },
            })
          : input.status
      }
    />,
  );
  expect(screen.getByRole("checkbox", { name: "Collection enabled" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save history" })).toBeDisabled();
  expect(fetch).not.toHaveBeenCalled();
});
test.each(["scope", "authority"] as const)(
  "production console fences pending writes after %s replacement",
  async (kind) => {
    const pending = Promise.withResolvers<Response>();
    let writeRequest: Request | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "POST") {
          writeRequest = request;
          return pending.promise;
        }
        return Response.json(
          new URL(request.url).pathname.endsWith("/history")
            ? historyStatus()
            : economicsSourcePage(),
        );
      }),
    );
    const view = render(
      <EconomicsConsole scope={HISTORY_SCOPE} authorityRevision={1} session={session} />,
    );
    await userEvent.click(screen.getByRole("tab", { name: "History" }));
    await userEvent.click(await screen.findByRole("radio", { name: "Collection settings" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "Collection enabled" }));
    await userEvent.click(screen.getByRole("button", { name: "Save history" }));
    await waitFor(() => expect(writeRequest).toBeDefined());
    view.rerender(
      <EconomicsConsole
        scope={{ ...HISTORY_SCOPE, repositoryId: kind === "scope" ? 2 : 1 }}
        authorityRevision={kind === "authority" ? 2 : 1}
        session={session}
      />,
    );
    expect(writeRequest?.signal.aborted).toBe(true);
    await act(async () => pending.resolve(Response.json(historyMutation())));
    expect(screen.queryByRole("button", { name: "Saving" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: kind === "scope" ? "Registered runs" : "History" }),
    ).toHaveAttribute("aria-selected", "true");
  },
);

test.each([
  { tab: "History", toggle: "Collection enabled", save: "Save history", initialMissing: false },
  { tab: "History", toggle: "Collection enabled", save: "Save history", initialMissing: true },
  {
    tab: "Observation",
    toggle: "Observation enabled",
    save: "Save observation",
    initialMissing: false,
  },
])(
  "$tab keeps the exact unresolved command across navigation; initialMissing=$initialMissing",
  async ({ tab, toggle, save, initialMissing }) => {
    const commands: unknown[] = [];
    let refreshedHistory = historyStatus(initialMissing ? null : historyDataset());
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const history = tab === "History";
        if (request.method === "POST") {
          const raw: unknown = await request.json();
          commands.push(raw);
          const result = history
            ? historyMutation(historyCommandSchema.parse(raw))
            : observationMutation(observationCommandSchema.parse(raw));
          if (commands.length === 1) {
            if (history)
              refreshedHistory = historyStatus(
                historyMutation(historyCommandSchema.parse(raw)).snapshot,
              );
            throw new TypeError("lost reply");
          }
          return Response.json({ ...result, outcome: "replayed" });
        }
        const path = new URL(request.url).pathname;
        return Response.json(
          path.endsWith("/history")
            ? refreshedHistory
            : path.endsWith("/observation")
              ? observationStatus()
              : economicsSourcePage(),
        );
      }),
    );
    render(<EconomicsConsole scope={HISTORY_SCOPE} authorityRevision={1} session={session} />);
    await userEvent.click(screen.getByRole("tab", { name: tab }));
    if (tab === "History")
      await userEvent.click(await screen.findByRole("radio", { name: "Collection settings" }));
    if (initialMissing)
      fireEvent.change(screen.getByLabelText("Import runs created since (UTC)"), {
        target: { value: "2020-01-01" },
      });
    await userEvent.click(await screen.findByRole("checkbox", { name: toggle }));
    await userEvent.click(screen.getByRole("button", { name: save }));
    await screen.findByText(/Save outcome unknown/);
    await userEvent.click(screen.getByRole("tab", { name: "Registered runs" }));
    expect(screen.queryByRole("button", { name: "Retry same operation" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: tab }));
    expect(screen.getByRole("checkbox", { name: toggle })).toBeDisabled();
    await userEvent.click(await screen.findByRole("button", { name: "Retry same operation" }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: toggle })).toBeEnabled());
    expect(commands).toHaveLength(2);
    expect(commands[1]).toEqual(commands[0]);
  },
);
