import { afterEach, describe, expect, test, vi } from "vitest";
import { fetchGovernanceComparison } from "../src/api/governanceComparison/client";
import { deriveGovernanceComparison } from "../src/api/governanceComparison/identity";
import { canonicalJsonText } from "../src/api/governanceObservation/canonicalJson";
import { governanceStateCanonicalText } from "../src/api/governanceObservation/identity";
import { governanceObservationFixture } from "./fixture";
import {
  governanceComparisonFixture,
  unbaselinedGovernanceComparisonFixture,
} from "./governanceComparisonFixture";

const scope = { installationId: 1, repositoryId: 1 } as const;

afterEach(() => vi.unstubAllGlobals());

describe("governance comparison client", () => {
  test.each([
    [governanceComparisonFixture(), "compared"],
    [unbaselinedGovernanceComparisonFixture(), "unbaselined"],
  ] as const)("admits exact %s evidence", async (body, state) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(body)),
    );

    await expect(fetchGovernanceComparison(scope)).resolves.toMatchObject({
      evidence: { state },
      kind: "ready",
    });
  });

  test("rejects a structurally valid but false changed-coordinate ledger", async () => {
    const original = governanceObservationFixture();
    const changed = {
      ...original,
      repository: { ...original.repository, defaultBranch: "main" },
    };
    const currentStateDigest = await digestState(changed);
    const observation = { ...changed, stateDigest: currentStateDigest };
    const body = governanceComparisonFixture({
      comparison: {
        addedRuleCount: 0,
        baselineStateDigest: original.stateDigest,
        changedCoordinates: ["api_version"],
        currentStateDigest,
        relation: "differs",
        removedRuleCount: 0,
      },
      observation,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(body)),
    );

    await expect(fetchGovernanceComparison(scope)).resolves.toEqual({
      kind: "invalid-response",
    });
  });

  test("rejects differs without an exact changed-coordinate witness", async () => {
    const original = governanceComparisonFixture();
    const body = {
      ...original,
      comparison: {
        ...original.comparison,
        changedCoordinates: [],
        relation: "differs",
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(body)),
    );

    await expect(fetchGovernanceComparison(scope)).resolves.toEqual({
      kind: "invalid-response",
    });
  });

  test("derives exact set deltas without semantic rule pairing", () => {
    const baseline = governanceObservationFixture();
    const replacementRule = {
      canonicalJson:
        '{"ruleset_id":42,"ruleset_source":"example-org/ci-coordinator",' +
        '"ruleset_source_type":"Repository","type":"pull_request"}',
      ruleType: "pull_request",
      rulesetId: 42,
      rulesetSource: "example-org/ci-coordinator",
      rulesetSourceType: "Repository",
    };
    const current = {
      ...baseline,
      rules: [replacementRule],
      stateDigest: "f".repeat(64),
    };

    expect(deriveGovernanceComparison(baseline, current)).toMatchObject({
      addedRuleCount: 1,
      changedCoordinates: ["rules"],
      relation: "differs",
      removedRuleCount: 1,
    });
  });

  test.each([
    [401, "unauthenticated", "unauthenticated"],
    [403, "forbidden", "forbidden"],
    [404, "not_found", "not-found"],
    [409, "stale", "stale"],
    [429, "rate_limited", "rate-limited"],
    [503, "provider_binding_mismatch", "unavailable"],
  ] as const)("maps HTTP %s %s to %s", async (status, error, kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            error,
            ok: false,
            retryAfterSeconds: error === "rate_limited" ? 30 : null,
          },
          { status },
        ),
      ),
    );

    await expect(fetchGovernanceComparison(scope)).resolves.toMatchObject({ kind });
  });

  test.each([
    "unavailable",
    "malformed_provider_response",
    "provider_binding_mismatch",
    "observation_limit_exceeded",
  ] as const)("preserves the exact %s unavailability reason", async (reason) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ error: reason, ok: false, retryAfterSeconds: null }, { status: 503 }),
      ),
    );

    await expect(fetchGovernanceComparison(scope)).resolves.toEqual({
      kind: "unavailable",
      reason,
    });
  });

  test.each([
    [200, "{"],
    [503, "{}"],
    [503, JSON.stringify({ error: "unavailable", ok: false, retryAfterSeconds: 1 })],
    [403, JSON.stringify({ error: "unauthenticated", ok: false, retryAfterSeconds: null })],
  ])("rejects malformed or inconsistent HTTP %s evidence: %s", async (status, body) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () => new Response(body, { status, headers: { "content-type": "application/json" } }),
      ),
    );

    await expect(fetchGovernanceComparison(scope)).resolves.toEqual({
      kind: "invalid-response",
    });
  });

  test.each([false, true])(
    "distinguishes transport failure from caller cancellation (%s)",
    async (cancelled) => {
      const controller = new AbortController();
      const failure = new TypeError("connection closed");
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          if (cancelled) controller.abort();
          throw failure;
        }),
      );

      const result = fetchGovernanceComparison(scope, controller.signal);
      if (cancelled) await expect(result).rejects.toBe(failure);
      else await expect(result).resolves.toEqual({ kind: "network-failure" });
    },
  );

  test("admits a valid composed response above the single-state transport budget", async () => {
    const original = governanceComparisonFixture();
    if (original.baseline === null || original.comparison === null) {
      throw new TypeError("comparison fixture must contain a baseline");
    }
    const rules = [41, 42].map(largeEscapingRule);
    const observationWithoutDigest = { ...original.observation, rules };
    const stateDigest = await digestState(observationWithoutDigest);
    const observation = { ...observationWithoutDigest, stateDigest };
    const baseline = {
      ...original.baseline,
      pointer: { ...original.baseline.pointer, stateDigest },
      state: { ...original.baseline.state, rules, stateDigest },
    };
    const body = {
      ...original,
      baseline,
      comparison: {
        ...original.comparison,
        baselineStateDigest: stateDigest,
        currentStateDigest: stateDigest,
      },
      observation,
    };
    const encoded = JSON.stringify(body);
    const byteLength = new TextEncoder().encode(encoded).byteLength;
    expect(byteLength).toBeGreaterThan(8 * 1024 * 1024);
    expect(byteLength).toBeLessThanOrEqual(32 * 1024 * 1024);
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(encoded, {
            headers: { "content-type": "application/json" },
          }),
      ),
    );

    await expect(fetchGovernanceComparison(scope)).resolves.toMatchObject({
      evidence: { state: "compared" },
      kind: "ready",
    });
  }, 20_000);
});

function largeEscapingRule(rulesetId: number) {
  const metadata = {
    ruleset_id: rulesetId,
    ruleset_source: "example-org/ci-coordinator",
    ruleset_source_type: "Repository",
    type: "required_status_checks",
  } as const;
  const empty = canonicalJsonText({ padding: "", ...metadata });
  const paddingLength = Math.floor((1_048_552 - new TextEncoder().encode(empty).byteLength) / 2);
  return {
    canonicalJson: canonicalJsonText({ padding: "\\".repeat(paddingLength), ...metadata }),
    ruleType: metadata.type,
    rulesetId,
    rulesetSource: metadata.ruleset_source,
    rulesetSourceType: metadata.ruleset_source_type,
  };
}

async function digestState(
  state: ReturnType<typeof governanceObservationFixture>,
): Promise<string> {
  const bytes = new TextEncoder().encode(governanceStateCanonicalText(state));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
