import { afterEach, expect, test, vi } from "vitest";
import { fetchHistoryAnalytics } from "../src/api/ciEconomics/analyticsClient";
import {
  analyticsReportSchema,
  analyticsResponseSchema,
} from "../src/api/ciEconomics/analyticsSchema";
import { economicsProxyRequestIsAdmitted } from "../src/api/development/economicsProxyPolicy";
import { analyticsQuery, analyticsReport } from "./analyticsFixture";

afterEach(() => vi.unstubAllGlobals());
test("analytics binds scope, filters and units through the development proxy", async () => {
  const report = analyticsReport();
  const fetch = vi.fn(async (_request: Request) =>
    Response.json({ outcome: "available", report, unavailable: null }),
  );
  vi.stubGlobal("fetch", fetch);
  expect((await fetchHistoryAnalytics(report.query)).kind).toBe("ready");
  const request = fetch.mock.calls[0]?.[0];
  expect(request?.cache).toBe("no-store");
  expect(economicsProxyRequestIsAdmitted("GET", new URL(request?.url ?? ""))).toBe(true);
  expect(new URL(request?.url ?? "").searchParams.get("createdUntil")).toBe(
    report.query.createdUntil,
  );
});
test.each([
  "installationId",
  "repositoryId",
  "generation",
  "workflowId",
  "jobName",
  "purpose",
  "horizonDays",
  "minimumDailySamples",
] as const)("foreign %s response is not displayed", async (field) => {
  const original = analyticsQuery();
  const query = {
    ...original,
    [field]: field === "jobName" ? "foreign" : field === "purpose" ? "lint" : 17,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ outcome: "available", report: analyticsReport(query), unavailable: null }),
    ),
  );
  expect((await fetchHistoryAnalytics(original)).kind).toBe("invalid-response");
});
test.each([
  { runs: 10 },
  { attempts: 1 },
  { mappingDigest: "a".repeat(64) },
  { buckets: [] },
  { providerCoverage: "complete" },
  { unit: "cpu-minutes" },
  { unexpected: true },
  { observedAt: "2000-01-01T00:00:00Z" },
])("analytics rejects contradictory report %#", (changes) => {
  expect(analyticsReportSchema.safeParse({ ...analyticsReport(), ...changes }).success).toBe(false);
});
test.each([
  { observedRunnerMs: 0 },
  { coverage: "unknown" },
  { attempts: 2 },
  { selected: { ...analyticsReport().buckets[0]?.selected, durationSamples: 0 } },
])("daily operands remain individually causal %#", (changes) => {
  const report = analyticsReport();
  expect(
    analyticsReportSchema.safeParse({
      ...report,
      buckets: [{ ...report.buckets[0], ...changes }, ...report.buckets.slice(1)],
    }).success,
  ).toBe(false);
});
test("unknown is not zero and an unsupported forecast has no estimate", () => {
  const report = analyticsReport();
  expect(analyticsReportSchema.safeParse(report).success).toBe(true);
  expect(report.buckets[2]?.observedRunnerMs).toBeNull();
  expect(
    analyticsReportSchema.safeParse({
      ...report,
      forecast: { ...report.forecast, predictedRunnerMs: 0 },
    }).success,
  ).toBe(false);
  expect(
    analyticsResponseSchema.safeParse({
      outcome: "available",
      report,
      unavailable: { reason: "dataset_unavailable" },
    }).success,
  ).toBe(false);
});
test.each([
  "dataset_unavailable",
  "generation_changed",
  "query_budget_exceeded",
  "snapshot_changed",
  "purpose_mapping_unavailable",
  "future_window",
])("bounded unavailable reason %s remains distinct", async (reason) => {
  const response = { outcome: "unavailable", report: null, unavailable: { reason } };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(response)),
  );
  expect(await fetchHistoryAnalytics(analyticsQuery())).toEqual({ kind: "ready", value: response });
});
