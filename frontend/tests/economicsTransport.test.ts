import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fetchEconomicsSources } from "../src/api/ciEconomics/catalogClient";
import { discoverEconomicsSources } from "../src/api/ciEconomics/sourceClient";
import {
  DISCOVERY_INPUT,
  ECONOMICS_CSRF,
  ECONOMICS_SCOPE,
  economicsSourcePage,
} from "./economicsConsoleFixture";

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => {
  vi.unstubAllGlobals();
});
test.each([
  [401, "unauthenticated", "unauthenticated"],
  [403, "forbidden", "forbidden"],
  [404, "not_found", "not-found"],
  [503, "unavailable", "unavailable"],
  [503, "overloaded", "unavailable"],
  [503, "provider_unavailable", "unavailable"],
  [401, "forbidden", "invalid-response"],
  [403, "unauthenticated", "invalid-response"],
  [404, "unavailable", "invalid-response"],
  [503, "not_found", "invalid-response"],
  [500, "unavailable", "invalid-response"],
  [400, "invalid_request", "invalid-response"],
])("keeps HTTP status and error algebra bound %s/%s", async (status, error, kind) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ok: false, error }, { status })),
  );
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, null)).kind).toBe(kind);
});
test.each([
  () => new Response("{}", { headers: { "content-type": "text/html" } }),
  () => new Response("{", { headers: { "content-type": "application/json" } }),
  () => Response.json({}),
  () => Response.json({ ...economicsSourcePage(), extra: "undeclared" }),
  () =>
    new Response(" ".repeat(256 * 1024 + 1), { headers: { "content-type": "application/json" } }),
])("rejects malformed and bounded responses %#", async (make) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => make()),
  );
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, null)).kind).toBe("invalid-response");
});
test("a caller abort is not displayed as provider failure", async () => {
  const controller = new AbortController();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      controller.abort();
      throw new DOMException("Aborted", "AbortError");
    }),
  );
  await expect(
    fetchEconomicsSources(ECONOMICS_SCOPE, null, controller.signal),
  ).rejects.toMatchObject({ name: "AbortError" });
});
test("a network failure stays separate from invalid evidence", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new TypeError("offline");
    }),
  );
  expect((await fetchEconomicsSources(ECONOMICS_SCOPE, null)).kind).toBe("network-failure");
  expect((await discoverEconomicsSources(DISCOVERY_INPUT, ECONOMICS_CSRF)).kind).toBe(
    "network-failure",
  );
});
