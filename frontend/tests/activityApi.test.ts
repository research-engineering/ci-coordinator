import { afterEach, expect, test, vi } from "vitest";
import { fetchActivity } from "../src/api/activity/client";
import {
  type ActivityPage,
  activityPageSchema,
  activityQuerySchema,
} from "../src/api/activity/schema";
import { proxyRequestIsAdmitted } from "../src/api/development/proxyPolicy";
import { activityPage, activityQuery } from "./activityFixture";

afterEach(() => vi.unstubAllGlobals());

test.each(["security", "business"] as const)(
  "%s reads and exports use scoped bounded transport",
  async (source) => {
    const query = activityQuery(source);
    const fetch = vi.fn(async (_request: Request) => Response.json(activityPage(query)));
    vi.stubGlobal("fetch", fetch);
    for (const exporting of [false, true]) {
      expect((await fetchActivity(query, null, new AbortController().signal, exporting)).kind).toBe(
        "ready",
      );
      const request = fetch.mock.calls.at(-1)?.[0];
      expect(request?.method).toBe("GET");
      expect(request?.cache).toBe("no-store");
      expect(request?.credentials).toBe("same-origin");
      const url = new URL(request?.url ?? "");
      expect(url.pathname.endsWith("/export")).toBe(exporting);
      expect(proxyRequestIsAdmitted("GET", url.pathname + url.search)).toBe(true);
      expect(url.searchParams.has("issuer")).toBe(false);
    }
  },
);

test.each([
  [
    "security actor class",
    (page: ActivityPage) => {
      if (page.items[0]) page.items[0].actor = "break-glass:v1:operator";
    },
  ],
  [
    "source",
    (page: ActivityPage) => {
      page.context.source = "business";
    },
  ],
  [
    "role outcome",
    (page: ActivityPage) => {
      if (page.items[0]) page.items[0].outcome = "denied";
    },
  ],
  [
    "foreign issuer",
    (page: ActivityPage) => {
      if (page.items[0]) page.items[0].issuer = "https://foreign.test";
    },
  ],
  [
    "descending sequence",
    (page: ActivityPage) => {
      if (page.items[0]) page.items.push({ ...page.items[0] });
    },
  ],
  [
    "event after observation",
    (page: ActivityPage) => {
      if (page.items[0]) page.items[0].occurredAt = "2026-09-13T00:00:00.000001Z";
    },
  ],
  [
    "retention",
    (page: ActivityPage) => {
      page.retentionSeconds = null;
    },
  ],
  [
    "empty continuation",
    (page: ActivityPage) => {
      page.items = [];
      page.nextCursor = "cursor";
    },
  ],
] as const)("rejects isolated page contradiction: %s", (_name, mutate) => {
  const page = activityPage();
  expect(activityPageSchema.safeParse(page).success).toBe(true);
  mutate(page);
  expect(activityPageSchema.safeParse(page).success).toBe(false);
});

test.each(["repository", "actor", "window", "event hash"])(
  "business response binds %s",
  async (change) => {
    const query = activityQuery("business");
    const page = activityPage(query);
    if (change === "repository") page.context.repositoryId = 32;
    if (change === "actor") query.actor = `keycloak-human:v1:${"d".repeat(64)}`;
    if (change === "window" && page.items[0])
      page.items[0].occurredAt = "2026-09-09T23:59:59.999999Z";
    if (change === "event hash" && page.items[0]) page.items[0].eventHash = "d".repeat(64);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(page)),
    );
    expect((await fetchActivity(query, null, new AbortController().signal)).kind).toBe(
      "invalid-response",
    );
  },
);

test.each([
  [401, "unauthenticated"],
  [403, "forbidden"],
  [503, "unavailable"],
] as const)("HTTP %s retains its exact failure algebra", async (status, error) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ok: false, error }, { status })),
  );
  expect(await fetchActivity(activityQuery(), null, new AbortController().signal)).toEqual({
    kind: error,
  });
});

test.each([
  { until: "2026-09-10T00:00:00Z" },
  { until: "2026-11-10T00:00:00Z" },
  { since: "2026-09-10T00:00:00.000001Z" },
  { installationId: 7 },
  { action: "unknown" },
  { limit: 101 },
])("invalid filters are rejected before HTTP %#", async (change) => {
  const query = { ...activityQuery(), ...change };
  expect(activityQuerySchema.safeParse(query).success).toBe(false);
});

test.each([
  ["POST", "/api/v1/activity/security?since=2026-09-10T00:00:00Z&until=2026-09-13T00:00:00Z"],
  [
    "GET",
    "/api/v1/activity/security?since=2026-09-10T00:00:00Z&until=2026-09-13T00:00:00Z&actor=one&actor=two",
  ],
  [
    "GET",
    "/api/v1/activity/security?since=2026-09-10T00:00:00Z&until=2026-09-13T00:00:00Z&secret=hidden",
  ],
  [
    "GET",
    "/api/v1/activity/repositories/0/31?since=2026-09-10T00:00:00Z&until=2026-09-13T00:00:00Z",
  ],
])("native proxy rejects %s %s", (method, url) => {
  expect(proxyRequestIsAdmitted(method, url)).toBe(false);
});

test.each(["toString", "constructor", "__proto__"])(
  "inherited %s is not an admitted query key",
  (key) => {
    expect(
      proxyRequestIsAdmitted(
        "GET",
        `/api/v1/activity/security?since=2026-09-10T00:00:00Z&until=2026-09-13T00:00:00Z&${key}=x`,
      ),
    ).toBe(false);
  },
);
