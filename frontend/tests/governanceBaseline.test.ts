import { afterEach, describe, expect, test, vi } from "vitest";
import { proxyRequestIsAdmitted } from "../src/api/development/proxyPolicy";
import {
  approveGovernanceBaseline,
  fetchGovernanceBaseline,
  type GovernanceBaselineApprovalCommand,
} from "../src/api/governanceBaseline/client";
import { governanceBaselineReasonIsAdmitted } from "../src/api/governanceBaseline/reason";
import {
  governanceBaselineApprovalFixture,
  governanceBaselineReadFixture,
  governanceBaselineRecordFixture,
} from "./governanceBaselineFixture";

const scope = { installationId: 1, repositoryId: 1 } as const;
const command: GovernanceBaselineApprovalCommand = {
  expectedActive: null,
  expectedStateDigest: "84e824c649cc36e3ec99cb36c0b37181e7b5fe6e0ed38e4b66bc5b016c821cb7",
  operationId: "00000000-0000-4000-8000-000000000000",
  reason: "Adopt repository governance",
  scope,
};

afterEach(() => vi.unstubAllGlobals());

describe("governance baseline client", () => {
  test.each([
    [governanceBaselineReadFixture(), "active"],
    [governanceBaselineReadFixture({ baseline: null, state: "absent" }), "absent"],
  ] as const)("admits an exact-scope %s read", async (body, state) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(body)),
    );

    await expect(fetchGovernanceBaseline(scope)).resolves.toMatchObject({
      kind: "ready",
      read: { state },
    });
  });

  test.each([
    "",
    " padded",
    "padded\u00a0",
    "\ufeffhidden",
    "hidden\u3000",
    "\u001ccontrol",
    "control\u0085",
    "nul\0byte",
    "\ud800",
  ])("rejects noncanonical reason %j before transport", async (reason) => {
    const transport = vi.fn();
    vi.stubGlobal("fetch", transport);

    expect(governanceBaselineReasonIsAdmitted(reason)).toBe(false);
    await expect(
      approveGovernanceBaseline({ ...command, reason }, "c".repeat(43)),
    ).resolves.toEqual({ kind: "invalid-response" });
    expect(transport).not.toHaveBeenCalled();
  });

  test.each(["\u00c4nderung genehmigt", "A\ufeffB", "exact reason"])(
    "admits bounded scalar reason %j",
    (reason) => {
      expect(governanceBaselineReasonIsAdmitted(reason)).toBe(true);
    },
  );

  test("binds an approval to same-origin credentials and the exact observation", async () => {
    const requests: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: RequestInfo | URL) => {
        requests.push(request as Request);
        return Response.json(governanceBaselineApprovalFixture(), { status: 201 });
      }),
    );

    await expect(approveGovernanceBaseline(command, "c".repeat(43))).resolves.toMatchObject({
      kind: "complete",
      approval: { state: "accepted" },
    });
    const request = requests[0];
    expect(request?.credentials).toBe("same-origin");
    expect(request?.headers.get("x-csrf-token")).toBe("c".repeat(43));
    await expect(request?.clone().json()).resolves.toEqual({
      expectedActive: null,
      expectedStateDigest: command.expectedStateDigest,
      operationId: command.operationId,
      reason: command.reason,
    });
  });

  test.each([
    [401, "unauthenticated"],
    [403, "forbidden"],
    [409, "stale"],
    [409, "baseline_conflict"],
    [409, "operation_conflict"],
    [503, "overloaded"],
    [503, "unavailable"],
  ] as const)("maps HTTP %s to exact %s failure", async (status, error) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error, ok: false }, { status })),
    );

    await expect(approveGovernanceBaseline(command, "c".repeat(43))).resolves.toEqual({
      kind: error,
    });
  });

  test.each([
    [
      "scope substitution",
      governanceBaselineReadFixture({ scope: { installationId: 1, repositoryId: 2 } }),
    ],
    [
      "state digest substitution",
      governanceBaselineReadFixture({
        baseline: governanceBaselineRecordFixture({
          state: {
            ...governanceBaselineRecordFixture().state,
            stateDigest: "f".repeat(64),
          },
          pointer: {
            ...governanceBaselineRecordFixture().pointer,
            stateDigest: "f".repeat(64),
          },
        }),
      }),
    ],
    [
      "non-adjacent predecessor",
      governanceBaselineReadFixture({
        baseline: governanceBaselineRecordFixture({
          pointer: { ...governanceBaselineRecordFixture().pointer, version: 3 },
          supersedes: {
            baselineId: `governance-baseline:${"c".repeat(64)}`,
            stateDigest: command.expectedStateDigest,
            version: 1,
          },
        }),
      }),
    ],
  ])("rejects %s in a retained read", async (_label, body) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(body)),
    );

    await expect(fetchGovernanceBaseline(scope)).resolves.toEqual({ kind: "invalid-response" });
  });

  test.each([
    ["success status", "duplicate", 201],
    ["duplicate operation identity", "duplicate", 200],
  ] as const)("rejects a success that contradicts its %s", async (_case, state, status) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          governanceBaselineApprovalFixture({
            baseline: governanceBaselineRecordFixture({
              operationId: "11111111-1111-4111-8111-111111111111",
            }),
            state,
          }),
          { status },
        ),
      ),
    );

    await expect(approveGovernanceBaseline(command, "c".repeat(43))).resolves.toEqual({
      kind: "invalid-response",
    });
  });
});

describe("governance baseline development proxy", () => {
  const path = "/api/v1/workbench/repositories/1/1/governance-baselines";

  test.each([
    ["GET", path, true],
    ["POST", path, true],
    ["DELETE", path, false],
    ["GET", `${path}?unknown=1`, false],
  ] as const)("admits only the exact browser baseline route", (method, url, expected) => {
    expect(proxyRequestIsAdmitted(method, url)).toBe(expected);
  });
});
