import { afterEach, expect, test, vi } from "vitest";
import { fetchHistoryAnalytics } from "../src/api/ciEconomics/analyticsClient";
import {
  type AnalyticsReport,
  analyticsQuerySchema,
  analyticsReportSchema,
} from "../src/api/ciEconomics/analyticsSchema";
import { archiveQuerySchema } from "../src/api/ciEconomics/archiveReadSchema";
import { archiveJobSchema } from "../src/api/ciEconomics/archiveRecordSchema";
import { economicsProxyRequestIsAdmitted } from "../src/api/development/economicsProxyPolicy";
import { analyticsQuery, analyticsReport, forecastReport, mappedReport } from "./analyticsFixture";
import { archiveJob, archiveQuery } from "./archiveFixture";

afterEach(() => vi.unstubAllGlobals());

test.each([512, 513])("job-name boundaries count Unicode scalars: %i", (length) => {
  const jobName = "\u{1f680}".repeat(length);
  const expected = length === 512;
  expect(analyticsQuerySchema.safeParse({ ...analyticsQuery(), jobName }).success).toBe(expected);
  for (const kind of ["records", "jobs"] as const) {
    expect(archiveQuerySchema.safeParse({ ...archiveQuery(kind), jobName }).success).toBe(expected);
  }
  expect(archiveJobSchema.safeParse({ ...archiveJob(), name: jobName }).success).toBe(expected);
});

test.each([128, 129])("runner label boundaries count Unicode scalars: %i", (length) => {
  expect(
    archiveJobSchema.safeParse({ ...archiveJob(), labels: ["\u{1f680}".repeat(length)] }).success,
  ).toBe(length === 128);
});

test("a maximum Unicode job name survives encoded analytics transport", async () => {
  const query = { ...analyticsQuery(), jobName: "\u{1f680}".repeat(512) };
  const fetch = vi.fn(async (_request: Request) =>
    Response.json({ outcome: "available", report: analyticsReport(query), unavailable: null }),
  );
  vi.stubGlobal("fetch", fetch);
  expect((await fetchHistoryAnalytics(query)).kind).toBe("ready");
  const url = new URL(fetch.mock.calls[0]?.[0].url ?? "");
  expect(url.search.length).toBeGreaterThan(4096);
  expect(economicsProxyRequestIsAdmitted("GET", url)).toBe(true);
});

test.each([
  ["reverse holdouts", (report: AnalyticsReport) => report.forecast.folds.reverse()],
  [
    "zero width",
    (report: AnalyticsReport) => {
      const fold = report.forecast.folds[0];
      if (fold) fold.testUntil = fold.trainingUntil;
    },
  ],
  [
    "calibration interval",
    (report: AnalyticsReport) => {
      const fold = report.forecast.folds[0];
      if (fold) fold.lowerRunnerMs = 0;
    },
  ],
  [
    "evaluation interval",
    (report: AnalyticsReport) => {
      const fold = report.forecast.folds[4];
      if (fold) fold.lowerRunnerMs = fold.predictedRunnerMs + 1;
    },
  ],
  [
    "phase reordering",
    (report: AnalyticsReport) => {
      const first = report.forecast.folds[0],
        last = report.forecast.folds[7];
      if (first && last) {
        first.phase = "evaluation";
        first.lowerRunnerMs = first.upperRunnerMs = first.predictedRunnerMs;
        last.phase = "calibration";
        last.lowerRunnerMs = last.upperRunnerMs = null;
      }
    },
  ],
  [
    "diagnostic pairing",
    (report: AnalyticsReport) => {
      report.forecast.backtestMaeMs = null;
    },
  ],
  [
    "microsecond cutoff",
    (report: AnalyticsReport) => {
      report.forecast.cutoff = report.query.createdUntil.replace("00Z", "00.000001Z");
    },
  ],
  [
    "holdout beyond cutoff",
    (report: AnalyticsReport) => {
      const fold = report.forecast.folds[7];
      if (fold) fold.testUntil = "2026-09-09T00:00:00.000001Z";
    },
  ],
] as const)("forecast rejects isolated contradiction: %s", (_name, mutate) => {
  const report = forecastReport();
  expect(analyticsReportSchema.safeParse(report).success).toBe(true);
  mutate(report);
  expect(analyticsReportSchema.safeParse(report).success).toBe(false);
});

test("named category without a mapping cannot be admitted", () => {
  const source = analyticsReport();
  expect(analyticsReportSchema.safeParse(source).success).toBe(true);
  source.query.purpose = "lint";
  expect(analyticsReportSchema.safeParse(source).success).toBe(false);
});

test.each(["installationId", "repositoryId", "generation"] as const)(
  "nested mapping cannot cross %s",
  (key) => {
    const report = mappedReport();
    expect(analyticsReportSchema.safeParse(report).success).toBe(true);
    if (report.mapping) report.mapping[key] += 1;
    expect(analyticsReportSchema.safeParse(report).success).toBe(false);
  },
);

test.each(["job key", "purpose"])("mapping rejects duplicate %s", (duplicate) => {
  const report = mappedReport();
  const entry = report.mapping?.entries[0];
  if (!entry || !report.mapping) throw new Error("mapping fixture missing");
  if (duplicate === "job key") report.mapping.entries.push({ ...entry });
  else entry.purposes.push("lint");
  expect(analyticsReportSchema.safeParse(report).success).toBe(false);
});

test.each(["lint", "mixed"] as const)("%s requires an explicit mapping", (purpose) => {
  const report = analyticsReport({ ...analyticsQuery(), purpose });
  expect(analyticsReportSchema.safeParse(report).success).toBe(false);
});

test.each(["unchanged", "version", "provenance", "job", "digest"])(
  "mapping bytes bind the displayed digest: %s",
  async (change) => {
    const report = mappedReport();
    const fetch = vi.fn(async () =>
      Response.json({ outcome: "available", report, unavailable: null }),
    );
    vi.stubGlobal("fetch", fetch);
    expect((await fetchHistoryAnalytics(report.query)).kind).toBe("ready");
    if (report.mapping) {
      if (change === "version") report.mapping.version = "v2";
      if (change === "provenance") report.mapping.provenance = "another policy";
      if (change === "job" && report.mapping.entries[0])
        report.mapping.entries[0].jobName = "Other";
    }
    if (change === "digest") report.mappingDigest = "a".repeat(64);
    expect((await fetchHistoryAnalytics(report.query)).kind).toBe(
      change === "unchanged" ? "ready" : "invalid-response",
    );
  },
);
