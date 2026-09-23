import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import type { PurposeSettingsCommand } from "../src/api/ciEconomics/purposeSettingsSchema";
import { PurposeSettingsPanel } from "../src/features/ciEconomics/PurposeSettingsPanel";
import { EconomicsConsole } from "./consoleSelectionHarness";
import { economicsSourcePage } from "./economicsConsoleFixture";
import { controlPlaneSessionFixture } from "./fixture";
import { historyStatus } from "./historyFixture";
import { purposeCommand, purposeQuery, purposeSnapshot } from "./purposeSettingsFixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function addRow() {
  await userEvent.click(await screen.findByText("Analytics settings", { selector: "summary" }));
  await userEvent.click(await screen.findByRole("button", { name: "Add mapping" }));
  await userEvent.type(screen.getByLabelText("Workflow ID 1"), "17");
  await userEvent.type(screen.getByLabelText("Exact job name 1"), "Ruff");
  await userEvent.click(screen.getByRole("checkbox", { name: "lint" }));
}

test("row editor validates, saves explicit categories and confirms the revision", async () => {
  const saved = vi.fn();
  let snapshot = purposeSnapshot();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "PUT") {
        const command = (await request.json()) as PurposeSettingsCommand;
        expect(command.entries).toEqual(purposeCommand().entries);
        snapshot = purposeSnapshot(command);
        return Response.json({ operationId: command.operationId, outcome: "committed", snapshot });
      }
      return Response.json({ outcome: "available", snapshot, unavailable: null });
    }),
  );
  render(
    <PurposeSettingsPanel
      query={purposeQuery}
      session={controlPlaneSessionFixture()}
      active
      onSaved={saved}
    />,
  );
  await addRow();
  await userEvent.click(screen.getByRole("button", { name: "Save analytics settings" }));
  await screen.findByText("Saved analytics settings, revision 1.");
  expect(saved).toHaveBeenCalledOnce();
  await waitFor(() => expect(screen.getByRole("button", { name: "Add mapping" })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: "Remove mapping 1" }));
  expect(screen.queryByLabelText("Exact job name 1")).not.toBeInTheDocument();
});

test.each(["lost", "ambiguous"])(
  "%s result retains identical operation across same-scope navigation",
  async (failure) => {
    const commands: PurposeSettingsCommand[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname;
        if (request.method === "PUT") {
          const command = (await request.json()) as PurposeSettingsCommand;
          commands.push(command);
          if (commands.length === 1) {
            if (failure === "lost") throw new TypeError("lost response");
            return new Response(
              JSON.stringify({
                operationId: command.operationId,
                outcome: "committed",
                snapshot: purposeSnapshot(command),
              }).replace('"repositoryId":1', '"repositoryId":2,"repositoryId":1'),
              { headers: { "content-type": "application/json" } },
            );
          }
          return Response.json({
            operationId: command.operationId,
            outcome: "replayed",
            snapshot: purposeSnapshot(command),
          });
        }
        if (path.endsWith("/history")) return Response.json(historyStatus());
        if (path.endsWith("/analytics/settings"))
          return Response.json({
            outcome: "available",
            snapshot: purposeSnapshot(),
            unavailable: null,
          });
        if (path.endsWith("/analytics"))
          return Response.json({
            outcome: "unavailable",
            report: null,
            unavailable: { reason: "purpose_mapping_unavailable" },
          });
        return Response.json(economicsSourcePage());
      }),
    );
    const session = controlPlaneSessionFixture();
    const props = { scope: { ...purposeQuery, limit: 10 }, session, authorityRevision: 1 };
    const rendered = render(<EconomicsConsole {...props} />);
    await userEvent.click(screen.getByRole("tab", { name: "Analytics" }));
    await addRow();
    await userEvent.click(screen.getByRole("button", { name: "Save analytics settings" }));
    await screen.findByText(/Save outcome unknown/);
    expect(screen.queryByText("Saved analytics settings, revision 1.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save analytics settings" })).toBeDisabled();
    await userEvent.click(screen.getByRole("tab", { name: "Registered runs" }));
    rendered.rerender(<EconomicsConsole {...props} active={false} />);
    rendered.rerender(<EconomicsConsole {...props} active />);
    await userEvent.click(screen.getByRole("tab", { name: "Analytics" }));
    const retry = await screen.findByRole("button", { name: "Retry same operation" });
    await waitFor(() => expect(retry).toBeEnabled());
    expect(screen.getByLabelText("Exact job name 1")).toBeDisabled();
    await userEvent.click(retry);
    await screen.findByText("Saved analytics settings, revision 1.");
    expect(commands).toHaveLength(2);
    expect(commands[1]).toEqual(commands[0]);
  },
);

test("read-only administrator cannot save and duplicate mapping remains invalid", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ outcome: "available", snapshot: purposeSnapshot(), unavailable: null }),
    ),
  );
  const view = render(
    <PurposeSettingsPanel
      query={purposeQuery}
      session={controlPlaneSessionFixture({ roles: ["audit"] })}
      active
      onSaved={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByText("Analytics settings", { selector: "summary" }));
  expect(await screen.findByRole("button", { name: "Save analytics settings" })).toBeDisabled();
  view.unmount();
  render(
    <PurposeSettingsPanel
      query={purposeQuery}
      session={controlPlaneSessionFixture()}
      active
      onSaved={vi.fn()}
    />,
  );
  await addRow();
  await userEvent.click(screen.getByRole("button", { name: "Add mapping" }));
  await userEvent.type(screen.getByLabelText("Workflow ID 2"), "17");
  await userEvent.type(screen.getByLabelText("Exact job name 2"), "Ruff");
  await userEvent.click(
    within(screen.getByRole("group", { name: "Categories 2" })).getByRole("checkbox", {
      name: "test",
    }),
  );
  expect(screen.getByRole("button", { name: "Save analytics settings" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("Duplicate workflow/job pairs");
});

test("scope and session changes discard the old unknown command", async () => {
  const commands: PurposeSettingsCommand[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "PUT") {
        commands.push((await request.json()) as PurposeSettingsCommand);
        throw new TypeError("lost response");
      }
      const repositoryId = new URL(request.url).pathname.includes("/1/2/") ? 2 : 1;
      return Response.json({
        outcome: "available",
        snapshot: { ...purposeSnapshot(), repositoryId },
        unavailable: null,
      });
    }),
  );
  const session = controlPlaneSessionFixture({ roles: ["audit", "configure"] });
  const view = render(
    <PurposeSettingsPanel query={purposeQuery} session={session} active onSaved={vi.fn()} />,
  );
  await addRow();
  await userEvent.click(screen.getByRole("button", { name: "Save analytics settings" }));
  await screen.findByText(/Save outcome unknown/);
  view.rerender(
    <PurposeSettingsPanel
      query={{ ...purposeQuery, repositoryId: 2 }}
      session={session}
      active
      onSaved={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByText("Analytics settings", { selector: "summary" }));
  expect(await screen.findByRole("button", { name: "Add mapping" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Retry same operation" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Exact job name 1")).not.toBeInTheDocument();
  view.rerender(
    <PurposeSettingsPanel
      query={purposeQuery}
      session={{
        ...session,
        user: { ...session.user, actorId: `keycloak-human:v1:${"b".repeat(64)}` },
      }}
      active
      onSaved={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByText("Analytics settings", { selector: "summary" }));
  expect(await screen.findByRole("button", { name: "Add mapping" })).toBeEnabled();
  expect(commands).toHaveLength(1);
});
