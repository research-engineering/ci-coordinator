import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { analyticsReportSchema } from "../src/api/ciEconomics/analyticsSchema";
import { HistoricalMetrics } from "../src/features/ciEconomics/HistoricalMetrics";
import { analyticsReport } from "./analyticsFixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function report() {
  const source = analyticsReport();
  const first = source.buckets[0];
  if (!first) throw new Error("Missing fixture bucket");
  Object.assign(first, {
    coverage: "partial",
    runs: 2,
    attempts: 4,
    matchingAttempts: 2,
    completeAttempts: 2,
    partialAttempts: 1,
    unavailableAttempts: 1,
    observedRunnerMs: 420000,
    observedQueueMs: 25000,
  });
  Object.assign(first.selected, {
    jobs: 8,
    failures: 2,
    cancellations: 3,
    durationSamples: 6,
    queueSamples: 4,
    runnerMs: 420000,
    queueMs: 25000,
    missingDuration: 2,
    missingQueue: 4,
    unknownPurposeJobs: 8,
  });
  source.runs = 3;
  source.attempts = 5;
  source.cohort.unknownWorkflowAttempts = 5;
  return analyticsReportSchema.parse(source);
}

test.each([
  ["runner", "Retained runner usage", "7", "6 / 8"],
  ["duration", "Mean observed job duration", "70", "6 / 8"],
  ["queue", "Mean observed job queue time", "6.25", "4 / 8"],
  ["attempts", "Retained run attempts", "4", null],
  ["jobs", "Selected jobs", "8", null],
  ["failures", "Failed jobs", "2", null],
  ["cancelled", "Cancelled jobs", "3", null],
] as const)(
  "projects %s without fetching or changing the admitted population",
  async (metric, title, value, support) => {
    const source = report();
    const original = structuredClone(source);
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const onInspect = vi.fn();
    render(<HistoricalMetrics report={source} onInspect={onInspect} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Metric" }), metric);
    const chart = screen.getByRole("figure", { name: title });
    await userEvent.click(within(chart).getByText("Data table"));
    const row = within(chart).getByRole("row", { name: /^2026-09-10 / });
    expect(within(row).getByRole("cell", { name: value })).toBeVisible();
    if (support) expect(within(row).getByRole("cell", { name: support })).toBeVisible();
    if (metric === "attempts")
      expect(screen.getByText(/before job and category filters/)).toBeVisible();
    await userEvent.click(
      within(row).getByRole("button", { name: "Inspect source runs for 2026-09-10" }),
    );
    expect(onInspect).toHaveBeenCalledExactlyOnceWith("2026-09-10T00:00:00.000Z");
    expect(fetch).not.toHaveBeenCalled();
    expect(source).toEqual(original);
  },
);

test.each([
  ["duration", "durationSamples", "runnerMs", "missingDuration", "observedRunnerMs"],
  ["queue", "queueSamples", "queueMs", "missingQueue", "observedQueueMs"],
] as const)(
  "keeps missing %s samples distinct from measured zero",
  async (metric, samples, total, missing, observed) => {
    const source = report();
    const first = source.buckets[0];
    if (!first) throw new Error("Missing fixture bucket");
    first.selected[samples] = 0;
    first.selected[total] = 0;
    first.selected[missing] = first.selected.jobs;
    first[observed] = null;
    analyticsReportSchema.parse(source);
    const view = render(<HistoricalMetrics report={source} onInspect={vi.fn()} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Metric" }), metric);
    await userEvent.click(screen.getByText("Data table"));
    expect(
      within(screen.getByRole("row", { name: /^2026-09-10 / })).getByText("Not measured"),
    ).toBeVisible();
    first.selected[samples] = 1;
    first.selected[missing] -= 1;
    first[observed] = 0;
    analyticsReportSchema.parse(source);
    view.rerender(<HistoricalMetrics report={{ ...source }} onInspect={vi.fn()} />);
    const row = screen.getByRole("row", { name: /^2026-09-10 / });
    expect(within(row).getByRole("cell", { name: "0" })).toBeVisible();
    expect(within(row).getByRole("cell", { name: "1 / 8" })).toBeVisible();
  },
);
