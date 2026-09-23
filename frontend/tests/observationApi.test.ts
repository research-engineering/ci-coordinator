// @vitest-environment node
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  configureObservation,
  fetchObservationGaps,
  fetchObservationStatus,
  fetchObservationWorkflows,
} from "../src/api/ciEconomics/observationClient";
import {
  OBSERVATION_SCOPE,
  observationCommand,
  observationGap,
  observationGaps,
  observationMutation,
  observationStatus,
  observationWorkflows,
} from "./observationFixture";

const csrf = "s".repeat(43);
beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("location", new URL("https://coordinator.example"));
});
afterEach(() => vi.unstubAllGlobals());

test("bounded reads bind exact scope, page and canonical gap evidence", async () => {
  const fetch = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    return Response.json(
      path.endsWith("/gaps")
        ? observationGaps()
        : path.endsWith("/workflows")
          ? observationWorkflows()
          : observationStatus(),
    );
  });
  vi.stubGlobal("fetch", fetch);
  expect((await fetchObservationStatus(OBSERVATION_SCOPE)).kind).toBe("ready");
  expect((await fetchObservationGaps(OBSERVATION_SCOPE, null)).kind).toBe("ready");
  expect((await fetchObservationWorkflows(OBSERVATION_SCOPE, 1)).kind).toBe("ready");
  expect(fetch.mock.calls.map(([request]) => new URL(request.url).search)).toEqual([
    "",
    "?limit=20",
    "?pageNumber=1",
  ]);
  expect(
    fetch.mock.calls.every(
      ([request]) => request.credentials === "same-origin" && request.cache === "no-store",
    ),
  ).toBe(true);
});
test("mutation binds operation, revision, configuration, status and CSRF", async () => {
  const command = observationCommand();
  const fetch = vi.fn(async (_request: Request) => Response.json(observationMutation(command)));
  vi.stubGlobal("fetch", fetch);
  expect((await configureObservation(command, csrf)).kind).toBe("ready");
  const request = fetch.mock.calls[0]?.[0];
  expect(request?.method).toBe("POST");
  expect(request?.headers.get("x-csrf-token")).toBe(csrf);
  expect(await request?.json()).toEqual(command);
});
test.each([
  { operationId: "other" },
  { outcome: "revision_conflict" },
  { snapshot: null },
  ...[
    { revision: 2 },
    { repositoryId: 2 },
    { installationId: 2 },
    { configuration: { ...observationCommand().configuration, enabled: false } },
  ].map((delta) => ({ snapshot: { ...observationMutation().snapshot, ...delta } })),
])("rejects cross-operation mutation result %j", async (delta) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...observationMutation(), ...delta })),
  );
  expect((await configureObservation(observationCommand(), csrf)).kind).toBe("invalid-response");
});
test.each(["revision_conflict", "operation_conflict", "capacity_reached"])(
  "keeps %s conflict distinct from success",
  async (outcome) => {
    const result = { ...observationMutation(), snapshot: null, outcome };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(result, { status: 409 })),
    );
    expect(await configureObservation(observationCommand(), csrf)).toEqual({
      kind: "ready",
      value: result,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(result)),
    );
    expect((await configureObservation(observationCommand(), csrf)).kind).toBe("invalid-response");
  },
);
test.each(["installationId", "repositoryId"] as const)(
  "refuses foreign %s in reads",
  async (field) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ...observationStatus(null), [field]: 2 })),
    );
    expect((await fetchObservationStatus(OBSERVATION_SCOPE)).kind).toBe("invalid-response");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ...observationWorkflows(), [field]: 2 })),
    );
    expect((await fetchObservationWorkflows(OBSERVATION_SCOPE, 1)).kind).toBe("invalid-response");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ...observationGaps([]), [field]: 2 })),
    );
    expect((await fetchObservationGaps(OBSERVATION_SCOPE, null)).kind).toBe("invalid-response");
  },
);
test("gap identity is sensitive to independently mutated provenance and cursor", async () => {
  const gap = observationGap();
  for (const delta of [
    { configRevision: 2 },
    { selectorDigest: "b".repeat(64) },
    { reason: "source_conflict" },
    { lane: "backfill" },
    { createdFrom: "2026-09-08T00:00:00+00:00" },
  ]) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ ...observationGaps(), items: [{ ...gap, gap: { ...gap.gap, ...delta } }] }),
      ),
    );
    expect((await fetchObservationGaps(OBSERVATION_SCOPE, null)).kind).toBe("invalid-response");
  }
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(observationGaps())),
  );
  expect((await fetchObservationGaps(OBSERVATION_SCOPE, `1.1.1.${gap.gapId}`)).kind).toBe(
    "invalid-response",
  );
});
test("gap continuation preserves context and admits only later canonical gaps", async () => {
  const gap = observationGap();
  const cursor = `1.1.2.${"0".repeat(64)}`;
  const fetch = vi.fn(async () => Response.json(observationGaps()));
  vi.stubGlobal("fetch", fetch);
  expect((await fetchObservationGaps(OBSERVATION_SCOPE, cursor)).kind).toBe("ready");
  for (const invalid of [`2.1.2.${gap.gapId}`, `1.2.2.${gap.gapId}`, `${cursor}\n`]) {
    expect((await fetchObservationGaps(OBSERVATION_SCOPE, invalid)).kind).toBe("invalid-response");
  }
  expect(fetch).toHaveBeenCalledTimes(1);
});
test("invalid requests perform no transport I/O", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  expect((await fetchObservationStatus({ ...OBSERVATION_SCOPE, repositoryId: 0 })).kind).toBe(
    "invalid-response",
  );
  expect((await fetchObservationGaps(OBSERVATION_SCOPE, "bad")).kind).toBe("invalid-response");
  expect((await fetchObservationWorkflows(OBSERVATION_SCOPE, 21)).kind).toBe("invalid-response");
  expect((await configureObservation(observationCommand(), "bad")).kind).toBe("invalid-response");
  expect(
    (await configureObservation({ ...observationCommand(), expectedRevision: -1 }, csrf)).kind,
  ).toBe("invalid-response");
  expect(fetch).not.toHaveBeenCalled();
});
test("mismatched page and oversized bodies do not become empty success", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(observationWorkflows())),
  );
  expect((await fetchObservationWorkflows(OBSERVATION_SCOPE, 2)).kind).toBe("invalid-response");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...observationStatus(), noise: "x".repeat(16 * 1024) })),
  );
  expect((await fetchObservationStatus(OBSERVATION_SCOPE)).kind).toBe("invalid-response");
});
