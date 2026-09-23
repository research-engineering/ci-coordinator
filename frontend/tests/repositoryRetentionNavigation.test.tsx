import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { archiveQuerySchema } from "../src/api/ciEconomics/archiveReadSchema";
import { archivePage, retentionPreview } from "./archiveFixture";
import { RepositoryWorkspace } from "./consoleSelectionHarness";
import { controlPlaneSessionFixture, workbenchFixture } from "./fixture";
import { historyStatus } from "./historyFixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("sidebar navigation retains an uncertain retention operation without hidden reads or replay", async () => {
  const commands: unknown[] = [];
  const requests: Request[] = [];
  const status = historyStatus();
  const reviewed = retentionPreview();
  reviewed.preview.selection.repositoryId = 1;
  reviewed.preview.selection.generation = 1;
  const fetch = vi.fn(async (request: Request) => {
    requests.push(request);
    const url = new URL(request.url);
    if (url.pathname.endsWith("/retention/preview")) return Response.json(reviewed);
    if (url.pathname.endsWith("/retention/apply")) {
      const command = (await request.json()) as { operationId: string };
      commands.push(command);
      if (commands.length === 1) throw new TypeError("lost response");
      return Response.json({
        outcome: "replayed",
        operationId: command.operationId,
        preview: reviewed.preview,
        dataRevision: 6,
      });
    }
    if (url.pathname.endsWith("/history")) return Response.json(status);
    if (url.pathname.includes("/history/archive/")) {
      const query = archiveQuerySchema.parse({
        installationId: 1,
        repositoryId: 1,
        generation: 1,
        kind: url.pathname.endsWith("/jobs") ? "jobs" : "records",
        workflowRunId: url.searchParams.has("workflow_run_id")
          ? Number(url.searchParams.get("workflow_run_id"))
          : null,
        runAttempt: url.searchParams.has("run_attempt")
          ? Number(url.searchParams.get("run_attempt"))
          : null,
      });
      const page = archivePage(query);
      return Response.json({
        ...page,
        records: page.records.map((record) => ({
          ...record,
          header: { ...record.header, attempt: { ...record.header.attempt, repositoryId: 1 } },
        })),
      });
    }
    return Response.json(workbenchFixture());
  });
  vi.stubGlobal("fetch", fetch);
  const props = {
    authorityRevision: 1,
    scope: { installationId: 1, repositoryId: 1, limit: 10 },
    session: controlPlaneSessionFixture({ roles: ["audit", "configure", "read"] }),
    tab: "plans" as const,
    onTab: vi.fn(),
  };
  const view = render(<RepositoryWorkspace {...props} view="economics" />);
  await userEvent.click(screen.getByRole("tab", { name: "History" }));
  await userEvent.click(await screen.findByRole("button", { name: /#101/ }));
  await userEvent.click(await screen.findByRole("button", { name: "Manage detail retention" }));
  await userEvent.click(screen.getByRole("button", { name: "Preview detail deletion" }));
  await userEvent.click(await screen.findByRole("button", { name: "Confirm reviewed change" }));
  await screen.findByText(/The result is unknown/);
  view.rerender(<RepositoryWorkspace {...props} view="overview" />);
  const count = requests.length;
  await act(async () => {});
  expect(requests).toHaveLength(count);
  expect(commands).toHaveLength(1);
  view.rerender(<RepositoryWorkspace {...props} view="economics" />);
  await userEvent.click(await screen.findByRole("button", { name: "Confirm existing operation" }));
  await screen.findByText(/Retention change confirmed/);
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
  view.rerender(<RepositoryWorkspace {...props} authorityRevision={2} view="economics" />);
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Confirm existing operation" }),
    ).not.toBeInTheDocument(),
  );
  expect(commands).toHaveLength(2);
});
