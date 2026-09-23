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

test.each(["actor", "scope", "generation"] as const)(
  "%s replacement aborts old write and discards late receipt",
  async (replacement) => {
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
        Response.json({
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
  },
);
