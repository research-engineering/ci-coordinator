import { expect, test } from "vitest";
import {
  historyCommandSchema,
  historyDatasetSchema,
  historyMutationSchema,
  sameDetailRetention,
  sameHistoryConfiguration,
} from "../src/api/ciEconomics/historySchema";
import { historyStatusSchema } from "../src/api/ciEconomics/historyStatusSchema";
import {
  historyCommand,
  historyConfiguration,
  historyDataset,
  historyMutation,
  historyStatus,
} from "./historyFixture";

test("independent public fixtures satisfy each schema and retain exact variants", () => {
  expect(historyCommandSchema.parse(historyCommand())).toEqual(historyCommand());
  expect(historyDatasetSchema.parse(historyDataset())).toEqual(historyDataset());
  expect(historyMutationSchema.parse(historyMutation())).toEqual(historyMutation());
  expect(historyStatusSchema.parse(historyStatus())).toEqual(historyStatus());
  expect(historyStatusSchema.parse(historyStatus(null))).toEqual(historyStatus(null));
});
test.each(Object.keys(historyCommand()))("requires command operand %s", (key) => {
  const raw = Object.fromEntries(
    Object.entries(historyCommand()).filter(([field]) => field !== key),
  );
  expect(historyCommandSchema.safeParse(raw).success).toBe(false);
});
test("optional expansion is distinct from rescan and rejects invalid UTC boundaries", () => {
  const existing = { ...historyCommand(), expectedRevision: 1, initialCreatedFrom: null };
  expect(historyCommandSchema.parse(existing)).toEqual(existing);
  expect(Object.hasOwn(historyCommandSchema.parse(existing), "expandCreatedFrom")).toBe(false);
  expect(
    Object.hasOwn(
      historyCommandSchema.parse({ ...existing, expandCreatedFrom: undefined }),
      "expandCreatedFrom",
    ),
  ).toBe(false);
  expect(historyCommandSchema.parse({ ...existing, expandCreatedFrom: null })).toHaveProperty(
    "expandCreatedFrom",
    null,
  );
  expect(
    historyCommandSchema.safeParse({ ...existing, expandCreatedFrom: "2019-12-01T00:00:00Z" })
      .success,
  ).toBe(true);
  for (const candidate of [
    { ...historyCommand(), expandCreatedFrom: "2019-12-01T00:00:00Z" },
    { ...existing, expandCreatedFrom: "2019-12-01T00:00:00Z", rescan: true },
    { ...existing, expandCreatedFrom: "2020-02-31T00:00:00Z" },
    { ...existing, expandCreatedFrom: "2019-12-01T00:00:00.000001Z" },
  ]) {
    expect(historyCommandSchema.safeParse(candidate).success).toBe(false);
  }
});
test.each([
  { installationId: true },
  { repositoryId: "1" },
  { expectedRevision: 1.5 },
  { actor: "forged" },
  { operationId: "" },
  { initialCreatedFrom: null },
  { initialCreatedFrom: "2020-02-30T00:00:00Z" },
  { initialCreatedFrom: "2020-01-01T00:00:00.000001Z" },
  { rescan: true },
  { configuration: { ...historyConfiguration(), workflowIds: [] } },
  { configuration: { ...historyConfiguration(), workflowIds: [1, 1] } },
  { configuration: { ...historyConfiguration(), detailRetention: { mode: "unknown" } } },
])("rejects malformed command %j", (delta) => {
  expect(historyCommandSchema.safeParse({ ...historyCommand(), ...delta }).success).toBe(false);
});
test.each([
  null,
  { mode: "disabled" },
  { mode: "forever" },
  { mode: "days", days: 36_500, anchor: "first_successful_detail_import" },
] as const)("preserves applied policy %j and exact revision source", (detailRetention) => {
  const value = historyStatus(historyDataset({ ...historyConfiguration(), detailRetention }, 4));
  expect(historyStatusSchema.safeParse(value).success).toBe(true);
  expect(
    historyStatusSchema.safeParse({
      ...value,
      effectiveDetailRetention: { ...value.effectiveDetailRetention, revision: 9 },
    }).success,
  ).toBe(false);
  expect(
    historyStatusSchema.safeParse({
      ...value,
      effectiveDetailRetention: {
        ...value.effectiveDetailRetention,
        policy: { mode: "days", days: 2, anchor: "first_successful_detail_import" },
      },
    }).success,
  ).toBe(false);
});
test.each([
  { snapshot: null },
  { scan: null },
  { discovery: null },
  { effectiveDetailRetention: null },
  { pendingRechecks: 257 },
  { defaults: { ...historyStatus().defaults, updatedAt: "2027-01-01T00:00:00Z" } },
  { observedAt: "2020-01-01T00:00:00Z" },
  { repositoryId: 2 },
  { scan: { ...historyStatus().scan, windowFrom: "2019-01-01T00:00:00Z" } },
  { scan: { ...historyStatus().scan, windowThrough: "2020-01-09T00:00:00Z" } },
  { scan: { ...historyStatus().scan, pageNumber: 11 } },
  { scan: { ...historyStatus().scan, token: "never public" } },
  { scan: { ...historyStatus().scan, lastOutcome: "invented" } },
])("rejects inconsistent status %j", (delta) => {
  expect(historyStatusSchema.safeParse({ ...historyStatus(), ...delta }).success).toBe(false);
});
test.each([
  { recoveryFloor: "2027-01-01T00:00:00Z" },
  { completedThrough: "2019-01-01T00:00:00Z" },
  { completedThrough: "2027-01-01T00:00:00Z" },
  { pendingRuns: -1 },
  { pendingRuns: 101 },
  { pendingRuns: 1.5 },
  { token: "not public" },
])("rejects contradictory discovery operand %j", (delta) => {
  const value = historyStatus();
  expect(
    historyStatusSchema.safeParse({ ...value, discovery: { ...value.discovery, ...delta } })
      .success,
  ).toBe(false);
});
test("completed recent frontier requires its exact end and no pending handoff", () => {
  const value = historyStatus();
  if (!value.discovery) throw new Error("missing fixture discovery");
  const discovery = {
    ...value.discovery,
    progress: { ...value.discovery.progress, traversalComplete: true },
  };
  expect(historyStatusSchema.safeParse({ ...value, discovery }).success).toBe(false);
  const completed = { ...discovery, completedThrough: discovery.progress.createdThrough };
  expect(historyStatusSchema.safeParse({ ...value, discovery: completed }).success).toBe(true);
  expect(
    historyStatusSchema.safeParse({ ...value, discovery: { ...completed, pendingRuns: 1 } })
      .success,
  ).toBe(false);
  expect(
    historyStatusSchema.safeParse({
      ...value,
      discovery: { ...completed, progress: { ...completed.progress, attemptsSeen: 1 } },
    }).success,
  ).toBe(false);
});
test.each([
  "committed",
  "replayed",
  "revision_conflict",
  "operation_conflict",
  "capacity_reached",
  "dataset_fenced",
  "pending_work",
  "invalid_population",
])("mutation %s has exactly the admitted snapshot relationship", (outcome) => {
  for (const snapshot of [null, historyDataset()]) {
    expect(
      historyMutationSchema.safeParse({ ...historyMutation(), outcome, snapshot }).success,
    ).toBe((outcome === "committed" || outcome === "replayed") === (snapshot !== null));
  }
});
test("configuration comparison is set-aware but sensitive to all policy and quota operands", () => {
  const left = { ...historyConfiguration(), workflowIds: [1, 2] };
  expect(sameHistoryConfiguration(left, { ...left, workflowIds: [2, 1] })).toBe(true);
  for (const right of [
    { ...left, enabled: false },
    { ...left, workflowIds: null },
    { ...left, workflowIds: [1] },
    { ...left, workflowIds: [1, 3] },
    { ...left, detailRetention: { mode: "forever" as const } },
    ...Object.entries(left.quota).map(([key, value]) => ({
      ...left,
      quota: { ...left.quota, [key]: value + 1 },
    })),
  ])
    expect(sameHistoryConfiguration(left, right)).toBe(false);
  expect(sameDetailRetention({ mode: "disabled" }, { mode: "forever" })).toBe(false);
  expect(historyDatasetSchema.safeParse({ ...historyDataset(), state: "paused" }).success).toBe(
    false,
  );
});

test.each([
  ["2020-01-01T00:00:00Z", "2020-01-01T23:59:59Z", true],
  ["2020-01-01T00:00:00Z", "2020-01-01T00:00:00+00:00", true],
  ["2020-01-02T00:00:00Z", "2020-01-01T23:59:59Z", false],
  ["2019-12-31T00:00:00Z", "2020-01-01T00:00:00Z", false],
  ["2020-01-01T00:00:00Z", "2020-01-01T01:00:00+02:00", false],
] as const)(
  "catalog source for bound %s requires the UTC day of %s: %s",
  (bound, createdAt, ok) => {
    const command = {
      ...historyCommand(),
      initialCreatedFrom: bound,
      populationBoundSource: { kind: "repository_created_at", repositoryCreatedAt: createdAt },
    };
    expect(historyCommandSchema.safeParse(command).success).toBe(ok);
  },
);
test("catalog source requires a bound and its closed shape", () => {
  const source = { kind: "repository_created_at", repositoryCreatedAt: "2020-01-01T12:00:00Z" };
  const plainUpdate = { ...historyCommand(), expectedRevision: 1, initialCreatedFrom: null };
  expect(
    historyCommandSchema.safeParse({ ...plainUpdate, populationBoundSource: source }).success,
  ).toBe(false);
  expect(
    historyCommandSchema.safeParse({
      ...plainUpdate,
      expandCreatedFrom: "2020-01-01T00:00:00Z",
      populationBoundSource: source,
    }).success,
  ).toBe(true);
  expect(
    historyCommandSchema.safeParse({
      ...historyCommand(),
      initialCreatedFrom: "2020-01-01T00:00:00Z",
      populationBoundSource: { ...source, kind: "explicit" },
    }).success,
  ).toBe(false);
  expect(historyCommandSchema.parse(historyCommand())).not.toHaveProperty("populationBoundSource");
  // The generated and backend contracts admit null as omission; the parsed command omits it.
  expect(
    historyCommandSchema.parse({ ...historyCommand(), populationBoundSource: null }),
  ).toStrictEqual(historyCommandSchema.parse(historyCommand()));
});
test.each([
  { source: "explicit", repositoryCreatedAt: null, recordedAt: "2026-09-12T10:00:00Z" },
  {
    source: "repository_created_at",
    repositoryCreatedAt: "2020-01-01T12:34:56Z",
    recordedAt: "2026-09-12T10:00:00Z",
  },
  { source: "unrecorded", repositoryCreatedAt: null, recordedAt: null },
] as const)("admits population bound provenance %j", (provenance) => {
  const value = historyStatus();
  expect(
    historyStatusSchema.safeParse({
      ...value,
      populationBound: { createdFrom: "2020-01-01T00:00:00Z", ...provenance },
    }).success,
  ).toBe(true);
});
test.each([
  { populationBound: null },
  { createdFrom: "2019-12-01T00:00:00Z" },
  { source: "unrecorded" },
  { source: "explicit", repositoryCreatedAt: "2020-01-01T12:34:56Z" },
  { source: "repository_created_at", repositoryCreatedAt: null },
  { source: "repository_created_at", repositoryCreatedAt: "2020-01-02T00:00:00Z" },
  { recordedAt: "2027-01-01T00:00:00Z" },
  { token: "not public" },
])("rejects contradictory population bound operand %j", (delta) => {
  const value = historyStatus();
  const populationBound =
    "populationBound" in delta ? delta.populationBound : { ...value.populationBound, ...delta };
  expect(historyStatusSchema.safeParse({ ...value, populationBound }).success).toBe(false);
});
test.each([
  ["2020-01-01T01:00:00Z", false],
  ["2020-01-01T11:59:59.999999Z", false],
  ["2020-01-01T12:00:00Z", true],
] as const)(
  "catalog provenance recorded at %s after creation 2020-01-01T12:00:00Z is admitted=%s",
  (recordedAt, ok) => {
    const value = historyStatus();
    const populationBound = {
      createdFrom: "2020-01-01T00:00:00Z",
      source: "repository_created_at",
      repositoryCreatedAt: "2020-01-01T12:00:00Z",
      recordedAt,
    } as const;
    expect(historyStatusSchema.safeParse({ ...value, populationBound }).success).toBe(ok);
  },
);
test("status v1 stays strict in both directions: a missing or unknown key fails closed", () => {
  // An already-open tab parses with the same strict-object rule, so it rejects the added
  // populationBound key and shows the status as unavailable instead of partial.
  const { populationBound: _populationBound, ...previous } = historyStatus();
  expect(historyStatusSchema.safeParse(previous).success).toBe(false);
  expect(historyStatusSchema.safeParse({ ...historyStatus(), unknownKey: 1 }).success).toBe(false);
});
