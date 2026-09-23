import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { analyticsMappingIsCanonical } from "../src/api/ciEconomics/analyticsIdentity";
import { analyticsReportSchema } from "../src/api/ciEconomics/analyticsSchema";
import { archiveQuerySchema } from "../src/api/ciEconomics/archiveReadSchema";
import { AnalyticsSources } from "../src/features/ciEconomics/AnalyticsSources";
import { analyticsReport, mappedReport } from "./analyticsFixture";
import { archivePage } from "./archiveFixture";
import { historyStatus } from "./historyFixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function input() {
  const history = historyStatus();
  return {
    scope: { installationId: 1, repositoryId: 2, limit: 10 },
    history: {
      ...history,
      repositoryId: 2,
      snapshot: history.snapshot && { ...history.snapshot, repositoryId: 2, generation: 3 },
    },
    report: analyticsReport(),
    day: undefined as string | undefined,
    onBack: vi.fn(),
  };
}

function serveArchive(changes: { dataRevision?: number; configurationRevision?: number } = {}) {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const url = new URL(request.url);
      const names: Readonly<Record<string, string>> = {
        created_from: "createdFrom",
        created_through: "createdThrough",
        workflow_id: "workflowId",
        workflow_run_id: "workflowRunId",
        run_attempt: "runAttempt",
        job_name: "jobName",
      };
      const parameters = Object.fromEntries(
        Array.from(url.searchParams, ([key, value]) => [names[key] ?? key, value]),
      );
      const query = archiveQuerySchema.parse({
        installationId: 1,
        repositoryId: 2,
        ...parameters,
        ...Object.fromEntries(
          ["generation", "workflowId", "workflowRunId", "runAttempt", "limit"]
            .filter((key) => parameters[key] !== undefined)
            .map((key) => [key, Number(parameters[key])]),
        ),
        kind: url.pathname.endsWith("/jobs") ? "jobs" : "records",
      });
      return Response.json({ ...archivePage(query), ...changes });
    }),
  );
  return requests;
}

test.each([undefined, "2026-09-10T00:00:00.000Z"])(
  "analytics inspection binds filters, time, revisions and job drilldown: %s",
  async (day) => {
    const props = input();
    props.report.query.workflowId = 17;
    props.report.query.jobName = "Ruff";
    const requests = serveArchive();
    render(<AnalyticsSources {...props} day={day} />);
    await userEvent.click(await screen.findByRole("button", { name: /#101/ }));
    await screen.findByText("Ruff");
    const records = new URL(requests[0]?.url ?? "");
    expect(records.pathname).toContain("/repositories/1/2/");
    expect(Object.fromEntries(records.searchParams)).toMatchObject({
      generation: "3",
      workflow_id: "17",
      job_name: "Ruff",
      created_from: day ?? "2026-09-10T00:00:00Z",
      created_through:
        day === undefined ? "2026-09-12T23:59:59.999999Z" : "2026-09-10T23:59:59.999999Z",
    });
    expect(Object.fromEntries(new URL(requests[1]?.url ?? "").searchParams)).toMatchObject({
      generation: "3",
      workflow_run_id: "101",
      run_attempt: "1",
      job_name: "Ruff",
    });
    expect(requests.every((request) => request.method === "GET")).toBe(true);
    expect(
      screen.queryByRole("button", { name: "Manage detail retention" }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Back to analytics" }));
    expect(props.onBack).toHaveBeenCalledOnce();
  },
);

test.each(["dataRevision", "configurationRevision"] as const)(
  "a changed %s cannot be presented as the graph's population",
  async (revision) => {
    const props = input();
    serveArchive({ [revision]: props.report[revision] + 1 });
    render(<AnalyticsSources {...props} />);
    await screen.findByText(/archive changed since analytics refresh the analysis/i);
    expect(screen.queryByRole("button", { name: /#101/ })).not.toBeInTheDocument();
  },
);

test("category inspection discloses the broader containing-run population", async () => {
  const props = input();
  props.report = mappedReport();
  props.report.query.purpose = "lint";
  props.report.query.workflowId = 17;
  props.report.query.jobName = "Ruff";
  for (const bucket of props.report.buckets) bucket.selected.unknownPurposeJobs = 0;
  expect(analyticsReportSchema.safeParse(props.report).success).toBe(true);
  expect(await analyticsMappingIsCanonical(props.report)).toBe(true);
  serveArchive();
  render(<AnalyticsSources {...props} />);
  await screen.findByRole("button", { name: /#101/ });
  expect(screen.getByText(/Statistics category: lint/)).toHaveTextContent(
    /may also contain jobs outside this category/,
  );
});

test.each(["generation", "repository", "day"])(
  "an obsolete %s is rejected before provider-independent archive reads",
  (change) => {
    const props = input();
    const requests = serveArchive();
    if (change === "generation" && props.history.snapshot) props.history.snapshot.generation += 1;
    if (change === "repository") props.scope.repositoryId += 1;
    if (change === "day") props.day = "2000-01-01T00:00:00Z";
    render(<AnalyticsSources {...props} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/refresh/i);
    expect(requests).toHaveLength(0);
  },
);
