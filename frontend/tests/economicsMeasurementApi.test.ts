import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  type ReportComparison,
  reportBudgetSchema,
  reportComparisonSchema,
} from "../src/api/ciEconomics/comparisonSchema";
import {
  compareMeasurementReports,
  evaluateMeasurementBudget,
  fetchAttemptMeasurements,
  fetchMeasurementReport,
} from "../src/api/ciEconomics/measurementClient";
import {
  attemptMeasurementsSchema,
  reportMeasurementSchema,
  retainedReportSchema,
} from "../src/api/ciEconomics/measurementSchema";
import {
  economicsMeasurements,
  economicsReport,
  economicsReportPage,
  economicsSource,
  RECEIVED,
} from "./economicsConsoleFixture";

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => {
  vi.unstubAllGlobals();
});
const baseline = economicsReport();
const treatment = economicsReport(economicsSource(4202));
function comparison(): ReportComparison {
  return {
    schemaVersion: "ci-economics-report-comparison/v1",
    ok: true,
    baseline,
    treatment,
    pairDigest: "a".repeat(64),
    mismatches: [],
    coverageStatus: "not_verified",
    causalStatus: "not_established",
    differences: baseline.payload.measurements.map((item) => ({
      counter: item.counter,
      unit: "microsecond",
      scope: item.scope,
      reduction: 0,
      relativeReduction: { numerator: 0, denominator: item.value ?? 1 },
    })),
  };
}
function budget() {
  const measurement = baseline.payload.measurements[0];
  if (!measurement) throw new Error("fixture counter missing");
  return {
    schemaVersion: "ci-economics-report-budget/v1" as const,
    ok: true as const,
    report: baseline,
    measurement,
    maximumUs: 10,
    thresholdAuthority: "caller_supplied" as const,
    evaluationWindow: "exact_report" as const,
    outcome: "within_budget" as const,
  };
}
test("reads exact measurements, retained report, server comparison and report budget", async () => {
  const fetch = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    return Response.json(
      url.pathname.endsWith("/measurements")
        ? economicsMeasurements()
        : url.pathname.endsWith("/report-comparisons")
          ? comparison()
          : url.pathname.endsWith("/budget")
            ? budget()
            : baseline,
    );
  });
  vi.stubGlobal("fetch", fetch);
  expect((await fetchAttemptMeasurements(economicsSource())).kind).toBe("ready");
  const pointer = economicsReportPage().items[0];
  if (!pointer) throw new Error("fixture pointer missing");
  expect((await fetchMeasurementReport(economicsSource(), pointer)).kind).toBe("ready");
  expect((await compareMeasurementReports(baseline, treatment)).kind).toBe("ready");
  expect((await evaluateMeasurementBudget(baseline, "cpu_system", 10)).kind).toBe("ready");
  expect(fetch.mock.calls.every(([request]) => request.method === "GET")).toBe(true);
  expect(new URL(fetch.mock.calls[2]?.[0].url ?? "http://invalid").search).toBe(
    `?baselineReportId=${baseline.reportId}&treatmentReportId=${treatment.reportId}`,
  );
});
test("keeps reconciliation provenance when the same attempt has an independent registration", async () => {
  const value = {
    ...economicsMeasurements(),
    source: {
      sourceKind: "reconciliation",
      sourceId: "f".repeat(64),
      attempt: economicsSource().attempt,
      contractHash: "e".repeat(64),
      plannedRoute: "selected",
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(value)),
  );
  expect(await fetchAttemptMeasurements(economicsSource())).toMatchObject({
    kind: "ready",
    value: { source: { sourceKind: "reconciliation" } },
  });
});
test.each([
  { retainUntil: RECEIVED },
  { source: economicsSource(4202) },
  { attemptWall: { ...economicsMeasurements().attemptWall, totalJobCount: 2 } },
])("rejects measurement lifetime or identity substitution %j", async (delta) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...economicsMeasurements(), ...delta })),
  );
  expect((await fetchAttemptMeasurements(economicsSource())).kind).toBe("invalid-response");
});
test.each([
  { reportId: "0".repeat(64) },
  { reportDigest: "0".repeat(64) },
  { receivedAt: "2026-09-08T10:00:00Z" },
  { retainUntil: "2026-12-07T09:00:00Z" },
  { source: economicsSource(4202) },
])("rejects report substitution %j", async (delta) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...baseline, ...delta })),
  );
  const pointer = economicsReportPage().items[0];
  if (!pointer) throw new Error("fixture pointer missing");
  expect((await fetchMeasurementReport(economicsSource(), pointer)).kind).toBe("invalid-response");
});
test.each([
  {
    counter: "cpu_user",
    unit: "microsecond",
    scope: "reporter_interval",
    value: 1,
    unavailableReason: null,
  },
  {
    counter: "elapsed",
    unit: "microsecond",
    scope: "reporter_interval",
    value: null,
    unavailableReason: null,
  },
  {
    counter: "cpu_system",
    unit: "microsecond",
    scope: "waited_children",
    value: 0,
    unavailableReason: "counter_error",
  },
])("rejects counter contradiction %j", (value) => {
  expect(reportMeasurementSchema.safeParse(value).success).toBe(false);
});
test("unknown is not zero and report counter order is exact", () => {
  expect(
    reportMeasurementSchema.safeParse({
      counter: "cpu_user",
      unit: "microsecond",
      scope: "waited_children",
      value: null,
      unavailableReason: "unsupported_platform",
    }).success,
  ).toBe(true);
  expect(
    retainedReportSchema.safeParse({
      ...baseline,
      payload: { ...baseline.payload, measurements: [...baseline.payload.measurements].reverse() },
    }).success,
  ).toBe(false);
  expect(
    attemptMeasurementsSchema.safeParse({ ...economicsMeasurements(), recordedAt: "invalid" })
      .success,
  ).toBe(false);
});
test.each([
  { coverageStatus: "verified" },
  { causalStatus: "established" },
  { differences: [] },
  { mismatches: ["source_sha"] },
  { mismatches: ["source_sha", "source_sha"], differences: [] },
  { mismatches: ["producer", "source_sha"], differences: [] },
  { differences: comparison().differences.map((item) => ({ ...item, reduction: 5 })) },
  { differences: comparison().differences.map((item) => ({ ...item, relativeReduction: null })) },
])("rejects inconsistent comparison %j", (delta) => {
  expect(reportComparisonSchema.safeParse({ ...comparison(), ...delta }).success).toBe(false);
});
test("incomparable and insufficient evidence remain admitted negative outcomes", () => {
  expect(
    reportComparisonSchema.safeParse({ ...comparison(), mismatches: ["producer"], differences: [] })
      .success,
  ).toBe(true);
  expect(
    reportBudgetSchema.safeParse({ ...budget(), outcome: "insufficient_evidence" }).success,
  ).toBe(false);
});
test("binds budget counter, threshold and both selected comparison identities", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...comparison(), baseline: treatment, treatment: baseline })),
  );
  expect((await compareMeasurementReports(baseline, treatment)).kind).toBe("invalid-response");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(budget())),
  );
  expect((await evaluateMeasurementBudget(baseline, "cpu_system", 11)).kind).toBe(
    "invalid-response",
  );
  expect((await evaluateMeasurementBudget(baseline, "cpu_user", 10)).kind).toBe("invalid-response");
});
