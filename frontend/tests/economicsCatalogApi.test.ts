import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fetchEconomicsReports, fetchEconomicsSources } from "../src/api/ciEconomics/catalogClient";
import {
  reportPageSchema,
  sourceCursorSchema,
  sourcePageSchema,
} from "../src/api/ciEconomics/catalogSchema";
import {
  discoverEconomicsSources,
  registerEconomicsSource,
} from "../src/api/ciEconomics/sourceClient";
import {
  sourceDiscoveryInputSchema,
  sourceDiscoverySchema,
} from "../src/api/ciEconomics/sourceSchema";
import {
  DISCOVERY_INPUT,
  ECONOMICS_CSRF,
  ECONOMICS_SCOPE,
  economicsDiscovery,
  economicsReportPage,
  economicsSource,
  economicsSourceItem,
  economicsSourcePage,
  RECEIVED,
} from "./economicsConsoleFixture";

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

test("binds bounded catalog requests to exact source identities", async () => {
  const fetch = vi.fn(async (request: Request) =>
    Response.json(
      new URL(request.url).pathname.endsWith("/reports")
        ? economicsReportPage()
        : economicsSourcePage(),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, null)).kind).toBe("ready");
  expect((await fetchEconomicsReports(economicsSource(), null)).kind).toBe("ready");
  expect(fetch.mock.calls.map(([request]) => new URL(request.url).search)).toEqual([
    "?limit=20",
    "?limit=20",
  ]);
});

test.each([
  "",
  "0.1",
  "01.1",
  "1.0",
  "1.01",
  "1",
  "1.1.1",
  "9007199254740992.1",
  "1.9007199254740992",
  "1.-1",
  "1.1 ",
])("rejects noncanonical source cursor %s", (cursor) => {
  expect(sourceCursorSchema.safeParse(cursor).success).toBe(false);
});
test.each([0, 101, 1.5, Number.NaN])("rejects page limit %s before I/O", async (limit) => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, null, undefined, limit)).kind).toBe(
    "invalid-response",
  );
  expect((await fetchEconomicsReports(economicsSource(), null, undefined, limit)).kind).toBe(
    "invalid-response",
  );
  expect(fetch).not.toHaveBeenCalled();
});
test.each([
  { repositoryId: 2 },
  { installationId: 2 },
  { nextCursor: "1.1" },
  { items: [economicsSourceItem(), economicsSourceItem()] },
  { items: [economicsSourceItem(economicsSource(1)), economicsSourceItem(economicsSource(2))] },
  { items: [economicsSourceItem(economicsSource(4201, 2))] },
])("rejects inconsistent source page %j", async (delta) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...economicsSourcePage(), ...delta })),
  );
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, null)).kind).toBe("invalid-response");
});
test.each([
  { status: "expired" },
  { attemptCount: 9 },
  { nextAttemptAt: RECEIVED },
  { completedAt: null },
  { terminalReason: "deadline_exceeded" },
  { lastFailureReason: "evidence_conflict" },
  { status: "pending", attemptCount: 0 },
  { status: "deferred", completedAt: null, nextAttemptAt: RECEIVED },
])("rejects contradictory collection state %j", (delta) => {
  expect(
    sourcePageSchema.safeParse(
      economicsSourcePage([
        { ...economicsSourceItem(), ...delta } as ReturnType<typeof economicsSourceItem>,
      ]),
    ).success,
  ).toBe(false);
});
test("admits deadline closure before the first collection attempt", () => {
  const value = {
    ...economicsSourceItem(),
    status: "terminal_unavailable" as const,
    attemptCount: 0,
    terminalReason: "deadline_exceeded" as const,
  };
  expect(sourcePageSchema.safeParse(economicsSourcePage([value])).success).toBe(true);
});
test("rejects a forged canonical source digest and a cross-request cursor", async () => {
  const source = { ...economicsSource(), sourceId: "0".repeat(64) };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(economicsSourcePage([economicsSourceItem(source)]))),
  );
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, null)).kind).toBe("invalid-response");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(economicsSourcePage())),
  );
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, "4200.2")).kind).toBe("invalid-response");
});
test("admits descending numeric pagination and refuses a premature next cursor", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...economicsSourcePage(), nextCursor: "4201.2" })),
  );
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, "4202.1", undefined, 1)).kind).toBe("ready");
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, null)).kind).toBe("invalid-response");
});
test("keeps missing source, empty list, source mismatch and invalid continuation distinct", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...economicsReportPage(), items: [] })),
  );
  expect((await fetchEconomicsReports(economicsSource(), null)).kind).toBe("ready");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ok: false, error: "not_found" }, { status: 404 })),
  );
  expect((await fetchEconomicsReports(economicsSource(), null)).kind).toBe("not-found");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...economicsReportPage(), source: economicsSource(4202) })),
  );
  expect((await fetchEconomicsReports(economicsSource(), null)).kind).toBe("invalid-response");
  const page = economicsReportPage();
  expect(reportPageSchema.safeParse({ ...page, nextCursor: "0".repeat(64) }).success).toBe(false);
  expect(
    reportPageSchema.safeParse({ ...page, items: [...page.items, ...page.items] }).success,
  ).toBe(false);
});
test("discovery is an explicit CSRF-bound operation with the exact requested window", async () => {
  const fetch = vi.fn(async (_request: Request) => Response.json(economicsDiscovery()));
  vi.stubGlobal("fetch", fetch);
  expect((await discoverEconomicsSources(DISCOVERY_INPUT, ECONOMICS_CSRF)).kind).toBe("ready");
  const call = fetch.mock.calls[0];
  expect(call).toBeDefined();
  const request = call?.[0];
  if (!request) throw new Error("discovery request missing");
  expect(request.method).toBe("POST");
  expect(request.headers.get("x-csrf-token")).toBe(ECONOMICS_CSRF);
  expect(await request.json()).toEqual(DISCOVERY_INPUT);
});
test.each([
  { pageNumber: 2 },
  { createdFrom: "2026-09-07T00:00:00Z" },
  { installationId: 2 },
  { sources: [economicsSource(), economicsSource()] },
  { termination: "next_page" },
  { providerTotal: 0 },
  { createdThrough: "2026-09-01T00:00:00Z" },
])("rejects cross-request discovery %j", async (delta) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...economicsDiscovery(), ...delta })),
  );
  expect((await discoverEconomicsSources(DISCOVERY_INPUT, ECONOMICS_CSRF)).kind).toBe(
    "invalid-response",
  );
});
test("bounded truncated provider populations are not reported as exhausted", () => {
  expect(
    sourceDiscoverySchema.safeParse({
      ...economicsDiscovery(),
      providerTotal: 101,
      termination: "truncated",
    }).success,
  ).toBe(true);
  expect(
    sourceDiscoveryInputSchema.safeParse({
      ...DISCOVERY_INPUT,
      createdThrough: "2026-09-16T00:00:00Z",
    }).success,
  ).toBe(false);
});
test.each([
  [201, "registered"],
  [200, "replayed"],
  [409, "source_conflict"],
  [409, "outside_source_window"],
  [409, "capacity_reached"],
] as const)("preserves registration %s/%s", async (status, outcome) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(
        {
          schemaVersion: "ci-economics-source-registration/v2",
          source: economicsSource(),
          outcome,
        },
        { status },
      ),
    ),
  );
  expect(await registerEconomicsSource(economicsSource(), ECONOMICS_CSRF)).toMatchObject({
    kind: "ready",
    value: { outcome },
  });
});
test.each([
  [201, "replayed"],
  [200, "registered"],
  [409, "registered"],
] as const)("rejects registration status contradiction %s/%s", async (status, outcome) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(
        {
          schemaVersion: "ci-economics-source-registration/v2",
          source: economicsSource(),
          outcome,
        },
        { status },
      ),
    ),
  );
  expect((await registerEconomicsSource(economicsSource(), ECONOMICS_CSRF)).kind).toBe(
    "invalid-response",
  );
});
test("does not send malformed discovery or credential material", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  expect(
    (await discoverEconomicsSources({ ...DISCOVERY_INPUT, pageNumber: 0 }, ECONOMICS_CSRF)).kind,
  ).toBe("invalid-response");
  expect((await discoverEconomicsSources(DISCOVERY_INPUT, "")).kind).toBe("invalid-response");
  expect((await registerEconomicsSource(economicsSource(), "")).kind).toBe("invalid-response");
  expect(fetch).not.toHaveBeenCalled();
});
