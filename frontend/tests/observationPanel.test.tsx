import { webcrypto } from "node:crypto";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ObservationGaps } from "../src/features/ciEconomics/ObservationGaps";
import { ObservationPanel } from "../src/features/ciEconomics/ObservationPanel";
import { ObservationProgress } from "../src/features/ciEconomics/ObservationProgress";
import { controlPlaneSessionFixture } from "./fixture";
import {
  OBSERVATION_CONFIG,
  OBSERVATION_SCOPE,
  OBSERVED,
  observationGap,
  observationGaps,
  observationSnapshot,
  observationStatus,
} from "./observationFixture";

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());
const session = controlPlaneSessionFixture({ roles: ["audit", "configure", "read"] });

test("observation counts use the shared explicit locale, not ambient number formatting", () => {
  const ambient = vi.spyOn(Number.prototype, "toLocaleString").mockReturnValue("ambient");
  try {
    const status = observationStatus();
    render(
      <ObservationProgress
        status={{
          ...status,
          occupiedSourceSlots: 1234,
          maximumSourceSlots: 10000,
          scans: status.scans.map((scan) => ({
            ...scan,
            pagesSeen: 2345,
            sourcesRegistered: 3456,
          })),
        }}
      />,
    );
    expect(screen.getByText("1,234 / 10,000")).toBeVisible();
    expect(screen.getAllByText("2,345").length).toBe(status.scans.length);
    expect(screen.getAllByText("3,456").length).toBe(status.scans.length);
    expect(ambient).not.toHaveBeenCalled();
  } finally {
    ambient.mockRestore();
  }
});

test("an invalid continuation can restart at the first page without reloading the application", async () => {
  const fetch = vi.fn(async (request: Request) =>
    request.url.includes("afterCursor=")
      ? Response.json({ ok: false, error: "invalid_request" }, { status: 400 })
      : Response.json({ ...observationGaps(), nextCursor: `1.1.1.${observationGap().gapId}` }),
  );
  vi.stubGlobal("fetch", fetch);
  render(<ObservationGaps scope={OBSERVATION_SCOPE} />);
  await screen.findByText("provider truncated");
  await userEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByRole("alert");
  await userEvent.click(screen.getByRole("button", { name: "First page" }));
  await screen.findByText("provider truncated");
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(screen.getByRole("button", { name: "First page" })).toBeDisabled();
});

test.each([
  [true, "2026-09-10T10:00:00.000002Z", true, "Scan claimed"],
  [true, OBSERVED, false, "Awaiting claim recovery"],
  [true, "2026-09-10T10:00:00Z", false, "Awaiting claim recovery"],
  [false, "2026-09-10T10:01:00Z", false, "Paused"],
] as const)(
  "claim motion is bound to enabled=%s and expiry=%s",
  (enabled, expiry, moving, label) => {
    const status = observationStatus(observationSnapshot({ ...OBSERVATION_CONFIG, enabled }));
    render(
      <ObservationProgress
        status={{
          ...status,
          scans: status.scans.map((scan) => ({ ...scan, leaseExpiresAt: expiry })),
        }}
      />,
    );
    const state = within(screen.getByRole("region", { name: "Recent scan" })).getByText(label, {
      selector: ".observation-lane-state",
    });
    expect(state.classList.contains("observation-lane-state--claimed")).toBe(moving);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  },
);

test("refresh failure keeps labelled previous evidence and draft; gaps are fetched only when opened", async () => {
  let fail = false;
  const fetch = vi.fn(async (request: Request) => {
    if (request.url.includes("/gaps")) return Response.json(observationGaps());
    return fail
      ? Response.json({ ok: false, error: "unavailable" }, { status: 503 })
      : Response.json(
          observationStatus(observationSnapshot({ ...OBSERVATION_CONFIG, enabled: false })),
        );
  });
  vi.stubGlobal("fetch", fetch);
  render(<ObservationPanel scope={OBSERVATION_SCOPE} session={session} />);
  await screen.findByRole("checkbox", { name: "Observation enabled" });
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Initial history" }), "3");
  expect(fetch).toHaveBeenCalledTimes(1);
  fail = true;
  await userEvent.click(screen.getByRole("button", { name: "Refresh evidence" }));
  await screen.findByText(/Showing the last validated status/);
  expect(screen.getByText("12 / 10,000")).toBeVisible();
  expect(screen.getByRole("combobox", { name: "Initial history" })).toHaveValue("3");
  expect(screen.getByRole("button", { name: "Save observation" })).toBeDisabled();
  fail = false;
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Save observation" })).toBeEnabled(),
  );
  expect(screen.getByRole("combobox", { name: "Initial history" })).toHaveValue("3");
  await userEvent.click(screen.getByText("Coverage gaps", { exact: true }));
  expect(await screen.findByText("provider truncated", { exact: true })).toBeVisible();
  expect(fetch.mock.calls.filter(([request]) => request.url.includes("/gaps"))).toHaveLength(1);
});
test("scan claims, recorded pages and historical failures are not a fabricated completion percentage", () => {
  const window = { createdFrom: "2026-09-10T09:00:00Z", createdThrough: "2026-09-10T10:00:00Z" };
  const status = observationStatus();
  render(
    <ObservationProgress
      status={{
        ...status,
        detailTruncatedUntil: "2026-12-09T09:00:00Z",
        scans: status.scans.map((scan) => ({
          ...scan,
          interval: window,
          window,
          cycleStartedAt: OBSERVED,
          pageNumber: 2,
          leaseExpiresAt: "2026-09-10T10:01:00Z",
          pagesSeen: 1,
          sourcesRegistered: 0,
          lastPageAt: OBSERVED,
          lastOutcome: "capacity_reached",
        })),
      }}
    />,
  );
  const recent = within(screen.getByRole("region", { name: "Recent scan" }));
  expect(recent.getByText("Scan claimed")).toBeVisible();
  expect(recent.getByText("capacity reached")).toBeVisible();
  expect(screen.getByText(/Some gap details were evicted/)).toBeVisible();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.queryByText(/100%/)).not.toBeInTheDocument();
});
