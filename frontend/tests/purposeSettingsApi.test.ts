import { afterEach, expect, test, vi } from "vitest";
import {
  configurePurposeSettings,
  fetchPurposeSettings,
} from "../src/api/ciEconomics/purposeSettingsClient";
import { decodePurposeSettings } from "../src/api/ciEconomics/purposeSettingsDecoder";
import {
  purposeSettingsCommandSchema,
  purposeSettingsSnapshotSchema,
} from "../src/api/ciEconomics/purposeSettingsSchema";
import { proxyRequestIsAdmitted } from "../src/api/development/proxyPolicy";
import { purposeCommand, purposeQuery, purposeSnapshot } from "./purposeSettingsFixture";

function rawResponse(body: BodyInit): Response {
  return new Response(body, { headers: { "content-type": "application/json" } });
}

function readPayload(): string {
  return JSON.stringify({ outcome: "available", snapshot: purposeSnapshot(), unavailable: null });
}

test.each([
  ["different duplicate", '"repositoryId":2,"repositoryId":1'],
  ["equal duplicate", '"repositoryId":1,"repositoryId":1'],
  ["escaped duplicate", '"repositoryId":1,"repository\\u0049d":1'],
  ["rounded decimal", '"repositoryId":1.00000000000000001'],
  ["decimal coercion", '"repositoryId":1.0'],
  ["exponent coercion", '"repositoryId":1e0'],
  ["unsafe integer", '"repositoryId":9007199254740993'],
])("rejects raw %s before lossy JSON admission", async (_name, field) => {
  const payload = readPayload().replace('"repositoryId":1', field);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => rawResponse(payload)),
  );
  expect(await fetchPurposeSettings(purposeQuery)).toEqual({ kind: "invalid-response" });
});

test.each([
  "{} {}",
  '{"outcome":"available",}',
  `/*comment*/${readPayload()}`,
  `[${"[".repeat(16)}0${"]".repeat(16)}]`,
  JSON.stringify(Array.from({ length: 4096 }, () => null)),
])("rejects malformed or excessive raw JSON", async (payload) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => rawResponse(payload)),
  );
  expect(await fetchPurposeSettings(purposeQuery)).toEqual({ kind: "invalid-response" });
});

test.each([
  ["depth", `${"[".repeat(17)}0${"]".repeat(17)}`],
  ["node", JSON.stringify(Array.from({ length: 4096 }, () => null))],
])("decoder independently enforces its %s bound", async (bound, payload) => {
  await expect(decodePurposeSettings(rawResponse(payload))).rejects.toThrow(
    `${bound} limit exceeded`,
  );
});

test("decoder accepts the resource-bound predecessors", async () => {
  const nested = `${"[".repeat(16)}0${"]".repeat(16)}`;
  expect(await decodePurposeSettings(rawResponse(nested))).toEqual(JSON.parse(nested));
  const array = Array.from({ length: 4095 }, () => null);
  expect(await decodePurposeSettings(rawResponse(JSON.stringify(array)))).toEqual(array);
});

test("UTF-8 damage is rejected even when replacement decoding would match the command", async () => {
  const command = {
    ...purposeCommand(),
    entries: [{ workflowId: 17, jobName: "Ruff\ufffd", purposes: ["lint" as const] }],
  };
  const body = JSON.stringify({
    operationId: command.operationId,
    outcome: "committed",
    snapshot: purposeSnapshot(command),
  });
  const [prefix, suffix] = body.split("\ufffd");
  if (prefix === undefined || suffix === undefined) throw new Error("missing UTF-8 fixture marker");
  const bytes = new Uint8Array([
    ...new TextEncoder().encode(prefix),
    0xff,
    ...new TextEncoder().encode(suffix),
  ]);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => rawResponse(bytes)),
  );
  expect(await configurePurposeSettings(command, "csrf")).toEqual({ kind: "invalid-response" });
});

test.each([1, Number.MAX_SAFE_INTEGER])(
  "preserves safe integer %i, whitespace and escapes",
  async (repositoryId) => {
    const snapshot = { ...purposeSnapshot(), repositoryId };
    const payload = JSON.stringify(
      { unavailable: null, snapshot, outcome: "available" },
      null,
      2,
    ).replace('"repositoryId"', '"repository\\u0049d"');
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => rawResponse(payload)),
    );
    expect(await fetchPurposeSettings({ ...purposeQuery, repositoryId })).toEqual({
      kind: "ready",
      value: { unavailable: null, snapshot, outcome: "available" },
    });
  },
);

afterEach(() => vi.unstubAllGlobals());
const path = "/api/v2/economics/repositories/1/1/history/analytics/settings";

test("PUT binds method, CSRF, scope, generation, operation and admitted payload", async () => {
  const command = purposeCommand();
  const fetch = vi.fn(async (request: Request) => {
    expect(request.method).toBe("PUT");
    expect(new URL(request.url).pathname).toBe(path);
    expect(request.headers.get("x-csrf-token")).toBe("csrf");
    expect(await request.json()).toEqual(command);
    return Response.json({
      operationId: command.operationId,
      outcome: "committed",
      snapshot: purposeSnapshot(command),
    });
  });
  vi.stubGlobal("fetch", fetch);
  expect((await configurePurposeSettings(command, "csrf")).kind).toBe("ready");
});

test.each(["operation", "scope", "generation", "revision", "payload", "status"])(
  "rejects substituted %s response",
  async (fault) => {
    const command = purposeCommand();
    const snapshot = purposeSnapshot(command);
    if (fault === "scope") snapshot.repositoryId = 2;
    if (fault === "generation") snapshot.generation = 2;
    if (fault === "revision") snapshot.revision = 2;
    if (fault === "payload" && snapshot.mapping)
      snapshot.mapping = { ...snapshot.mapping, entries: [] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            operationId: fault === "operation" ? "other" : command.operationId,
            outcome: "replayed",
            snapshot,
          },
          { status: fault === "status" ? 409 : 200 },
        ),
      ),
    );
    expect(await configurePurposeSettings(command, "csrf")).toEqual({ kind: "invalid-response" });
  },
);

test("missing mapping and saved empty mapping remain distinguishable", async () => {
  const missing = purposeSnapshot();
  const empty = purposeSnapshot({ ...purposeCommand(), entries: [] });
  expect(purposeSettingsSnapshotSchema.parse(missing).mapping).toBeNull();
  expect(purposeSettingsSnapshotSchema.parse(empty).mapping?.entries).toEqual([]);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      expect(request.method).toBe("GET");
      expect(new URL(request.url).search).toBe("?generation=1");
      return Response.json({ outcome: "available", snapshot: missing, unavailable: null });
    }),
  );
  expect((await fetchPurposeSettings(purposeQuery)).kind).toBe("ready");
});

test("Unicode scalar and cardinality oracles preserve exact names", () => {
  const command = purposeCommand();
  const entry = command.entries[0];
  if (!entry) throw new Error("fixture entry missing");
  for (const name of ["\u{1f680}".repeat(512), "e\u0301", "\u00e9", "Ruff", "ruff"])
    expect(
      purposeSettingsCommandSchema.parse({ ...command, entries: [{ ...entry, jobName: name }] })
        .entries[0]?.jobName,
    ).toBe(name);
  for (const name of ["", "\u{1f680}".repeat(513), "a\u0000b", "\ud800"])
    expect(
      purposeSettingsCommandSchema.safeParse({ ...command, entries: [{ ...entry, jobName: name }] })
        .success,
    ).toBe(false);
  expect(
    purposeSettingsCommandSchema.safeParse({ ...command, entries: [entry, entry] }).success,
  ).toBe(false);
  expect(
    purposeSettingsCommandSchema.safeParse({
      ...command,
      entries: [{ ...entry, purposes: ["lint", "lint"] }],
    }).success,
  ).toBe(false);
  for (const length of [64, 65])
    expect(
      purposeSettingsCommandSchema.safeParse({
        ...command,
        entries: Array.from({ length }, (_, index) => ({ ...entry, workflowId: index + 1 })),
      }).success,
    ).toBe(length === 64);
});

test("development proxy admits only exact GET generation and query-free PUT", () => {
  expect(proxyRequestIsAdmitted("PUT", path)).toBe(true);
  expect(proxyRequestIsAdmitted("GET", `${path}?generation=1`)).toBe(true);
  for (const [method, url] of [
    ["POST", path],
    ["PUT", `${path}?generation=1`],
    ["GET", path],
    ["GET", `${path}?generation=1&generation=1`],
    ["GET", `${path}?generation=1&actor=x`],
  ])
    expect(proxyRequestIsAdmitted(method, url)).toBe(false);
});
