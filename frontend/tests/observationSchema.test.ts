// @vitest-environment node
import { expect, test } from "vitest";
import {
  observationCommandSchema,
  observationInstant,
  observationMicroseconds,
  observationPayloadInstant,
  observationSnapshotSchema,
  sameObservationConfiguration,
} from "../src/api/ciEconomics/observationSchema";
import {
  observationGapsSchema,
  observationStatusSchema,
} from "../src/api/ciEconomics/observationStatusSchema";
import { observationWorkflowsSchema } from "../src/api/ciEconomics/observationWorkflowSchema";
import {
  OBSERVATION_CONFIG,
  OBSERVED,
  observationCommand,
  observationGap,
  observationGaps,
  observationScan,
  observationSnapshot,
  observationStatus,
  observationWorkflows,
} from "./observationFixture";

test.each(["2026-09-10T10:00:00.000001Z", "2026-09-10T10:00:00.000001+00:00"])(
  "preserves microseconds for %s",
  (instant) => {
    expect(observationInstant(instant)).toBe(OBSERVED);
    expect(observationMicroseconds(instant)).toBe(1_789_034_400_000_001n);
    expect(observationPayloadInstant(instant)).toBe("2026-09-10T10:00:00.000001+00:00");
  },
);
test.each([
  "2026-02-30T00:00:00Z",
  "2026-09-10T00:00:00+01:00",
  "2026-09-10T00:00:00.0000001Z",
  "not a timestamp",
  "0000-01-01T00:00:00Z",
])("rejects non-owner time %s", (instant) => {
  expect(observationInstant(instant)).toBeUndefined();
  expect(observationMicroseconds(instant)).toBeUndefined();
  expect(observationPayloadInstant(instant)).toBeUndefined();
});
test("canonical payload uses the Python no-fraction form when exact", () => {
  expect(observationPayloadInstant("2026-09-10T00:00:00.000000Z")).toBe(
    "2026-09-10T00:00:00+00:00",
  );
});
test.each([
  { expectedRevision: Number.MAX_SAFE_INTEGER },
  { operationId: " invalid" },
  { installationId: 0 },
  { repositoryId: Number.MAX_SAFE_INTEGER + 1 },
  { extra: true },
  { configuration: { ...OBSERVATION_CONFIG, backfillDays: 7 } },
  ...[
    { kind: "selected", workflowIds: [] },
    { kind: "selected", workflowIds: [1, 1] },
    { kind: "selected", workflowIds: null },
    { kind: "all", workflowIds: [1] },
    { kind: "all" },
    { kind: "selected", workflowIds: Array.from({ length: 33 }, (_, index) => index + 1) },
  ].map((selector) => ({ configuration: { ...OBSERVATION_CONFIG, selector } })),
])("rejects invalid configuration %j", (delta) => {
  expect(observationCommandSchema.safeParse(observationCommand()).success).toBe(true);
  expect(observationCommandSchema.safeParse({ ...observationCommand(), ...delta }).success).toBe(
    false,
  );
});
test("commands admit set order but snapshots require canonical order", () => {
  const configuration = {
    ...OBSERVATION_CONFIG,
    selector: { kind: "selected" as const, workflowIds: [2, 1] },
  };
  expect(observationCommandSchema.safeParse(observationCommand(configuration)).success).toBe(true);
  expect(observationSnapshotSchema.safeParse(observationSnapshot(configuration)).success).toBe(
    false,
  );
  const canonical = {
    ...configuration,
    selector: { ...configuration.selector, workflowIds: [1, 2] },
  };
  expect(observationSnapshotSchema.safeParse(observationSnapshot(canonical)).success).toBe(true);
  expect(sameObservationConfiguration(configuration, canonical)).toBe(true);
  for (const other of [
    OBSERVATION_CONFIG,
    { ...configuration, enabled: false },
    { ...configuration, backfillDays: 2 },
    { ...configuration, selector: { ...configuration.selector, workflowIds: [1, 3] } },
  ])
    expect(sameObservationConfiguration(configuration, other)).toBe(false);
});
test.each([
  { snapshot: null },
  { maximumSourceSlots: 20_000 },
  { occupiedSourceSlots: 10_001 },
  { scans: [observationScan()] },
  { scans: [observationScan(), observationScan()] },
  { snapshot: { ...observationSnapshot(), repositoryId: 2 } },
  ...[
    { pageNumber: 1 },
    { leaseExpiresAt: OBSERVED },
    { pagesSeen: 1 },
    { pagesSeen: 0.5 },
    { sourcesRegistered: 1 },
    { lastCompletedThrough: OBSERVED },
    { lastOutcome: "unknown" },
    { interval: { createdFrom: "2026-09-10T10:00:00Z", createdThrough: "2026-09-09T10:00:00Z" } },
  ].map((delta) => ({ scans: [{ ...observationScan(), ...delta }, observationScan("backfill")] })),
])("rejects contradictory status %j", (delta) => {
  expect(observationStatusSchema.safeParse(observationStatus()).success).toBe(true);
  expect(observationStatusSchema.safeParse({ ...observationStatus(), ...delta }).success).toBe(
    false,
  );
});
test("unconfigured status and fully bound in-progress scan are distinct valid states", () => {
  expect(observationStatusSchema.safeParse(observationStatus(null)).success).toBe(true);
  const window = { createdFrom: "2026-09-10T09:00:00Z", createdThrough: "2026-09-10T10:00:00Z" };
  const scan = {
    ...observationScan(),
    window,
    interval: window,
    cycleStartedAt: OBSERVED,
    pageNumber: 1,
    pagesSeen: 2,
    sourcesRegistered: 200,
    lastOutcome: "page_recorded",
    lastPageAt: OBSERVED,
    lastCompletedThrough: window.createdThrough,
  };
  expect(
    observationStatusSchema.safeParse({
      ...observationStatus(),
      scans: [scan, observationScan("backfill")],
    }).success,
  ).toBe(true);
  for (const delta of [
    { sourcesRegistered: 201 },
    { lastOutcome: null },
    { lastCompletedThrough: OBSERVED },
    { cycleStartedAt: window.createdFrom },
    { window: { ...window, createdThrough: "2026-09-10T11:00:00Z" } },
    { window: { ...window, createdFrom: "2026-09-10T09:00:00.1Z" } },
  ])
    expect(
      observationStatusSchema.safeParse({
        ...observationStatus(),
        scans: [{ ...scan, ...delta }, observationScan("backfill")],
      }).success,
    ).toBe(false);
});
test.each([
  ["2026-09-10T06:00:00Z", true],
  ["2026-09-10T06:00:01Z", false],
] as const)("active window ending %s preserves the six-hour bound", (createdThrough, admitted) => {
  const scan = {
    ...observationScan(),
    interval: { createdFrom: "2026-09-10T00:00:00Z", createdThrough: "2026-09-10T07:00:00Z" },
    window: { createdFrom: "2026-09-10T00:00:00Z", createdThrough },
    cycleStartedAt: OBSERVED,
    pageNumber: 1,
  };
  expect(
    observationStatusSchema.safeParse({
      ...observationStatus(),
      scans: [scan, observationScan("backfill")],
    }).success,
  ).toBe(admitted);
});
test("gap cursor can page historical gaps only in its bound scope", () => {
  const page = { ...observationGaps(), nextCursor: `1.1.2.${observationGap().gapId}` };
  expect(observationGapsSchema.safeParse(page).success).toBe(true);
  for (const cursor of [
    `2.1.2.${observationGap().gapId}`,
    `1.2.2.${observationGap().gapId}`,
    `1.1.0.${observationGap().gapId}`,
    `1.1.9007199254740992.${observationGap().gapId}`,
    `1.1.2.${"0".repeat(64)}`,
    `${page.nextCursor}\n`,
  ])
    expect(observationGapsSchema.safeParse({ ...page, nextCursor: cursor }).success).toBe(false);
});
test.each([
  { nextCursor: "0".repeat(64) },
  { observedAt: "2026-12-09T09:00:00.000001Z" },
  { items: [observationGap(), observationGap()] },
  { items: [{ ...observationGap(), expiresAt: "2026-12-10T09:00:00.000001Z" }] },
  ...[
    { repositoryId: 2 },
    { createdFrom: "2026-09-10T10:00:00Z" },
    { createdThrough: "2026-09-10T10:00:00Z" },
  ].map((delta) => ({
    items: [{ ...observationGap(), gap: { ...observationGap().gap, ...delta } }],
  })),
])("rejects contradictory gap provenance %j", (delta) => {
  expect(observationGapsSchema.safeParse(observationGaps()).success).toBe(true);
  expect(observationGapsSchema.safeParse({ ...observationGaps(), ...delta }).success).toBe(false);
});
test.each([
  { providerTotal: 3 },
  { termination: "next_page" },
  { pageNumber: 21 },
  { items: [observationWorkflows().items[0], observationWorkflows().items[0]] },
  ...["", "a\n", "\ud800", "x".repeat(257)].map((name) => ({
    items: [{ ...observationWorkflows().items[0], name }],
    providerTotal: 1,
  })),
])("rejects malformed provider catalogue %j", (delta) => {
  expect(observationWorkflowsSchema.safeParse(observationWorkflows()).success).toBe(true);
  expect(
    observationWorkflowsSchema.safeParse({ ...observationWorkflows(), ...delta }).success,
  ).toBe(false);
});
test("catalogue text uses code points; moving totals remain explicitly truncated", () => {
  const value = {
    ...observationWorkflows(),
    items: [
      {
        ...observationWorkflows().items[0],
        name: "\u{1F642}".repeat(256),
        path: "dynamic/provider/workflow",
        state: "future_provider_state",
      },
    ],
    providerTotal: 1,
  };
  expect(observationWorkflowsSchema.safeParse(value).success).toBe(true);
  expect(
    observationWorkflowsSchema.safeParse({
      ...value,
      pageNumber: 20,
      providerTotal: 0,
      termination: "truncated",
    }).success,
  ).toBe(true);
});
