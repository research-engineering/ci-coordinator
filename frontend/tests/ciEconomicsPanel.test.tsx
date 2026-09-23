import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { attemptSummaryCursor } from "../src/api/ciEconomics/schema";
import { shortIdentity } from "../src/domain/format";
import { CiEconomicsPanel } from "../src/features/workbench/CiEconomicsPanel";
import {
  ciEconomicsAttemptIdentityFixture,
  ciEconomicsAttemptJobsFixture,
  ciEconomicsAttemptPageFixture,
  ciEconomicsAttemptSummaryFixture,
} from "./ciEconomicsFixture";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows only server-projected economics and fetches jobs on demand", async () => {
  const requestedPaths: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      requestedPaths.push(path);
      return Response.json(
        path.endsWith("/jobs")
          ? ciEconomicsAttemptJobsFixture({
              attemptWall: exactDuration(9_999),
              queue: exactDuration(321),
              runnerOccupancy: exactDuration(7_654),
            })
          : ciEconomicsAttemptPageFixture(),
      );
    }),
  );
  render(
    <CiEconomicsPanel
      authorityRevision={1}
      scope={{ installationId: 1, limit: 10, repositoryId: 1 }}
    />,
  );

  expect(await screen.findByRole("button", { name: /View jobs for run 4201/ })).toBeVisible();
  expect(screen.queryByText("Backend tests")).not.toBeInTheDocument();
  expect(requestedPaths.some((path) => path.endsWith("/jobs"))).toBe(false);
  await userEvent.click(await screen.findByRole("button", { name: /View jobs for run 4201/ }));

  expect(await screen.findByText("321 ms")).toBeVisible();
  expect(screen.getByText("7,654 ms")).toBeVisible();
  expect(screen.getByText("9,999 ms")).toBeVisible();
  expect(screen.getByText("Backend tests")).toBeVisible();
  expect(requestedPaths.filter((path) => path.endsWith("/jobs"))).toHaveLength(1);
});

test("discards a late attempt response from an abandoned repository scope", async () => {
  const first = Promise.withResolvers<Response>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.includes("/repositories/1/1/")) return first.promise;
      return Response.json(
        ciEconomicsAttemptPageFixture({
          items: [
            ciEconomicsAttemptSummaryFixture({
              attempt: ciEconomicsAttemptIdentityFixture({
                repositoryId: 2,
                workflowRunId: 9_002,
              }),
              subjectId: "2".repeat(64),
            }),
          ],
        }),
      );
    }),
  );
  const view = render(
    <CiEconomicsPanel
      authorityRevision={1}
      scope={{ installationId: 1, limit: 10, repositoryId: 1 }}
    />,
  );

  view.rerender(
    <CiEconomicsPanel
      authorityRevision={1}
      scope={{ installationId: 1, limit: 10, repositoryId: 2 }}
    />,
  );
  expect(await screen.findByText("9002 / 2")).toBeVisible();

  await act(async () => {
    first.resolve(Response.json(ciEconomicsAttemptPageFixture()));
  });
  expect(screen.getByText("9002 / 2")).toBeVisible();
  expect(screen.queryByText("4201 / 2")).not.toBeInTheDocument();
});

test("discards late job evidence after selecting another retained attempt", async () => {
  const firstJobs = Promise.withResolvers<Response>();
  const firstSummary = ciEconomicsAttemptSummaryFixture();
  const secondSummary = ciEconomicsAttemptSummaryFixture({
    attempt: ciEconomicsAttemptIdentityFixture({ workflowRunId: 4_200 }),
    recordedAt: "2026-09-04T11:00:00.000000Z",
    snapshotDigest: "8".repeat(64),
    subjectId: "7".repeat(64),
  });
  let jobRequestCount = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (!new URL(request.url).pathname.endsWith("/jobs")) {
        return Response.json(
          ciEconomicsAttemptPageFixture({ items: [firstSummary, secondSummary] }),
        );
      }
      jobRequestCount += 1;
      if (jobRequestCount === 1) return firstJobs.promise;
      return Response.json(
        ciEconomicsAttemptJobsFixture({
          attempt: secondSummary.attempt,
          jobs: renamedFirstJob("Selected jobs"),
          recordedAt: secondSummary.recordedAt,
          snapshotDigest: secondSummary.snapshotDigest,
          subjectId: secondSummary.subjectId,
        }),
      );
    }),
  );
  render(
    <CiEconomicsPanel
      authorityRevision={1}
      scope={{ installationId: 1, limit: 10, repositoryId: 1 }}
    />,
  );

  await userEvent.click(await screen.findByRole("button", { name: /View jobs for run 4201/ }));
  await userEvent.click(screen.getByRole("button", { name: /View jobs for run 4200/ }));
  expect(await screen.findByText("Selected jobs")).toBeVisible();

  await act(async () => {
    firstJobs.resolve(
      Response.json(ciEconomicsAttemptJobsFixture({ jobs: renamedFirstJob("Abandoned jobs") })),
    );
  });
  expect(screen.getByText("Selected jobs")).toBeVisible();
  expect(screen.queryByText("Abandoned jobs")).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByLabelText(`Inspect identifier ${shortIdentity(secondSummary.subjectId)}`),
  );
  expect(screen.getByText(secondSummary.subjectId)).toBeVisible();
  expect(screen.queryByText(firstSummary.subjectId)).not.toBeInTheDocument();
});

test("does not request attempt jobs when authentication is rejected", async () => {
  const paths: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      paths.push(new URL(request.url).pathname);
      return Response.json({ error: "unauthenticated", ok: false }, { status: 401 });
    }),
  );
  render(
    <CiEconomicsPanel
      authorityRevision={1}
      scope={{ installationId: 1, limit: 10, repositoryId: 1 }}
    />,
  );

  expect(await screen.findByText("Economics authentication required")).toBeVisible();
  expect(paths.every((path) => !path.endsWith("/jobs"))).toBe(true);
  expect(screen.queryByRole("button", { name: /View jobs for run/ })).not.toBeInTheDocument();
});

test("advances one bounded attempt page only after an operator action", async () => {
  const newest = ciEconomicsAttemptSummaryFixture();
  const older = ciEconomicsAttemptSummaryFixture({
    attempt: ciEconomicsAttemptIdentityFixture({ workflowRunId: 4_000 }),
    recordedAt: "2026-09-04T11:00:00.000000Z",
    subjectId: "3".repeat(64),
  });
  const requestedCursors: Array<string | null> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const cursor = new URL(request.url).searchParams.get("afterCursor");
      requestedCursors.push(cursor);
      return Response.json(
        cursor === null
          ? ciEconomicsAttemptPageFixture({
              items: [newest],
              nextCursor: attemptSummaryCursor(newest),
            })
          : ciEconomicsAttemptPageFixture({ items: [older] }),
      );
    }),
  );
  render(
    <CiEconomicsPanel
      authorityRevision={1}
      scope={{ installationId: 1, limit: 10, repositoryId: 1 }}
    />,
  );

  expect(await screen.findByText("4201 / 2")).toBeVisible();
  expect(requestedCursors).toEqual([null]);
  await userEvent.click(screen.getByRole("button", { name: "Older" }));

  expect(await screen.findByText("4000 / 2")).toBeVisible();
  expect(screen.queryByText("4201 / 2")).not.toBeInTheDocument();
  expect(requestedCursors).toEqual([null, attemptSummaryCursor(newest)]);
});

function exactDuration(knownValueMs: number) {
  return {
    knownJobCount: 2,
    knownValueMs,
    quality: "exact" as const,
    reasonCode: null,
    totalJobCount: 2,
  };
}

function renamedFirstJob(name: string) {
  return ciEconomicsAttemptJobsFixture().jobs.map((job, index) =>
    index === 0 ? { ...job, name } : job,
  );
}
