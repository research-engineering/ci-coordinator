// @vitest-environment node
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { configureHistory, fetchHistoryStatus } from "../src/api/ciEconomics/historyClient";
import {
  HISTORY_SCOPE,
  historyCommand,
  historyDataset,
  historyMutation,
  historyStatus,
} from "./historyFixture";

const csrf = "s".repeat(43);
const UNEQUAL_SCOPE = { installationId: 37, repositoryId: 902 } as const;

beforeEach(() => vi.stubGlobal("location", new URL("https://coordinator.example")));
afterEach(() => vi.unstubAllGlobals());

test("reads and writes use exact routes, no-store, session credentials and CSRF", async () => {
  const fetch = vi.fn(async (request: Request) =>
    Response.json(request.method === "POST" ? historyMutation() : historyStatus()),
  );
  vi.stubGlobal("fetch", fetch);
  expect((await fetchHistoryStatus(HISTORY_SCOPE)).kind).toBe("ready");
  expect((await configureHistory(historyCommand(), csrf)).kind).toBe("ready");
  const [read, write] = fetch.mock.calls.map(([request]) => request);
  expect(new URL(read?.url ?? "").pathname).toBe("/api/v2/economics/repositories/1/1/history");
  expect(new URL(write?.url ?? "").pathname).toBe("/api/v2/economics/history");
  expect(write?.headers.get("x-csrf-token")).toBe(csrf);
  expect(await write?.json()).toEqual(historyCommand());
  expect(
    fetch.mock.calls.every(
      ([request]) => request.cache === "no-store" && request.credentials === "same-origin",
    ),
  ).toBe(true);
});

test("an earlier-bound command remains exact across the API transport", async () => {
  const command = {
    ...historyCommand(),
    expectedRevision: 1,
    initialCreatedFrom: null,
    expandCreatedFrom: "2019-12-01T00:00:00Z",
  };
  const fetch = vi.fn(async (_request: Request) => Response.json(historyMutation(command)));
  vi.stubGlobal("fetch", fetch);

  expect((await configureHistory(command, csrf)).kind).toBe("ready");
  const request = fetch.mock.calls[0]?.[0];
  const body: unknown = await request?.json();
  expect(body).toEqual(command);
  expect(body).not.toHaveProperty("actor");
});

test("binds unequal nondefault IDs through the actual GET path, POST body and response", async () => {
  const command = { ...historyCommand(), ...UNEQUAL_SCOPE };
  const status = historyStatus({ ...historyDataset(), ...UNEQUAL_SCOPE });
  const mutation = historyMutation(command);
  const fetch = vi.fn(async (request: Request) =>
    Response.json(request.method === "POST" ? mutation : status),
  );
  vi.stubGlobal("fetch", fetch);

  const readResult = await fetchHistoryStatus(UNEQUAL_SCOPE);
  const writeResult = await configureHistory(command, csrf);
  expect(readResult).toEqual({ kind: "ready", value: status });
  expect(writeResult).toEqual({ kind: "ready", value: mutation });

  const [read, write] = fetch.mock.calls.map(([request]) => request);
  expect(read?.method).toBe("GET");
  expect(new URL(read?.url ?? "").pathname).toBe("/api/v2/economics/repositories/37/902/history");
  expect(write?.method).toBe("POST");
  expect(await write?.json()).toEqual(command);
  expect(command.installationId).toBe(37);
  expect(command.repositoryId).toBe(902);
});

test.each(["foreign", "swapped"] as const)(
  "rejects %s scope from both status and mutation responses",
  async (kind) => {
    const responseScope =
      kind === "foreign"
        ? { installationId: 38, repositoryId: 902 }
        : { installationId: 902, repositoryId: 37 };
    const command = { ...historyCommand(), ...UNEQUAL_SCOPE };
    const status = historyStatus({ ...historyDataset(), ...responseScope });
    const mutation = historyMutation({ ...command, ...responseScope });
    const fetch = vi.fn(async (request: Request) =>
      Response.json(request.method === "POST" ? mutation : status),
    );
    vi.stubGlobal("fetch", fetch);

    expect((await fetchHistoryStatus(UNEQUAL_SCOPE)).kind).toBe("invalid-response");
    expect((await configureHistory(command, csrf)).kind).toBe("invalid-response");
  },
);

test.each([
  "revision_conflict",
  "operation_conflict",
  "capacity_reached",
  "dataset_fenced",
  "pending_work",
  "invalid_population",
])("preserves known rejection %s without false success", async (outcome) => {
  const value = { ...historyMutation(), outcome, snapshot: null };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(value, { status: outcome === "invalid_population" ? 400 : 409 }),
    ),
  );
  expect(await configureHistory(historyCommand(), csrf)).toEqual({ kind: "ready", value });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(value)),
  );
  expect((await configureHistory(historyCommand(), csrf)).kind).toBe("invalid-response");
});
test.each([
  { operationId: "foreign" },
  { snapshot: null },
  ...[
    { installationId: 2 },
    { repositoryId: 2 },
    { configurationRevision: 2 },
    { configuration: { ...historyCommand().configuration, enabled: false }, state: "paused" },
    { configuration: { ...historyCommand().configuration, detailRetention: { mode: "forever" } } },
  ].map((patch) => ({ snapshot: { ...historyDataset(), ...patch } })),
])("rejects mismatched receipt %j", async (patch) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...historyMutation(), ...patch })),
  );
  expect((await configureHistory(historyCommand(), csrf)).kind).toBe("invalid-response");
});
test.each(["installationId", "repositoryId"] as const)(
  "rejects foreign status %s",
  async (field) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ...historyStatus(null), [field]: 2 })),
    );
    expect((await fetchHistoryStatus(HISTORY_SCOPE)).kind).toBe("invalid-response");
  },
);
test("invalid input and token cannot reach network", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  expect((await configureHistory(historyCommand(), "bad")).kind).toBe("invalid-response");
  expect((await configureHistory({ ...historyCommand(), expectedRevision: -1 }, csrf)).kind).toBe(
    "invalid-response",
  );
  expect((await fetchHistoryStatus({ ...HISTORY_SCOPE, installationId: 0 })).kind).toBe(
    "invalid-response",
  );
  expect(fetch).not.toHaveBeenCalled();
});
test.each([
  [401, "unauthenticated"],
  [403, "forbidden"],
  [503, "unavailable"],
] as const)("keeps auth and outage recovery for %s", async (status, error) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ok: false, error }, { status })),
  );
  expect((await fetchHistoryStatus(HISTORY_SCOPE)).kind).toBe(error);
});
test("malformed and lost responses never confirm a write", async () => {
  for (const response of [
    Response.json({}),
    Response.json({ ok: false, error: "invalid_request" }, { status: 400 }),
  ]) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response),
    );
    expect((await configureHistory(historyCommand(), csrf)).kind).toBe("invalid-response");
  }
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new TypeError("offline");
    }),
  );
  expect((await configureHistory(historyCommand(), csrf)).kind).toBe("network-failure");
});

test.each([8192, 8193])(
  "only the response-byte limit rejects a valid %s-byte receipt",
  async (size) => {
    const text = JSON.stringify(historyMutation()).padEnd(size, " ");
    expect(new TextEncoder().encode(text)).toHaveLength(size);
    expect(JSON.parse(text)).toEqual(historyMutation());
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(text, { headers: { "content-type": "application/json" } })),
    );
    expect((await configureHistory(historyCommand(), csrf)).kind).toBe(
      size === 8192 ? "ready" : "invalid-response",
    );
  },
);
