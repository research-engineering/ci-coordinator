import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { analyticsQuerySchema } from "../src/api/ciEconomics/analyticsSchema";
import { AnalyticsPanel } from "../src/features/ciEconomics/AnalyticsPanel";
import { analyticsReport } from "./analyticsFixture";
import { historyDataset, historyStatus } from "./historyFixture";
import { purposeSnapshot } from "./purposeSettingsFixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const scope = { installationId: 1, repositoryId: 1, limit: 10 };

async function refreshAnalytics() {
  const header = screen.getByRole("heading", { name: "Usage and performance" }).parentElement;
  if (!header) throw new Error("Analytics heading has no header");
  await userEvent.click(within(header).getByRole("button", { name: "Refresh evidence" }));
}

test("analytics shows measured units, gaps, forecast refusal and exact selected filters", async () => {
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      urls.push(url);
      if (url.pathname.endsWith("/history")) return Response.json(historyStatus());
      if (url.pathname.endsWith("/analytics/settings"))
        return Response.json({
          outcome: "available",
          snapshot: purposeSnapshot(),
          unavailable: null,
        });
      const query = analyticsQuerySchema.parse({
        installationId: 1,
        repositoryId: 1,
        generation: 1,
        createdFrom: url.searchParams.get("createdFrom"),
        createdUntil: url.searchParams.get("createdUntil"),
        workflowId: url.searchParams.has("workflowId")
          ? Number(url.searchParams.get("workflowId"))
          : null,
        jobName: url.searchParams.get("jobName"),
        horizonDays: Number(url.searchParams.get("horizonDays")),
      });
      return Response.json({
        outcome: "available",
        report: analyticsReport(query),
        unavailable: null,
      });
    }),
  );
  render(<AnalyticsPanel scope={scope} onHistory={vi.fn()} />);
  await screen.findByRole("heading", { name: "Retained runner usage" });
  expect(screen.getByText("Measured runner-minutes")).toBeInTheDocument();
  expect(screen.getByText(/Daily measurements are incomplete/)).toBeInTheDocument();
  expect(screen.getByText(/not realized financial savings/)).toBeInTheDocument();
  await userEvent.type(screen.getByRole("spinbutton", { name: "Workflow ID" }), "17");
  await userEvent.type(screen.getByRole("textbox", { name: "Exact job name" }), "Ruff");
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Forecast horizon" }), "14");
  await userEvent.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(urls.at(-1)?.searchParams.get("workflowId")).toBe("17"));
  expect(urls.at(-1)?.searchParams.get("jobName")).toBe("Ruff");
  expect(urls.at(-1)?.searchParams.get("horizonDays")).toBe("14");
  await screen.findByRole("heading", { name: "Retained runner usage" });
  const chart = screen.getByRole("figure", { name: "Retained runner usage" });
  await userEvent.click(within(chart).getByText("Data table", { selector: "summary" }));
  expect(within(chart).getAllByText("Not measured").length).toBeGreaterThan(0);
});

test("unconfigured history exposes an explicit configuration action, not a mutation", async () => {
  const onHistory = vi.fn();
  const fetch = vi.fn(async () => Response.json(historyStatus(null)));
  vi.stubGlobal("fetch", fetch);
  render(<AnalyticsPanel scope={scope} onHistory={onHistory} />);
  await userEvent.click(await screen.findByRole("button", { name: "Configure history" }));
  expect(onHistory).toHaveBeenCalledOnce();
  expect(fetch).toHaveBeenCalledOnce();
});

test("server refusal is visible without an empty success chart", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) =>
      Response.json(
        new URL(request.url).pathname.endsWith("/history")
          ? historyStatus()
          : {
              outcome: "unavailable",
              report: null,
              unavailable: { reason: "query_budget_exceeded" },
            },
      ),
    ),
  );
  render(<AnalyticsPanel scope={scope} onHistory={vi.fn()} />);
  await screen.findByText(/exceeds the query budget/);
  expect(screen.queryByRole("figure", { name: "Retained runner usage" })).not.toBeInTheDocument();
});

test.each(["snapshot_changed", "network-failure"])(
  "header refresh retries the report after %s without changing filters",
  async (failure) => {
    const queries: URL[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const url = new URL(request.url);
        if (url.pathname.endsWith("/history")) return Response.json(historyStatus());
        if (url.pathname.endsWith("/analytics/settings"))
          return Response.json({
            outcome: "available",
            snapshot: purposeSnapshot(),
            unavailable: null,
          });
        queries.push(url);
        if (queries.length === 2) {
          if (failure === "network-failure") throw new TypeError("Failed to fetch");
          return Response.json({
            outcome: "unavailable",
            report: null,
            unavailable: { reason: failure },
          });
        }
        const query = analyticsQuerySchema.parse({
          installationId: scope.installationId,
          repositoryId: scope.repositoryId,
          generation: 1,
          createdFrom: url.searchParams.get("createdFrom"),
          createdUntil: url.searchParams.get("createdUntil"),
          workflowId: url.searchParams.has("workflowId")
            ? Number(url.searchParams.get("workflowId"))
            : null,
          jobName: url.searchParams.get("jobName"),
          horizonDays: Number(url.searchParams.get("horizonDays")),
        });
        return Response.json({
          outcome: "available",
          report: analyticsReport(query),
          unavailable: null,
        });
      }),
    );
    render(<AnalyticsPanel scope={scope} onHistory={vi.fn()} />);
    await screen.findByRole("heading", { name: "Retained runner usage" });
    await userEvent.type(screen.getByRole("textbox", { name: "Exact job name" }), "Ruff");
    await userEvent.click(screen.getByRole("button", { name: /^Apply$/ }));
    await screen.findByText(
      failure === "snapshot_changed" ? /archive changed during this read/ : /Connection failed/,
    );
    expect(queries).toHaveLength(2);
    await refreshAnalytics();
    await screen.findByRole("heading", { name: "Retained runner usage" });
    expect(queries).toHaveLength(3);
    expect(queries[2]?.search).toBe(queries[1]?.search);
    expect(screen.getByRole("textbox", { name: "Exact job name" })).toHaveValue("Ruff");
  },
);

test("a refreshed generation rejects pending reports from the previous dataset", async () => {
  let generation = 1;
  const pending: { request: Request; response: Response; resolve: (value: Response) => void }[] =
    [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname.endsWith("/history"))
        return Response.json(historyStatus({ ...historyDataset(), generation }));
      if (url.pathname.endsWith("/analytics/settings"))
        return Response.json({
          outcome: "available",
          snapshot: {
            ...purposeSnapshot(),
            generation: Number(url.searchParams.get("generation")),
          },
          unavailable: null,
        });
      const query = analyticsQuerySchema.parse({
        installationId: 1,
        repositoryId: 1,
        generation: Number(url.searchParams.get("generation")),
        createdFrom: url.searchParams.get("createdFrom"),
        createdUntil: url.searchParams.get("createdUntil"),
        horizonDays: Number(url.searchParams.get("horizonDays")),
      });
      const report = analyticsReport(query);
      if (query.generation === 2 && report.buckets[0]) report.buckets[0].selected.failures = 1;
      const response = Response.json({ outcome: "available", report, unavailable: null });
      if (query.generation === 2) return response;
      const deferred = Promise.withResolvers<Response>();
      pending.push({ request, response, resolve: deferred.resolve });
      return deferred.promise;
    }),
  );
  render(<AnalyticsPanel scope={scope} onHistory={vi.fn()} />);
  await waitFor(() => expect(pending).toHaveLength(1));
  generation = 2;
  await refreshAnalytics();
  await screen.findByRole("heading", { name: "Retained runner usage" });
  expect(screen.getByText("Failed jobs", { selector: "dt" }).parentElement).toHaveTextContent(
    /Failed jobs\s*1/,
  );
  await act(async () => {
    for (const old of pending) {
      expect(old.request.signal.aborted).toBe(true);
      old.resolve(old.response);
    }
  });
  expect(screen.getByText("Failed jobs", { selector: "dt" }).parentElement).toHaveTextContent(
    /Failed jobs\s*1/,
  );
});
