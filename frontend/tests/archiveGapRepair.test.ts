import { afterEach, expect, test, vi } from "vitest";
import { retryArchiveGaps } from "../src/api/ciEconomics/archiveGapRepairClient";
import {
  gapRepairRequestSchema,
  gapRepairResultSchema,
  selectedRepairIntervals,
} from "../src/api/ciEconomics/archiveGapRepairSchema";
import { archiveReadSchema } from "../src/api/ciEconomics/archiveReadSchema";
import { economicsProxyRequestIsAdmitted } from "../src/api/development/economicsProxyPolicy";
import { archiveGap, archiveGapPage, archiveRecord } from "./archiveFixture";

const request = {
  installationId: 1,
  repositoryId: 2,
  generation: 3,
  expectedRevision: 4,
  gapIds: ["d".repeat(64)],
  operationId: "retry-operation",
};
const intervals = [{ workflowRunId: 101, fromAttempt: 1, throughAttempt: 1 }];
const receipt = { request, intervals };
afterEach(() => vi.unstubAllGlobals());

test.each([
  { sourceOffset: 0, recordedOffset: 0, observedOffset: 0, valid: true },
  { sourceOffset: 0, recordedOffset: 1, observedOffset: 2, valid: true },
  { sourceOffset: 1, recordedOffset: 0, observedOffset: 2, valid: false },
  { sourceOffset: 0, recordedOffset: 2, observedOffset: 1, valid: false },
])(
  "missing source/recording/observation ordering is causal %#",
  ({ sourceOffset, recordedOffset, observedOffset, valid }) => {
    const page = archiveGapPage();
    const instant = (offset: number) =>
      new Date(Date.parse(page.observedAt) + offset).toISOString();
    expect(
      archiveReadSchema.safeParse({
        ...page,
        observedAt: instant(observedOffset),
        gaps: [
          {
            ...archiveGap(),
            runCreatedAt: instant(sourceOffset),
            recordedAt: instant(recordedOffset),
          },
        ],
      }).success,
    ).toBe(valid);
  },
);

test.each(["committed", "replayed"])(
  "%s receipt binds exact selection and private transport",
  async (outcome) => {
    const value = { outcome, operationId: request.operationId, receipt };
    const fetch = vi.fn(async (_input: Request) => Response.json(value));
    vi.stubGlobal("fetch", fetch);
    expect(await retryArchiveGaps(request, intervals, "c".repeat(43))).toEqual({
      kind: "ready",
      value,
    });
    const sent = fetch.mock.calls[0]?.[0];
    expect(sent?.method).toBe("POST");
    expect(sent?.credentials).toBe("same-origin");
    expect(sent?.cache).toBe("no-store");
    expect(sent?.headers.get("x-csrf-token")).toBe("c".repeat(43));
    expect(await sent?.json()).toEqual(request);
    expect(economicsProxyRequestIsAdmitted("POST", new URL(sent?.url ?? ""))).toBe(true);
  },
);

test.each([
  { repositoryId: 9 },
  { generation: 4 },
  { expectedRevision: 5 },
  { gapIds: ["e".repeat(64)] },
  { operationId: "other" },
])("rejects substituted receipt request %#", async (change) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        outcome: "committed",
        operationId: request.operationId,
        receipt: { ...receipt, request: { ...request, ...change } },
      }),
    ),
  );
  expect((await retryArchiveGaps(request, intervals, "c".repeat(43))).kind).toBe(
    "invalid-response",
  );
});

test.each(
  [
    [{ workflowRunId: 102, fromAttempt: 1, throughAttempt: 1 }],
    [{ workflowRunId: 101, fromAttempt: 1, throughAttempt: 2 }],
    [{ workflowRunId: 101, fromAttempt: 2, throughAttempt: 2 }],
    [],
  ].map((replacement) => ({ replacement })),
)("rejects substituted receipt intervals %#", async ({ replacement }) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        outcome: "replayed",
        operationId: request.operationId,
        receipt: { ...receipt, intervals: replacement },
      }),
    ),
  );
  expect((await retryArchiveGaps(request, intervals, "c".repeat(43))).kind).toBe(
    "invalid-response",
  );
});

test.each(["capacity_reached", "revision_conflict", "unsupported_gap"])(
  "%s is an explicit refusal only on 409",
  async (outcome) => {
    const value = { outcome, operationId: request.operationId, receipt: null };
    for (const status of [200, 409]) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => Response.json(value, { status })),
      );
      expect((await retryArchiveGaps(request, intervals, "c".repeat(43))).kind).toBe(
        status === 409 ? "ready" : "invalid-response",
      );
    }
    expect(gapRepairResultSchema.safeParse({ ...value, receipt }).success).toBe(false);
  },
);

test.each([
  { gapIds: [] },
  { gapIds: ["d".repeat(64), "d".repeat(64)] },
  { gapIds: ["e".repeat(64), "d".repeat(64)] },
  { gapIds: [`${"d".repeat(64)}\n`] },
  { actor: "injected" },
  { operationId: "operation\n" },
  { generation: true },
  { repositoryId: 1.5 },
])("rejects malformed request before transport %#", async (change) => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const candidate = { ...request, ...change };
  expect(gapRepairRequestSchema.safeParse(candidate).success).toBe(false);
  expect(
    (await retryArchiveGaps(candidate as typeof request, intervals, "c".repeat(43))).kind,
  ).toBe("invalid-response");
  expect(fetch).not.toHaveBeenCalled();
});

test("interval preview is inclusive, bounded, and rejects unsupported or absent sources", () => {
  const page = archiveGapPage();
  page.gaps.push({ ...archiveGap(), gapId: "e".repeat(64), runAttempt: 4 });
  const ids = page.gaps.map((gap) => gap.gapId);
  expect(selectedRepairIntervals(page, ids)).toEqual([
    { workflowRunId: 101, fromAttempt: 1, throughAttempt: 4 },
  ]);
  expect(selectedRepairIntervals(page, [...ids, ids[0] ?? ""])).toBeNull();
  expect(selectedRepairIntervals(page, ["f".repeat(64)])).toBeNull();
  for (const runAttempt of [50, 51, Number.MAX_SAFE_INTEGER]) {
    const changed = {
      ...page,
      gaps: [page.gaps[0] ?? archiveGap(), { ...archiveGap(), gapId: ids[1] ?? "", runAttempt }],
    };
    expect(selectedRepairIntervals(changed, ids) === null).toBe(runAttempt > 50);
  }
  expect(
    selectedRepairIntervals(
      { ...page, gaps: page.gaps.map((gap) => ({ ...gap, retrySupported: false })) },
      ids,
    ),
  ).toBeNull();
});

test.each([
  [null, "missing"],
  [archiveRecord(), "retained_complete"],
  [{ ...archiveRecord(), hasConflict: true }, "retained_conflicting"],
  [
    {
      ...archiveRecord(),
      header: { ...archiveRecord().header, population: "partial", providerJobTotal: 2 },
    },
    "retained_incomplete",
  ],
] as const)("gap resolution follows current exact retained header %#", (retained, resolution) => {
  const page = archiveGapPage();
  const gap = { ...archiveGap(), retained, resolution };
  expect(archiveReadSchema.safeParse({ ...page, gaps: [gap] }).success).toBe(true);
  expect(
    archiveReadSchema.safeParse({ ...page, gaps: [{ ...gap, resolution: "not_evaluated" }] })
      .success,
  ).toBe(false);
  if (retained) {
    for (const change of [{ repositoryId: 9 }, { workflowRunId: 102 }, { runAttempt: 2 }]) {
      const foreign = {
        ...retained,
        header: { ...retained.header, attempt: { ...retained.header.attempt, ...change } },
      };
      expect(
        archiveReadSchema.safeParse({ ...page, gaps: [{ ...gap, retained: foreign }] }).success,
      ).toBe(false);
    }
  }
});
