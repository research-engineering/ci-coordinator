import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ArchiveBrowser } from "../src/features/ciEconomics/ArchiveBrowser";
import { archiveGapPage } from "./archiveFixture";
import { controlPlaneSessionFixture } from "./fixture";
import { historyStatus } from "./historyFixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function props() {
  const status = historyStatus();
  return {
    kind: "gaps" as const,
    scope: { installationId: 1, repositoryId: 2, limit: 10 },
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
async function select() {
  await userEvent.click(
    await screen.findByRole("checkbox", { name: "Select retryable gaps on this page" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Retry selected gaps" }));
}

test.each([
  [401, "unauthenticated"],
  [403, "forbidden"],
] as const)(
  "first current typed %s gap refusal is closable, not success",
  async (status, error) => {
    const bodies: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "GET") return Response.json(archiveGapPage());
        bodies.push(await request.text());
        return Response.json({ ok: false, error }, { status });
      }),
    );
    const input = props();
    render(<ArchiveBrowser {...input} />);
    await select();
    await userEvent.click(screen.getByRole("button", { name: "Queue archive retries" }));
    await screen.findByText("The archive retry request was denied. No new retry was admitted.");
    expect(screen.getByRole("button", { name: "Close retry selection" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Retry same request" })).not.toBeInTheDocument();
    expect(input.onChanged).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Close retry selection" }));
    expect(bodies).toHaveLength(1);
  },
);

test.each([
  [401, "unauthenticated"],
  [403, "forbidden"],
] as const)("typed %s after a lost gap reply cannot erase uncertainty", async (status, error) => {
  const bodies: string[] = [];
  let modeledEffects = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "GET") return Response.json(archiveGapPage());
      const body = await request.text();
      bodies.push(body);
      if (bodies.length === 1) {
        modeledEffects += 1;
        throw new TypeError("effect modeled, reply lost");
      }
      if (bodies.length === 2) return Response.json({ ok: false, error }, { status });
      const command = JSON.parse(body) as { operationId: string };
      return Response.json({
        outcome: "replayed",
        operationId: command.operationId,
        receipt: {
          request: command,
          intervals: [{ workflowRunId: 101, fromAttempt: 1, throughAttempt: 1 }],
        },
      });
    }),
  );
  const input = props();
  render(<ArchiveBrowser {...input} />);
  await select();
  await userEvent.click(screen.getByRole("button", { name: "Queue archive retries" }));
  await screen.findByText(/The result is unknown/);
  await userEvent.click(screen.getByRole("button", { name: "Retry same request" }));
  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(screen.getByRole("button", { name: "Close retry selection" })).toBeDisabled();
  expect(screen.getByText(/The result is unknown/)).toBeVisible();
  expect(input.onChanged).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Retry same request" }));
  await screen.findByText(/Archive retry admission confirmed/);
  expect(bodies).toEqual([bodies[0], bodies[0], bodies[0]]);
  expect(modeledEffects).toBe(1);
  expect(input.onChanged).toHaveBeenCalledTimes(1);
});

test.each([
  [403, "unavailable"],
  [503, "forbidden"],
  [503, "unavailable"],
  [404, "not_found"],
] as const)("first gap HTTP %s/%s remains uncertain", async (status, error) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) =>
      request.method === "GET"
        ? Response.json(archiveGapPage())
        : Response.json({ ok: false, error }, { status }),
    ),
  );
  const input = props();
  render(<ArchiveBrowser {...input} />);
  await select();
  await userEvent.click(screen.getByRole("button", { name: "Queue archive retries" }));
  await screen.findByText(/The result is unknown/);
  expect(screen.getByRole("button", { name: "Close retry selection" })).toBeDisabled();
  expect(input.onChanged).not.toHaveBeenCalled();
});

test.each([false, true])(
  "gap window instants preserve originals and null meaning: window=%s",
  async (hasWindow) => {
    const page = archiveGapPage();
    const gap = page.gaps[0];
    if (!gap) throw new Error("Expected one gap");
    if (hasWindow)
      page.gaps = [
        {
          ...gap,
          reason: "provider_truncated",
          workflowRunId: null,
          runAttempt: null,
          runCreatedAt: null,
          retrySupported: false,
          resolution: "not_evaluated",
          sourceWindow: {
            installationId: 1,
            repositoryId: 2,
            createdFrom: "2026-09-10T09:00:00Z",
            createdThrough: "2026-09-11T09:00:00Z",
            windowFrom: "2026-09-10T09:00:00Z",
            windowThrough: "2026-09-11T09:00:00Z",
            cycleStartedAt: "2026-09-12T10:00:00Z",
            pageNumber: 1,
          },
        },
      ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(page)),
    );
    const view = render(<ArchiveBrowser {...props()} />);
    await screen.findByRole("table", { name: "Recorded failures and current retained evidence" });
    if (hasWindow) {
      expect(
        Array.from(view.container.querySelectorAll("time"), (cell) => [
          cell.getAttribute("datetime"),
          cell.textContent,
        ]),
      ).toEqual([
        ["2026-09-10T09:00:00Z", "10 Sep 2026, 09:00:00 UTC"],
        ["2026-09-11T09:00:00Z", "11 Sep 2026, 09:00:00 UTC"],
      ]);
      expect(screen.getByRole("button", { name: "Retry selected gaps" })).toBeDisabled();
    } else {
      expect(screen.getByText("Not applicable")).toBeVisible();
      expect(view.container.querySelectorAll("time")).toHaveLength(0);
    }
  },
);

test("unknown retry survives hidden tabs and CSRF rotation; exact replay refreshes evidence", async () => {
  const commands: unknown[] = [];
  const csrf: (string | null)[] = [];
  let reads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "GET") {
        reads += 1;
        return Response.json(archiveGapPage());
      }
      const command = (await request.json()) as { operationId: string };
      commands.push(command);
      csrf.push(request.headers.get("x-csrf-token"));
      if (commands.length === 1) throw new TypeError("lost response");
      return Response.json({
        outcome: "replayed",
        operationId: command.operationId,
        receipt: {
          request: command,
          intervals: [{ workflowRunId: 101, fromAttempt: 1, throughAttempt: 1 }],
        },
      });
    }),
  );
  const input = props();
  const view = render(<ArchiveBrowser {...input} />);
  await select();
  expect(screen.getByText("#101: attempt 1")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Queue archive retries" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Close retry selection" })).toBeDisabled();
  const readCount = reads;
  view.rerender(<ArchiveBrowser {...input} active={false} />);
  await act(async () => {});
  expect(reads).toBe(readCount);
  view.rerender(
    <ArchiveBrowser {...input} session={{ ...input.session, csrfToken: "z".repeat(43) }} />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Retry same request" }));
  await screen.findByText(/Archive retry admission confirmed/);
  expect(commands).toHaveLength(2);
  expect(commands[0]).toEqual(commands[1]);
  expect(csrf[1]).toBe("z".repeat(43));
  expect(input.onChanged).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(reads).toBeGreaterThan(readCount));
});

test.each([
  ["actor", false],
  ["scope", false],
  ["generation", false],
  ["actor", true],
  ["scope", true],
  ["generation", true],
] as const)(
  "%s replacement aborts old write and discards late response; refusal=%s",
  async (replacement, refusal) => {
    const pending = Promise.withResolvers<Response>();
    let write: Request | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((request: Request) => {
        if (request.method === "GET") return Promise.resolve(Response.json(archiveGapPage()));
        write = request;
        return pending.promise;
      }),
    );
    const input = props();
    const view = render(<ArchiveBrowser {...input} />);
    await select();
    await userEvent.click(screen.getByRole("button", { name: "Queue archive retries" }));
    await waitFor(() => expect(write).toBeDefined());
    const command = (await write?.json()) as { operationId: string };
    const next = {
      ...input,
      ...(replacement === "scope" ? { scope: { ...input.scope, repositoryId: 9 } } : {}),
      ...(replacement === "actor"
        ? { session: { ...input.session, user: { ...input.session.user, actorId: "other" } } }
        : {}),
      ...(replacement === "generation" && input.status.snapshot
        ? { status: { ...input.status, snapshot: { ...input.status.snapshot, generation: 4 } } }
        : {}),
    };
    view.rerender(<ArchiveBrowser {...next} />);
    expect(write?.signal.aborted).toBe(true);
    await act(async () =>
      pending.resolve(
        refusal
          ? Response.json({ ok: false, error: "forbidden" }, { status: 403 })
          : Response.json({
              outcome: "committed",
              operationId: command.operationId,
              receipt: {
                request: command,
                intervals: [{ workflowRunId: 101, fromAttempt: 1, throughAttempt: 1 }],
              },
            }),
      ),
    );
    expect(input.onChanged).not.toHaveBeenCalled();
    expect(screen.queryByText(/Archive retry admission confirmed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No new retry was admitted/)).not.toBeInTheDocument();
  },
);
