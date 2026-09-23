import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { HistoryStatus } from "../src/api/ciEconomics/historyStatusSchema";
import { HistoryProgress } from "../src/features/ciEconomics/HistoryProgress";
import { historyStatus } from "./historyFixture";

afterEach(cleanup);

const TIMESTAMPS = {
  requestedFrom: "2026-09-01T00:00:00Z",
  requestedThrough: "2026-09-05T00:00:00Z",
  windowFrom: "2026-09-03T00:00:00Z",
  windowThrough: "2026-09-04T00:00:00Z",
  discoveryCompletedThrough: "2026-09-06T08:09:10Z",
  nextAttempt: "2026-09-14T15:16:17Z",
  leaseExpiry: "2026-09-15T16:17:18Z",
  observedAt: "2026-09-13T22:11:12Z",
} as const;

function progressStatus(): HistoryStatus {
  const base = historyStatus();
  if (base.snapshot === null || base.scan === null || base.discovery === null)
    throw new Error("history progress fixture must contain collection state");
  return {
    ...base,
    snapshot: { ...base.snapshot, configurationRevision: 902 },
    scan: {
      ...base.scan,
      revision: 37,
      createdFrom: TIMESTAMPS.requestedFrom,
      createdThrough: TIMESTAMPS.requestedThrough,
      windowFrom: TIMESTAMPS.windowFrom,
      windowThrough: TIMESTAMPS.windowThrough,
      nextAttemptAt: TIMESTAMPS.nextAttempt,
      leaseExpiresAt: TIMESTAMPS.leaseExpiry,
    },
    discovery: {
      ...base.discovery,
      recoveryFloor: "2026-09-06T01:02:03Z",
      completedThrough: TIMESTAMPS.discoveryCompletedThrough,
      progress: {
        ...base.discovery.progress,
        revision: 53,
        createdFrom: "2026-09-06T01:02:03Z",
        createdThrough: "2026-09-07T04:05:06Z",
        windowFrom: "2026-09-06T02:03:04Z",
        windowThrough: "2026-09-06T12:00:00Z",
        cycleStartedAt: "2026-09-13T19:00:00Z",
      },
    },
    observedAt: TIMESTAMPS.observedAt,
  };
}

function assertTimes(label: string, expected: readonly (readonly [string, string])[]) {
  const row = screen.getByText(label, { exact: true }).closest("div");
  expect(row).not.toBeNull();
  const value = row?.querySelector("dd");
  expect(value).not.toBeNull();
  const times = [...(value?.querySelectorAll("time") ?? [])];
  expect(times).toHaveLength(expected.length);
  expected.forEach(([value, formatted], index) => {
    const time = times[index];
    if (time === undefined) throw new Error(`missing time element for ${label}`);
    expect(time.getAttribute("datetime")).toBe(value);
    expect(time.getAttribute("title")).toBe(value);
    expect(time.textContent).toBe(formatted);
  });
}

test("binds each progress label to its own timestamp and keeps revisions independent", () => {
  const status = progressStatus();
  expect(status.scan?.revision).not.toBe(status.snapshot?.configurationRevision);
  expect(status.discovery?.progress.revision).not.toBe(status.snapshot?.configurationRevision);
  render(<HistoryProgress status={status} />);
  fireEvent.click(screen.getByText("Scan and storage details", { exact: true }));

  assertTimes("Requested range (UTC)", [
    ["2026-09-01T00:00:00Z", "01 Sep 2026, 00:00:00 UTC"],
    ["2026-09-05T00:00:00Z", "05 Sep 2026, 00:00:00 UTC"],
  ]);
  assertTimes("Current window (UTC)", [
    ["2026-09-03T00:00:00Z", "03 Sep 2026, 00:00:00 UTC"],
    ["2026-09-04T00:00:00Z", "04 Sep 2026, 00:00:00 UTC"],
  ]);
  assertTimes("Recent recovery through", [["2026-09-06T08:09:10Z", "06 Sep 2026, 08:09:10 UTC"]]);
  assertTimes("Next eligible attempt", [["2026-09-14T15:16:17Z", "14 Sep 2026, 15:16:17 UTC"]]);
  assertTimes("Work allocation expires", [["2026-09-15T16:17:18Z", "15 Sep 2026, 16:17:18 UTC"]]);
  assertTimes("Status observed", [["2026-09-13T22:11:12Z", "13 Sep 2026, 22:11:12 UTC"]]);
});

test("does not render absent lease or discovery fields as present", () => {
  const withoutLease = progressStatus();
  if (withoutLease.scan === null) throw new Error("history progress scan is required");
  render(
    <HistoryProgress
      status={{ ...withoutLease, scan: { ...withoutLease.scan, leaseExpiresAt: null } }}
    />,
  );
  fireEvent.click(screen.getByText("Scan and storage details", { exact: true }));
  expect(screen.queryByText("Work allocation expires", { exact: true })).not.toBeInTheDocument();

  cleanup();
  const withoutDiscovery = progressStatus();
  render(<HistoryProgress status={{ ...withoutDiscovery, discovery: null }} />);
  fireEvent.click(screen.getByText("Scan and storage details", { exact: true }));
  expect(screen.queryByText("Recent recovery through", { exact: true })).not.toBeInTheDocument();
  expect(
    screen.queryByText("Recent discovery pages checked", { exact: true }),
  ).not.toBeInTheDocument();
});
