import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ArchiveBrowser } from "../src/features/ciEconomics/ArchiveBrowser";
import { ArchiveRetention } from "../src/features/ciEconomics/ArchiveRetention";
import {
  archiveDetailPage,
  archivePage,
  archiveQuery,
  archiveRecord,
  retentionPreview,
} from "./archiveFixture";
import { controlPlaneSessionFixture } from "./fixture";
import { historyStatus } from "./historyFixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const scope = { installationId: 1, repositoryId: 2, limit: 10 };
function props() {
  const status = historyStatus();
  return {
    scope,
    status: {
      ...status,
      installationId: 1,
      repositoryId: 2,
      snapshot:
        status.snapshot === null ? null : { ...status.snapshot, repositoryId: 2, generation: 3 },
    },
    session: controlPlaneSessionFixture({ roles: ["read", "audit", "configure"] }),
    onChanged: vi.fn(),
  };
}

test("records drill down to job evidence without mutation; inactive reads stop", async () => {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      if (new URL(request.url).pathname.includes("/history/attempts/"))
        return Response.json(archiveDetailPage());
      return Response.json(
        archivePage(
          archiveQuery(new URL(request.url).pathname.endsWith("jobs") ? "jobs" : "records"),
        ),
      );
    }),
  );
  const input = props();
  const view = render(<ArchiveBrowser {...input} />);
  const select = await screen.findByRole("button", { name: /#101/ });
  expect(requests.some((request) => request.url.includes("/history/attempts/"))).toBe(false);
  await userEvent.click(select);
  await screen.findByText("Ruff");
  expect(await screen.findByText(/Step 1: success/)).toBeVisible();
  expect(requests.every((request) => request.method === "GET")).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Manage detail retention" }));
  expect(screen.getByRole("region", { name: "Optional detail retention" })).toBeInTheDocument();
  const count = requests.length;
  view.rerender(<ArchiveBrowser {...input} active={false} />);
  await act(async () => {});
  expect(requests).toHaveLength(count);
});

test.each([
  ["retained", "Numbered steps are retained but unavailable in a recognized format."],
  ["not_imported", "Numbered steps were not imported for this attempt."],
  ["expired", "Numbered steps expired; retained statistics remain available."],
] as const)("selected attempt shows honest nullable %s detail", async (state, message) => {
  const page = archiveDetailPage();
  page.detailPayload = null;
  page.detail =
    state === "not_imported"
      ? archiveRecord().detail
      : { ...page.detail, state, content: state === "expired" ? "expired" : "unavailable_format" };
  page.record.detail = page.detail;
  if (state === "expired") page.observedAt = "2026-10-12T10:00:00Z";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.includes("/history/attempts/")) return Response.json(page);
      return Response.json(
        path.endsWith("/jobs") ? archivePage(archiveQuery("jobs")) : archivePage(),
      );
    }),
  );
  render(<ArchiveBrowser {...props()} />);
  await userEvent.click(await screen.findByRole("button", { name: /#101/ }));
  expect(await screen.findByText(message)).toBeVisible();
});

test("late detail response is fenced after a scope replacement", async () => {
  const pending = Promise.withResolvers<Response>();
  let detailRequest: Request | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.includes("/history/attempts/")) {
        detailRequest = request;
        return pending.promise;
      }
      return Response.json(
        path.endsWith("/jobs") ? archivePage(archiveQuery("jobs")) : archivePage(),
      );
    }),
  );
  const input = props();
  const view = render(<ArchiveBrowser {...input} />);
  await userEvent.click(await screen.findByRole("button", { name: /#101/ }));
  await screen.findByText("Ruff");
  await waitFor(() => expect(detailRequest).toBeDefined());
  view.rerender(<ArchiveBrowser {...input} scope={{ ...scope, repositoryId: 9 }} />);
  expect(detailRequest?.signal.aborted).toBe(true);
  await act(async () => pending.resolve(Response.json(archiveDetailPage())));
  expect(screen.queryByText(/Step 1:/)).not.toBeInTheDocument();
});

test("unknown retention writes keep one operation and do not silently replay", async () => {
  const reviewed = retentionPreview();
  const commands: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (new URL(request.url).pathname.endsWith("preview")) return Response.json(reviewed);
      const command = (await request.json()) as { operationId: string };
      commands.push(command);
      if (commands.length === 1) throw new TypeError("lost reply");
      return Response.json({
        outcome: "replayed",
        operationId: command.operationId,
        dataRevision: 6,
        preview: reviewed.preview,
      });
    }),
  );
  const input = {
    page: archivePage(archiveQuery("jobs")),
    defaultRevision: 1,
    session: props().session,
    onChanged: vi.fn(),
    onClose: vi.fn(),
  };
  render(<ArchiveRetention {...input} />);
  await userEvent.click(screen.getByRole("button", { name: "Preview detail deletion" }));
  await userEvent.click(await screen.findByRole("button", { name: "Confirm reviewed change" }));
  await screen.findByText(/The result is unknown/);
  expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
  expect(commands).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Confirm existing operation" }));
  await screen.findByText(/Retention change confirmed/);
  expect(commands).toHaveLength(2);
  expect(commands[0]).toEqual(commands[1]);
  expect(input.onChanged).toHaveBeenCalledTimes(1);
});

test("scope replacement cancels an obsolete archive request", async () => {
  const pending = Promise.withResolvers<Response>();
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      requests.push(request);
      return pending.promise;
    }),
  );
  const input = props();
  const view = render(<ArchiveBrowser {...input} />);
  await waitFor(() => expect(requests).toHaveLength(1));
  view.rerender(<ArchiveBrowser {...input} scope={{ ...scope, repositoryId: 9 }} />);
  expect(requests[0]?.signal.aborted).toBe(true);
  await act(async () => pending.resolve(Response.json(archivePage())));
  expect(screen.queryByText(".github/workflows/fullcheck.yml")).not.toBeInTheDocument();
});
