import { afterEach, describe, expect, test, vi } from "vitest";
import {
  fetchCiEconomicsAttemptJobs,
  fetchCiEconomicsAttempts,
} from "../src/api/ciEconomics/client";
import {
  ciEconomicsAttemptJobsSchema,
  ciEconomicsAttemptPageSchema,
} from "../src/api/ciEconomics/schema";
import {
  ciEconomicsAttemptIdentityFixture,
  ciEconomicsAttemptJobsFixture,
  ciEconomicsAttemptPageFixture,
  ciEconomicsAttemptSummaryFixture,
} from "./ciEconomicsFixture";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CI economics runtime admission", () => {
  test("admits exact attempt and job projections", () => {
    expect(ciEconomicsAttemptPageSchema.parse(ciEconomicsAttemptPageFixture())).toEqual(
      ciEconomicsAttemptPageFixture(),
    );
    expect(ciEconomicsAttemptJobsSchema.parse(ciEconomicsAttemptJobsFixture())).toEqual(
      ciEconomicsAttemptJobsFixture(),
    );
  });

  test("admits equal known job timestamps as a zero-duration interval", () => {
    const timestamp = "2026-09-04T11:59:43.000000Z";
    const admitted = ciEconomicsAttemptJobsFixture({
      jobs: [
        {
          ...firstJob(),
          timing: { completedAt: timestamp, createdAt: timestamp, startedAt: timestamp },
        },
      ],
      nextJobId: null,
      attemptWall: exactDuration(0, 1),
      queue: exactDuration(0, 1),
      runnerOccupancy: exactDuration(0, 1),
    });

    expect(ciEconomicsAttemptJobsSchema.safeParse(admitted).success).toBe(true);
  });

  test.each([
    [
      "a cursor unrelated to the final attempt",
      ciEconomicsAttemptPageFixture({
        nextCursor: `2026-09-04T12:00:00.000000Z.${"0".repeat(64)}`,
      }),
      ciEconomicsAttemptPageSchema,
    ],
    [
      "a partial runner identity",
      ciEconomicsAttemptJobsFixture({
        jobs: [
          {
            ...firstJob(),
            runner: {
              runnerGroupId: null,
              runnerGroupName: null,
              runnerId: 31,
              runnerName: null,
            },
          },
        ],
      }),
      ciEconomicsAttemptJobsSchema,
    ],
    [
      "an internally contradictory duration",
      ciEconomicsAttemptJobsFixture({
        queue: {
          knownJobCount: 1,
          knownValueMs: null,
          quality: "exact",
          reasonCode: null,
          totalJobCount: 2,
        },
      }),
      ciEconomicsAttemptJobsSchema,
    ],
    [
      "non-canonical job ordering",
      ciEconomicsAttemptJobsFixture({ jobs: [...ciEconomicsAttemptJobsFixture().jobs].reverse() }),
      ciEconomicsAttemptJobsSchema,
    ],
    [
      "a partial conflict classification",
      ciEconomicsAttemptJobsFixture({
        queue: {
          knownJobCount: 0,
          knownValueMs: null,
          quality: "conflict",
          reasonCode: "conflicting_workflow_job_evidence",
          totalJobCount: 2,
        },
      }),
      ciEconomicsAttemptJobsSchema,
    ],
    [
      "a provider conclusion outside the closed GitHub terminal algebra",
      {
        ...ciEconomicsAttemptJobsFixture(),
        jobs: [{ ...firstJob(), conclusion: "unknown" }],
      },
      ciEconomicsAttemptJobsSchema,
    ],
  ])("rejects %s", (_label, value, schema) => {
    expect(schema.safeParse(value).success).toBe(false);
  });
});

describe("CI economics generated-operation transport", () => {
  test("binds same-origin attempt reads to the exact repository scope", async () => {
    const requests: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        requests.push(request);
        return Response.json(ciEconomicsAttemptPageFixture());
      }),
    );

    await expect(
      fetchCiEconomicsAttempts({ installationId: 1, repositoryId: 1 }, null),
    ).resolves.toMatchObject({ kind: "ready" });

    expect(requests).toHaveLength(1);
    const request = onlyRequest(requests);
    expect(request.credentials).toBe("same-origin");
    expect(new URL(request.url).pathname).toBe("/api/v1/economics/repositories/1/1/attempts");
    expect(new URL(request.url).searchParams.get("limit")).toBe("50");
  });

  test("rejects a response from another repository scope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          ciEconomicsAttemptPageFixture({
            items: [
              ciEconomicsAttemptSummaryFixture({
                attempt: ciEconomicsAttemptIdentityFixture({ repositoryId: 2 }),
              }),
            ],
          }),
        ),
      ),
    );

    await expect(
      fetchCiEconomicsAttempts({ installationId: 1, repositoryId: 1 }, null),
    ).resolves.toEqual({ kind: "invalid-response" });
  });

  test("binds job reads and responses to the full attempt identity", async () => {
    const requests: Request[] = [];
    const summary = ciEconomicsAttemptSummaryFixture();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        requests.push(request);
        return Response.json(ciEconomicsAttemptJobsFixture());
      }),
    );

    await expect(fetchCiEconomicsAttemptJobs(summary, null)).resolves.toMatchObject({
      kind: "ready",
    });

    const request = onlyRequest(requests);
    const url = new URL(request.url);
    expect(url.pathname).toBe("/api/v1/economics/repositories/1/1/attempts/4201/2/jobs");
    expect(url.searchParams.get("headSha")).toBe(summary.attempt.headSha);
    expect(request.credentials).toBe("same-origin");
  });

  test("rejects job evidence for a different attempt identity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          ciEconomicsAttemptJobsFixture({
            attempt: ciEconomicsAttemptIdentityFixture({ headSha: "9".repeat(40) }),
          }),
        ),
      ),
    );

    await expect(
      fetchCiEconomicsAttemptJobs(ciEconomicsAttemptSummaryFixture(), null),
    ).resolves.toEqual({ kind: "invalid-response" });
  });

  test("rejects job evidence for a different retained snapshot of the same attempt", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(ciEconomicsAttemptJobsFixture({ snapshotDigest: "9".repeat(64) })),
      ),
    );

    await expect(
      fetchCiEconomicsAttemptJobs(ciEconomicsAttemptSummaryFixture(), null),
    ).resolves.toEqual({ kind: "invalid-response" });
  });

  test("rejects a non-calendar cursor before issuing a request", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    await expect(
      fetchCiEconomicsAttempts(
        { installationId: 1, repositoryId: 1 },
        `2026-99-99T12:00:00.000000Z.${"0".repeat(64)}`,
      ),
    ).resolves.toEqual({ kind: "invalid-response" });
    expect(fetch).not.toHaveBeenCalled();
  });

  test("rejects mismatched status and error bodies", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: "unauthenticated", ok: false }, { status: 403 })),
    );

    await expect(
      fetchCiEconomicsAttempts({ installationId: 1, repositoryId: 1 }, null),
    ).resolves.toEqual({ kind: "invalid-response" });
  });

  test("rejects a body outside the operation media type", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify(ciEconomicsAttemptPageFixture()), {
            headers: { "content-type": "text/plain" },
            status: 200,
          }),
      ),
    );

    await expect(
      fetchCiEconomicsAttempts({ installationId: 1, repositoryId: 1 }, null),
    ).resolves.toEqual({ kind: "invalid-response" });
  });

  test("rejects a declared response larger than the operation budget", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () => new Response(null, { headers: { "content-length": String(256 * 1024 + 1) } }),
      ),
    );

    await expect(
      fetchCiEconomicsAttempts({ installationId: 1, repositoryId: 1 }, null),
    ).resolves.toEqual({ kind: "invalid-response" });
  });
});

function firstJob() {
  const job = ciEconomicsAttemptJobsFixture().jobs.at(0);
  if (!job) throw new Error("CI economics fixture must contain a job");
  return job;
}

function exactDuration(knownValueMs: number, jobCount: number) {
  return {
    knownJobCount: jobCount,
    knownValueMs,
    quality: "exact" as const,
    reasonCode: null,
    totalJobCount: jobCount,
  };
}

function onlyRequest(requests: readonly Request[]): Request {
  const request = requests.at(0);
  if (requests.length !== 1 || !request) throw new Error("expected exactly one request");
  return request;
}
