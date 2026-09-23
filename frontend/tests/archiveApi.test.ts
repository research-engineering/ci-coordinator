import { afterEach, expect, test, vi } from "vitest";
import {
  fetchArchiveDetail,
  fetchArchivePage,
  MAX_ARCHIVE_RESPONSE_BYTES,
} from "../src/api/ciEconomics/archiveReadClient";
import {
  type ArchiveDetailRead,
  archiveReadSchema,
} from "../src/api/ciEconomics/archiveReadSchema";
import {
  archiveAttemptDetailSchema,
  archiveRecordSchema,
} from "../src/api/ciEconomics/archiveRecordSchema";
import {
  applyArchiveRetention,
  previewArchiveRetention,
} from "../src/api/ciEconomics/archiveRetentionClient";
import { retentionPreviewSchema } from "../src/api/ciEconomics/archiveRetentionSchema";
import { economicsProxyRequestIsAdmitted } from "../src/api/development/economicsProxyPolicy";
import {
  archiveDetailPage,
  archiveDetailPayload,
  archiveJob,
  archivePage,
  archiveQuery,
  archiveRecord,
  retentionPreview,
} from "./archiveFixture";

afterEach(() => vi.unstubAllGlobals());
test.each(["records", "jobs", "gaps", "detail"] as const)(
  "archive %s request binds its query and private transport",
  async (kind) => {
    const query = archiveQuery(kind);
    const page = archivePage(query);
    const fetch = vi.fn(async (_request: Request) => Response.json(page));
    vi.stubGlobal("fetch", fetch);
    expect(await fetchArchivePage(query, null)).toEqual({ kind: "ready", value: page });
    const request = fetch.mock.calls[0]?.[0] as Request | undefined;
    expect(request?.credentials).toBe("same-origin");
    expect(request?.cache).toBe("no-store");
    expect(economicsProxyRequestIsAdmitted("GET", new URL(request?.url ?? ""))).toBe(true);
  },
);

test("detail request binds the selected attempt, generation and bounded transport", async () => {
  const query = archiveDetailPage().query;
  const page = archiveDetailPage(query);
  const fetch = vi.fn(async (_request: Request) => Response.json(page));
  vi.stubGlobal("fetch", fetch);
  expect(await fetchArchiveDetail(query)).toEqual({ kind: "ready", value: page });
  const request = fetch.mock.calls[0]?.[0] as Request | undefined;
  expect(new URL(request?.url ?? "").pathname).toBe(
    "/api/v2/economics/repositories/1/2/history/attempts/101/1/detail",
  );
  expect(new URL(request?.url ?? "").search).toBe("?generation=3");
  expect(request?.cache).toBe("no-store");
  expect(request?.credentials).toBe("same-origin");
  expect(economicsProxyRequestIsAdmitted(request?.method, new URL(request?.url ?? ""))).toBe(true);
});

test.each([
  { query: { ...archiveDetailPage().query, repositoryId: 9 } },
  { detailPayload: { ...archiveDetailPayload(), schemaVersion: "legacy" } },
])("rejects contradictory detail response %#", async (changes) => {
  const query = archiveDetailPage().query;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...archiveDetailPage(query), ...changes })),
  );
  expect((await fetchArchiveDetail(query)).kind).toBe("invalid-response");
});

test.each(["not_imported", "retained", "expired"] as const)(
  "accepts unavailable detail with null payload and %s retention",
  async (state) => {
    const page = archiveDetailPage();
    page.detailPayload = null;
    page.detail =
      state === "not_imported"
        ? archiveRecord().detail
        : {
            ...page.detail,
            state,
            content: state === "expired" ? "expired" : "unavailable_format",
          };
    page.record.detail = page.detail;
    if (state === "expired") page.observedAt = "2026-10-12T10:00:00Z";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(page)),
    );
    expect(await fetchArchiveDetail(page.query)).toEqual({ kind: "ready", value: page });
  },
);

test("admits a conflicting retained parent without detail content", async () => {
  const page = archiveDetailPage();
  page.record.hasConflict = true;
  page.detailPayload = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(page)),
  );
  expect(await fetchArchiveDetail(page.query)).toEqual({ kind: "ready", value: page });
});

const invalidDetailBindings: readonly [string, (page: ArchiveDetailRead) => void][] = [
  [
    "not imported",
    (page) => {
      page.detail = archiveRecord().detail;
    },
  ],
  [
    "expired state",
    (page) => {
      page.detail = { ...page.detail, state: "expired", content: "expired" };
    },
  ],
  [
    "foreign head",
    (page) => {
      page.record.header.attempt = { ...page.record.header.attempt, headSha: "b".repeat(40) };
    },
  ],
  [
    "partial parent",
    (page) => {
      page.record.header.population = "partial";
      page.record.header.providerJobTotal = 2;
    },
  ],
  [
    "conflict population",
    (page) => {
      page.record.header.population = "conflict";
    },
  ],
  [
    "conflict flag",
    (page) => {
      page.record.hasConflict = true;
    },
  ],
  [
    "expiry boundary",
    (page) => {
      page.observedAt = "2026-10-12T10:00:00Z";
    },
  ],
  [
    "past expiry",
    (page) => {
      page.observedAt = "2026-10-12T10:00:00.000001Z";
    },
  ],
  [
    "future detail import",
    (page) => {
      page.detail = {
        ...page.detail,
        firstImportedAt: "2026-09-12T10:00:00.000001Z",
        expiresAt: "2026-10-12T10:00:00.000001Z",
      };
    },
  ],
  [
    "future record import",
    (page) => {
      page.record.firstImportedAt = "2026-09-12T10:00:00.000001Z";
    },
  ],
  [
    "future run creation",
    (page) => {
      page.record.header.runCreatedAt = "2026-09-12T10:00:00.000001Z";
    },
  ],
];

test.each(invalidDetailBindings)("rejects payload with %s", async (_name, change) => {
  const page = archiveDetailPage();
  change(page);
  page.record.detail = page.detail;
  expect(archiveRecordSchema.safeParse(page.record).success).toBe(true);
  expect(archiveAttemptDetailSchema.safeParse(page.detailPayload).success).toBe(true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(page)),
  );
  expect((await fetchArchiveDetail(page.query)).kind).toBe("invalid-response");
});

test("admits retained detail one microsecond before logical expiry", async () => {
  const page = { ...archiveDetailPage(), observedAt: "2026-10-12T09:59:59.999999Z" };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(page)),
  );
  expect(await fetchArchiveDetail(page.query)).toEqual({ kind: "ready", value: page });
});

test.each([
  ["GET", "/history/attempts/101/1/detail"],
  ["GET", "/history/attempts/101/1/detail?generation=3&generation=3"],
  ["GET", "/history/attempts/101/1/detail?generation=3&cursor=x"],
  ["GET", "/history/attempts/101/1/detail?generation=0"],
  ["GET", "/history/attempts/101/1/detail?generation=3.0"],
  ["GET", "/history/attempts/101/1/detail?generation=9007199254740992"],
  ["GET", "/history/attempts/9007199254740992/1/detail?generation=3"],
  ["GET", "/history/attempts/101/9007199254740992/detail?generation=3"],
  ["GET", "/history/attempts/0/1/detail?generation=3"],
  ["GET", "/history/attempts/101/01/detail?generation=3"],
  ["GET", "/history/attempts/101/1/detail/extra?generation=3"],
  ["POST", "/history/attempts/101/1/detail?generation=3"],
  ["PUT", "/history/attempts/101/1/detail?generation=3"],
])("detail proxy rejects %s %s", (method, path) => {
  expect(
    economicsProxyRequestIsAdmitted(
      method,
      new URL(`https://local.test/api/v2/economics/repositories/1/2${path}`),
    ),
  ).toBe(false);
});

test.each([
  { query: { ...archiveQuery(), repositoryId: 1 } },
  { query: { ...archiveQuery(), generation: 4 } },
  { query: { ...archiveQuery(), workflowId: 18 } },
  { records: [archiveRecord(), archiveRecord()] },
  { records: [{ ...archiveRecord(), header: { ...archiveRecord().header, providerJobTotal: 2 } }] },
  { jobs: [archiveJob()] },
  { observedAt: "2020-01-01T00:00:00Z" },
  { records: [], nextCursor: "continuation" },
  { nextCursor: "x".repeat(4097) },
  { providerCompleteness: "complete" },
  { extra: true },
])("rejects contradictory archive response %#", async (changes) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ...archivePage(), ...changes })),
  );
  expect((await fetchArchivePage(archiveQuery(), null)).kind).toBe("invalid-response");
});

test("expired cursor is recoverable but never a successful empty page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ok: false, error: "stale_cursor" }, { status: 409 })),
  );
  expect(await fetchArchivePage(archiveQuery(), "cursor")).toEqual({
    kind: "unavailable",
    reason: "stale_cursor",
  });
});

test("retention preview and exact replay preserve reviewed effects", async () => {
  const reviewed = retentionPreview();
  const command = {
    selection: reviewed.preview.selection,
    reviewedDigest: reviewed.reviewedDigest,
    operationId: "operation",
  };
  const result = {
    outcome: "replayed",
    operationId: "operation",
    preview: reviewed.preview,
    dataRevision: 6,
  };
  const fetch = vi.fn(async (request: Request) =>
    Response.json(new URL(request.url).pathname.endsWith("preview") ? reviewed : result),
  );
  vi.stubGlobal("fetch", fetch);
  expect((await previewArchiveRetention(command.selection)).kind).toBe("ready");
  expect(await applyArchiveRetention(command, reviewed, "c".repeat(43))).toEqual({
    kind: "ready",
    value: result,
  });
  expect(fetch.mock.calls[1]?.[0].headers.get("x-csrf-token")).toBe("c".repeat(43));
  expect(await fetch.mock.calls[1]?.[0].json()).toEqual(command);
  expect(
    (
      await applyArchiveRetention(
        { ...command, reviewedDigest: "a".repeat(64) },
        reviewed,
        "c".repeat(43),
      )
    ).kind,
  ).toBe("invalid-response");
  expect(fetch).toHaveBeenCalledTimes(2);
});

test.each([
  { releasedBytes: 1 },
  { deletedDetails: 1 },
  { statisticsPreserved: false },
  { effects: [] },
])("retention preview accounting is causal %#", (changes) => {
  const value = retentionPreview();
  expect(
    retentionPreviewSchema.safeParse({ ...value, preview: { ...value.preview, ...changes } })
      .success,
  ).toBe(false);
});

test("job and detail pages reject foreign populations and inconsistent retention", () => {
  const jobs = archivePage(archiveQuery("jobs"));
  expect(archiveReadSchema.safeParse({ ...jobs, jobs: [archiveJob(), archiveJob()] }).success).toBe(
    false,
  );
  const detail = archivePage(archiveQuery("detail"));
  expect(
    archiveReadSchema.safeParse({ ...detail, detail: { ...detail.detail, state: "retained" } })
      .success,
  ).toBe(false);
});

test.each([
  ["GET", "/history/archive/records?generation=3&limit=51"],
  ["GET", "/history/archive/jobs?generation=3"],
  ["GET", "/history/archive/records?generation=3&generation=3"],
  ["GET", "/history/archive/records?generation=3&actor=admin"],
  ["GET", "/history/archive/records?generation=3.0"],
  ["POST", "/history/archive/records?generation=3"],
  [
    "GET",
    "/history/analytics?generation=3&createdFrom=2026-01-01T00:00:00Z&createdUntil=2026-01-03T00:00:00Z&installationId=999",
  ],
])("proxy rejects %s %s", (method, path) => {
  expect(
    economicsProxyRequestIsAdmitted(
      method,
      new URL(`https://local.test/api/v2/economics/repositories/1/2${path}`),
    ),
  ).toBe(false);
});

test("a full admitted escaped-label page fits the finite archive transport budget", async () => {
  const query = archiveQuery("jobs");
  const labels = Array.from(
    { length: 32 },
    (_, index) => "\u0001".repeat(127) + String.fromCharCode(32 + index),
  );
  const record = archiveRecord();
  const page = {
    ...archivePage(query),
    records: [{ ...record, jobCount: 50, header: { ...record.header, providerJobTotal: 50 } }],
    jobs: Array.from({ length: 50 }, (_, index) => ({
      ...archiveJob(),
      providerJobId: 301 + index,
      labels,
    })),
  };
  const raw = JSON.stringify(page);
  expect(new TextEncoder().encode(raw).length).toBeGreaterThan(1024 * 1024);
  expect(new TextEncoder().encode(raw).length).toBeLessThan(MAX_ARCHIVE_RESPONSE_BYTES);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(raw, { headers: { "content-type": "application/json" } })),
  );
  expect((await fetchArchivePage(query, null)).kind).toBe("ready");
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(raw + " ".repeat(MAX_ARCHIVE_RESPONSE_BYTES), {
          headers: { "content-type": "application/json" },
        }),
    ),
  );
  expect((await fetchArchivePage(query, null)).kind).toBe("invalid-response");
});
