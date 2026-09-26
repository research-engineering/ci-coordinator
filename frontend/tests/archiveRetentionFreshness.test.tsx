import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ArchiveBrowser } from "../src/features/ciEconomics/ArchiveBrowser";
import { archiveDetailPage, archivePage, archiveQuery, retentionPreview } from "./archiveFixture";
import { controlPlaneSessionFixture } from "./fixture";
import { historyStatus } from "./historyFixture";

const scope = { installationId: 1, repositoryId: 2, limit: 10 };
function props() {
  const status = historyStatus();
  return {
    scope,
    session: controlPlaneSessionFixture({ roles: ["read", "audit", "configure"] }),
    status: {
      ...status,
      installationId: 1,
      repositoryId: 2,
      snapshot:
        status.snapshot === null ? null : { ...status.snapshot, repositoryId: 2, generation: 3 },
    },
    onChanged: vi.fn(),
  };
}
function detail(revision = 5, expired = false) {
  const value = archiveDetailPage();
  value.dataRevision = revision;
  if (expired) {
    value.detail = { ...value.detail, state: "expired", content: "expired" };
    value.record.detail = value.detail;
    value.detailPayload = null;
    value.observedAt = "2026-10-12T10:00:00Z";
  }
  return value;
}
function jobs(revision = 5, expired = false) {
  const value = archivePage(archiveQuery("jobs"));
  value.dataRevision = revision;
  value.observedAt = detail(revision, expired).observedAt;
  value.records = [detail(revision, expired).record];
  return value;
}
function preview() {
  const value = retentionPreview();
  value.preview.effects[0] = {
    key: { workflowRunId: 101, runAttempt: 1 },
    before: detail().detail,
    after: detail(6, true).detail,
    payloadBytes: 128,
    deletePayload: true,
  };
  value.preview.deletedDetails = 1;
  value.preview.releasedBytes = 128;
  return value;
}
async function openRetention() {
  await userEvent.click(await screen.findByRole("button", { name: /#101/ }));
  await screen.findByText(/Step 1: success/);
  await userEvent.click(await screen.findByRole("button", { name: "Manage detail retention" }));
  await userEvent.click(screen.getByRole("button", { name: "Preview detail deletion" }));
  await screen.findByRole("button", { name: "Confirm reviewed change" });
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("confirmed deletion invalidates both selected reads, retains a receipt and binds the next preview to revision 6", async () => {
  const nextJobs = Promise.withResolvers<Response>();
  const nextDetail = Promise.withResolvers<Response>();
  let applied = false;
  const selections: unknown[] = [];
  const reads: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/preview")) {
        const selection = (await request.json()) as ReturnType<
          typeof retentionPreview
        >["preview"]["selection"];
        selections.push(selection);
        const value = preview();
        value.preview.selection = selection;
        return Response.json(value);
      }
      if (path.endsWith("/apply")) {
        const command = (await request.json()) as { operationId: string };
        applied = true;
        return Response.json({
          outcome: "committed",
          operationId: command.operationId,
          dataRevision: 6,
          preview: preview().preview,
        });
      }
      reads.push(request);
      if (path.includes("/history/attempts/"))
        return applied ? nextDetail.promise : Response.json(detail());
      if (path.endsWith("/jobs")) return applied ? nextJobs.promise : Response.json(jobs());
      return Response.json(archivePage());
    }),
  );
  const input = props();
  render(<ArchiveBrowser {...input} />);
  await openRetention();
  await userEvent.click(screen.getByRole("button", { name: "Confirm reviewed change" }));
  const receipt = await screen.findByRole("status", { name: "Retention operation receipt" });
  expect(within(receipt).getByText(/Recorded data revision 6/)).toBeVisible();
  await waitFor(() => expect(reads).toHaveLength(5));
  expect(screen.queryByText(/Step 1: success/)).not.toBeInTheDocument();
  expect(screen.queryByText("Ruff")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Manage detail retention" })).not.toBeInTheDocument();
  await act(async () => {
    nextJobs.resolve(Response.json(jobs(6, true)));
    nextDetail.resolve(Response.json(detail(6, true)));
  });
  expect(await screen.findByText("Ruff")).toBeVisible();
  expect(
    await screen.findByText("Numbered steps expired; retained statistics remain available."),
  ).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Manage detail retention" }));
  await userEvent.click(screen.getByRole("button", { name: "Preview detail deletion" }));
  await waitFor(() => expect(selections).toHaveLength(2));
  expect(selections[0]).toMatchObject({ dataRevision: 5, configurationRevision: 4 });
  expect(selections[1]).toMatchObject({
    dataRevision: 6,
    configurationRevision: 4,
    keys: [{ workflowRunId: 101, runAttempt: 1 }],
  });
  expect(input.onChanged).toHaveBeenCalledTimes(1);
});

test.each(["review", "uncertain", "applying"] as const)(
  "manual read refresh invalidates a %s preview source without discarding an unresolved operation",
  async (phase) => {
    const inFlight = Promise.withResolvers<Response>();
    const bodies: string[] = [];
    let jobReads = 0;
    let detailReads = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname;
        if (path.endsWith("/preview")) return Response.json(preview());
        if (path.endsWith("/apply")) {
          bodies.push(await request.text());
          if (phase === "applying") return inFlight.promise;
          throw new TypeError("unknown outcome");
        }
        if (path.includes("/history/attempts/")) {
          detailReads++;
          return Response.json(detail());
        }
        if (path.endsWith("/jobs")) {
          jobReads++;
          return Response.json(jobs());
        }
        return Response.json(archivePage());
      }),
    );
    const input = props();
    const view = render(<ArchiveBrowser {...input} />);
    await openRetention();
    if (phase !== "review") {
      await userEvent.click(screen.getByRole("button", { name: "Confirm reviewed change" }));
      await screen.findByText(
        phase === "uncertain" ? /The result is unknown/ : "Applying reviewed change",
      );
    }
    await userEvent.click(
      within(screen.getByRole("region", { name: "Retained attempt" })).getByRole("button", {
        name: "Refresh evidence",
      }),
    );
    await waitFor(() => {
      expect(jobReads).toBe(2);
      expect(detailReads).toBe(2);
    });
    if (phase === "review") {
      expect(screen.getByRole("button", { name: "Confirm reviewed change" })).toBeDisabled();
      expect(bodies).toEqual([]);
    } else {
      expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
      view.rerender(<ArchiveBrowser {...input} active={false} />);
      view.rerender(<ArchiveBrowser {...input} active />);
      if (phase === "uncertain") {
        await userEvent.click(screen.getByRole("button", { name: "Confirm existing operation" }));
        await waitFor(() => expect(bodies).toHaveLength(2));
        expect(bodies[1]).toBe(bodies[0]);
        expect(JSON.parse(bodies[1] ?? "")).toMatchObject({
          selection: { dataRevision: 5 },
          reviewedDigest: "b".repeat(64),
        });
      } else {
        expect(bodies).toHaveLength(1);
        await act(async () =>
          inFlight.resolve(Response.json({ ok: false, error: "unavailable" }, { status: 503 })),
        );
        await screen.findByText(/The result is unknown/);
      }
    }
    expect(input.onChanged).not.toHaveBeenCalled();
  },
);

test("a confirmed refusal is not a deletion receipt and an old revision cannot satisfy a confirmed data floor", async () => {
  let outcome: "refused" | "applied" = "refused";
  const input = props();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/preview")) return Response.json(preview());
      if (path.endsWith("/apply")) {
        const { operationId } = (await request.json()) as { operationId: string };
        return Response.json(
          {
            outcome: outcome === "refused" ? "revision_conflict" : "committed",
            operationId,
            dataRevision: outcome === "refused" ? null : 6,
            preview: outcome === "refused" ? null : preview().preview,
          },
          { status: outcome === "refused" ? 409 : 200 },
        );
      }
      if (path.includes("/history/attempts/")) return Response.json(detail());
      if (path.endsWith("/jobs")) return Response.json(jobs());
      return Response.json(archivePage());
    }),
  );
  render(<ArchiveBrowser {...input} />);
  await openRetention();
  await userEvent.click(screen.getByRole("button", { name: "Confirm reviewed change" }));
  await screen.findByText(/No new change was admitted/);
  expect(screen.queryByText(/Retention change confirmed/)).not.toBeInTheDocument();
  outcome = "applied";
  await userEvent.click(await screen.findByRole("button", { name: "Manage detail retention" }));
  await userEvent.click(screen.getByRole("button", { name: "Preview detail deletion" }));
  await userEvent.click(await screen.findByRole("button", { name: "Confirm reviewed change" }));
  await screen.findByText(/Retention change confirmed/);
  await waitFor(() =>
    expect(screen.getAllByText("Evidence could not be validated")).toHaveLength(2),
  );
  expect(screen.queryByText(/Step 1: success/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Manage detail retention" })).not.toBeInTheDocument();
});

test("hidden return retires an unsubmitted preview even without manual refresh", async () => {
  let revision = 5;
  const writes: unknown[] = [];
  const previews: unknown[] = [];
  let jobsRead = 0;
  let detailsRead = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/preview")) {
        const selection = (await request.json()) as ReturnType<
          typeof retentionPreview
        >["preview"]["selection"];
        previews.push(selection);
        const value = preview();
        value.preview.selection = selection;
        return Response.json(value);
      }
      if (path.endsWith("/apply")) {
        writes.push(await request.json());
        return Response.json({ ok: false, error: "unavailable" }, { status: 503 });
      }
      if (path.includes("/history/attempts/")) {
        detailsRead++;
        return Response.json(detail(revision));
      }
      if (path.endsWith("/jobs")) {
        jobsRead++;
        return Response.json(jobs(revision));
      }
      return Response.json(archivePage());
    }),
  );
  const input = props();
  const view = render(<ArchiveBrowser {...input} />);
  await openRetention();
  expect(screen.getByRole("button", { name: "Confirm reviewed change" })).toBeEnabled();
  expect(previews).toEqual([expect.objectContaining({ dataRevision: 5 })]);
  view.rerender(<ArchiveBrowser {...input} active={false} />);
  revision = 6;
  view.rerender(<ArchiveBrowser {...input} active />);
  await waitFor(() => {
    expect(jobsRead).toBe(2);
    expect(detailsRead).toBe(2);
  });
  await screen.findByText("Ruff");
  expect(screen.getByRole("button", { name: "Confirm reviewed change" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Confirm reviewed change" }));
  expect(writes).toEqual([]);
  expect(input.onChanged).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await userEvent.click(screen.getByRole("button", { name: "Manage detail retention" }));
  await userEvent.click(screen.getByRole("button", { name: "Preview detail deletion" }));
  await screen.findByRole("button", { name: "Confirm reviewed change" });
  expect(previews).toEqual([
    expect.objectContaining({ dataRevision: 5 }),
    expect.objectContaining({ dataRevision: 6 }),
  ]);
  expect(screen.getByRole("button", { name: "Confirm reviewed change" })).toBeEnabled();
});

test("hidden return preserves uncertain Q and retries selection 5 after a new revision-6 read", async () => {
  let revision = 5;
  const bodies: string[] = [];
  const reviewed = preview();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/preview")) return Response.json(reviewed);
      if (path.endsWith("/apply")) {
        const body = await request.text();
        bodies.push(body);
        if (bodies.length === 1) throw new TypeError("lost retention receipt");
        const command = JSON.parse(body) as { operationId: string };
        return Response.json({
          outcome: "replayed",
          operationId: command.operationId,
          dataRevision: 6,
          preview: reviewed.preview,
        });
      }
      if (path.includes("/history/attempts/"))
        return Response.json(detail(revision, revision === 6));
      if (path.endsWith("/jobs")) return Response.json(jobs(revision, revision === 6));
      return Response.json(archivePage());
    }),
  );
  const input = props();
  const view = render(<ArchiveBrowser {...input} />);
  await openRetention();
  await userEvent.click(screen.getByRole("button", { name: "Confirm reviewed change" }));
  await screen.findByText(/The result is unknown/);
  view.rerender(<ArchiveBrowser {...input} active={false} />);
  revision = 6;
  view.rerender(<ArchiveBrowser {...input} active />);
  await screen.findByText("Ruff");
  await screen.findByText("Numbered steps expired; retained statistics remain available.");
  expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
  expect(bodies).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Confirm existing operation" }));
  await screen.findByText(/Retention change confirmed/);
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toBe(bodies[0]);
  expect(JSON.parse(bodies[1] ?? "")).toMatchObject({
    selection: { dataRevision: 5 },
    reviewedDigest: "b".repeat(64),
  });
  expect(input.onChanged).toHaveBeenCalledTimes(1);
});

test("manual refresh cancels and fences an older detail response while jobs remain independently admitted", async () => {
  const obsolete = Promise.withResolvers<Response>();
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.includes("/history/attempts/")) {
        requests.push(request);
        return requests.length === 1 ? obsolete.promise : Response.json(detail(6, true));
      }
      return Response.json(path.endsWith("/jobs") ? jobs(6, true) : archivePage());
    }),
  );
  render(<ArchiveBrowser {...props()} />);
  await userEvent.click(await screen.findByRole("button", { name: /#101/ }));
  await screen.findByText("Ruff");
  await userEvent.click(screen.getByRole("button", { name: "Refresh evidence" }));
  await screen.findByText("Numbered steps expired; retained statistics remain available.");
  expect(requests[0]?.signal.aborted).toBe(true);
  await act(async () => obsolete.resolve(Response.json(detail())));
  expect(screen.queryByText(/Step 1: success/)).not.toBeInTheDocument();
});

test("refresh retires an old cursor before requesting the new first page", async () => {
  const cursors: (string | null)[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      cursors.push(url.searchParams.get("cursor"));
      return Response.json({ ...archivePage(), nextCursor: "opaque-page-two" });
    }),
  );
  render(<ArchiveBrowser {...props()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Next page" }));
  await screen.findByText("Page 2");
  await userEvent.click(screen.getByRole("button", { name: "Refresh evidence" }));
  await screen.findByText("Page 1");
  expect(cursors).toEqual([null, "opaque-page-two", null]);
});

test("manual refresh does not weaken analytics' frozen configuration/data revision fence", async () => {
  let revision = 5;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.includes("/history/attempts/")) return Response.json(detail(revision));
      return Response.json(path.endsWith("/jobs") ? jobs(revision) : archivePage());
    }),
  );
  render(
    <ArchiveBrowser
      {...props()}
      inspection={{ query: archiveQuery(), configurationRevision: 4, dataRevision: 5 }}
    />,
  );
  await userEvent.click(await screen.findByRole("button", { name: /#101/ }));
  await screen.findByText(/Step 1: success/);
  expect(screen.queryByRole("button", { name: "Manage detail retention" })).not.toBeInTheDocument();
  revision = 6;
  await userEvent.click(screen.getByRole("button", { name: "Refresh evidence" }));
  await waitFor(() =>
    expect(screen.getAllByText("Evidence temporarily unavailable")).toHaveLength(2),
  );
  expect(screen.queryByText(/Step 1: success/)).not.toBeInTheDocument();
});
