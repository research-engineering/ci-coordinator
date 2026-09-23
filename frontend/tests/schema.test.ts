import { describe, expect, test } from "vitest";
import workbenchContract from "../openapi/workbench.openapi.json";
import { canonicalJsonText } from "../src/api/governanceObservation/canonicalJson";
import {
  governanceObservationErrorSchema,
  governanceObservationSchema,
} from "../src/api/governanceObservation/schema";
import {
  installationCatalogSchema,
  providerInventoryErrorSchema,
  repositoryPageSchema,
} from "../src/api/providerInventory/schema";
import { MAX_WORKBENCH_SECTION_ITEMS } from "../src/api/workbench/limits";
import { workbenchSnapshotSchema } from "../src/api/workbench/schema";
import { workflowDiscoverySchema } from "../src/api/workflowDiscovery/schema";
import {
  governanceObservationFixture,
  installationCatalogFixture,
  installationFixture,
  repositoryFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "./fixture";

describe("workbench response admission", () => {
  test("admits a bounded snapshot", () => {
    expect(workbenchSnapshotSchema.parse(workbenchFixture()).ledgerRevision).toBe(42);
  });

  test("admits every bounded workbench projection variant", () => {
    const snapshot = workbenchFixture({
      auditEvents: [auditEventWithPayload({ nested: [null, true, "value", 7] })],
      configEpochs: [configEpoch({ active: true, activeRevision: 1 })],
      overrides: [
        {
          active: true,
          actor: "operator",
          appliedAt: "2026-07-18T08:00:00Z",
          auditEventId: "audit-event",
          expiresAt: "2026-07-18T09:00:00Z",
          kind: "force_full_ci",
          operationId: "operation",
          overrideId: "override",
          reason: "incident containment",
          subjectId: "subject",
        },
      ],
      plans: [
        {
          baseSha: "a".repeat(40),
          catalogHash: null,
          eventName: "pull_request",
          executionMode: "selected",
          expiresAt: "2026-07-18T09:00:00Z",
          fallbackReason: null,
          headSha: "b".repeat(40),
          issuedAt: "2026-07-18T08:00:00Z",
          omittedObligationIds: [],
          planId: "plan",
          productionAdmissionReceiptId: null,
          profiles: [
            {
              capacityMode: "optimized",
              capacityReason: null,
              executionKind: "witness-shards",
              maxParallel: 2,
              profileId: "python",
              shardIds: ["0", "1"],
              testCount: 4,
              witnessCount: 2,
            },
            {
              executionKind: "native-job-set",
              jobId: "lint",
              profileId: "frontend",
              witnessCount: 1,
            },
          ],
          recordId: "record",
          ref: "refs/pull/1/head",
          requestId: "request",
          runAttempt: 1,
          selectedObligationIds: ["obligation"],
          selectedWitnessIds: ["witness"],
          targetRegistryHash: null,
          testManifestId: null,
          verifiedPlanId: null,
          workflowRunId: 1,
        },
      ],
      runs: [
        {
          attemptCount: 1,
          baseSha: "a".repeat(40),
          claimGeneration: 1,
          contractHash: "contract",
          createdAt: "2026-07-18T08:00:00Z",
          deadlineAt: "2026-07-18T09:00:00Z",
          eventName: "pull_request",
          findings: [{ kind: "warning", message: "bounded", signalId: null }],
          headSha: "b".repeat(40),
          leaseActive: false,
          leaseExpiresAt: null,
          maxAttempts: 3,
          nextAttemptAt: "2026-07-18T08:05:00Z",
          ref: "refs/pull/1/head",
          revision: 1,
          runAttempt: 1,
          state: "success",
          subjectId: "subject",
          workflowRunId: 1,
        },
      ],
    });

    expect(workbenchSnapshotSchema.safeParse(snapshot).success).toBe(true);
  });

  test.each([
    ["unknown field", { ...workbenchFixture(), unexpected: true }],
    ["invalid timestamp", workbenchFixture({ observedAt: "today" })],
    ["unsafe revision", workbenchFixture({ ledgerRevision: Number.MAX_VALUE })],
    [
      "oversized section",
      workbenchFixture({ auditEvents: Array.from({ length: 101 }, () => ({})) as never }),
    ],
  ])("rejects %s", (_label, value) => {
    expect(workbenchSnapshotSchema.safeParse(value).success).toBe(false);
  });

  test("rejects a payload beyond the admitted depth", () => {
    let payload: unknown = null;
    for (let depth = 0; depth < 34; depth += 1) payload = [payload];
    const value = workbenchFixture({
      auditEvents: [
        {
          actor: "operator",
          auditEventId: "event",
          createdAt: "2026-07-18T08:00:00Z",
          eventHash: "hash",
          eventType: "test",
          payload: payload as never,
          payloadHash: "hash",
          previousEventHash: null,
          sequence: 1,
          subjectId: "subject",
          subjectType: "test",
        },
      ],
    });

    expect(workbenchSnapshotSchema.safeParse(value).success).toBe(false);
  });

  test.each([
    ["oversized string", "x".repeat(65_537)],
    ["non-finite number", Number.POSITIVE_INFINITY],
    ["unsafe integer", Number.MAX_SAFE_INTEGER + 1],
    ["unsupported scalar", undefined],
    ["oversized array", Array.from({ length: 1_001 }, () => null)],
    [
      "oversized object",
      Object.fromEntries(Array.from({ length: 1_001 }, (_, index) => [`key-${index}`, null])),
    ],
    ["oversized key", { ["k".repeat(513)]: null }],
    ["exhausted node budget", Array.from({ length: 1_000 }, () => Array(10).fill(null))],
  ])("rejects bounded JSON with %s", (_label, payload) => {
    expect(
      workbenchSnapshotSchema.safeParse(
        workbenchFixture({ auditEvents: [auditEventWithPayload(payload)] }),
      ).success,
    ).toBe(false);
  });

  test.each([
    [
      "multiple active epochs",
      [
        configEpoch({ active: true, activeRevision: 1 }),
        configEpoch({ active: true, activeRevision: 2 }),
      ],
    ],
    ["active epoch without revision", [configEpoch({ active: true, activeRevision: null })]],
    ["inactive epoch with revision", [configEpoch({ active: false, activeRevision: 1 })]],
  ])("rejects %s", (_label, configEpochs) => {
    expect(workbenchSnapshotSchema.safeParse(workbenchFixture({ configEpochs })).success).toBe(
      false,
    );
  });

  test("keeps the response bound aligned with the request contract", () => {
    const contract = workbenchContract as WorkbenchContract;
    const parameters =
      contract.paths["/api/v1/workbench/repositories/{installation_id}/{repository_id}"].get
        .parameters;
    const limit = parameters.find((parameter) => parameter.name === "limit");

    expect(limit?.schema.maximum).toBe(MAX_WORKBENCH_SECTION_ITEMS);
  });
});

function auditEventWithPayload(payload: unknown) {
  return {
    actor: "operator",
    auditEventId: "event",
    createdAt: "2026-07-18T08:00:00Z",
    eventHash: "hash",
    eventType: "test",
    payload: payload as never,
    payloadHash: "hash",
    previousEventHash: null,
    sequence: 1,
    subjectId: "subject",
    subjectType: "test",
  };
}

function configEpoch(overrides: { active: boolean; activeRevision: number | null }) {
  return {
    active: overrides.active,
    activeRevision: overrides.activeRevision,
    compiledSchemaId: "compiled-schema",
    documentHash: "document-hash",
    documentProfileId: "document-profile",
    documentSchemaId: "document-schema",
    epochHash: "epoch-hash",
    epochId: `epoch-${String(overrides.activeRevision)}`,
    semanticProfileId: "semantic-profile",
    sourceFormat: "json" as const,
    sourceHash: "source-hash",
  };
}

describe("provider inventory response admission", () => {
  test("admits bounded catalog and repository page projections", () => {
    expect(installationCatalogSchema.parse(installationCatalogFixture()).complete).toBe(true);
    expect(repositoryPageSchema.parse(repositoryPageFixture()).totalCount).toBe(1);
  });

  test.each([
    [
      "false completeness without failures",
      installationCatalogFixture({ complete: false }),
      installationCatalogSchema,
    ],
    [
      "duplicate installation identity",
      installationCatalogFixture({
        failures: [{ installationId: 1, reason: "unavailable", retryAfterSeconds: null }],
        complete: false,
      }),
      installationCatalogSchema,
    ],
    [
      "aggregate installation catalog overflow",
      installationCatalogFixture({
        complete: false,
        failures: [{ installationId: 65, reason: "unavailable", retryAfterSeconds: null }],
        installations: Array.from({ length: 64 }, (_, index) =>
          installationFixture({ accountId: index + 101, installationId: index + 1 }),
        ),
      }),
      installationCatalogSchema,
    ],
    [
      "cross-installation repository",
      repositoryPageFixture({
        repositories: [
          {
            ...repositoryFixture(),
            scope: { installationId: 2, repositoryId: 1 },
          },
        ],
      }),
      repositoryPageSchema,
    ],
    [
      "cross-account repository",
      repositoryPageFixture({
        repositories: [repositoryFixture({ ownerId: 999 })],
      }),
      repositoryPageSchema,
    ],
    [
      "inactive repository page",
      repositoryPageFixture({
        installation: installationFixture({ state: "suspended" }),
      }),
      repositoryPageSchema,
    ],
    [
      "non-canonical installation order",
      installationCatalogFixture({
        installations: [
          installationFixture({ accountId: 102, installationId: 2 }),
          installationFixture(),
        ],
      }),
      installationCatalogSchema,
    ],
    [
      "retry delay without rate limit",
      installationCatalogFixture({
        complete: false,
        failures: [{ installationId: 2, reason: "unavailable", retryAfterSeconds: 30 }],
      }),
      installationCatalogSchema,
    ],
    [
      "page larger than its declared page size",
      repositoryPageFixture({
        perPage: 1,
        repositories: [
          repositoryFixture(),
          repositoryFixture({ scope: { installationId: 1, repositoryId: 2 } }),
        ],
        totalCount: 2,
      }),
      repositoryPageSchema,
    ],
    [
      "next page beyond the request range",
      repositoryPageFixture({ hasNextPage: true, page: 10_000, totalCount: 1_000_001 }),
      repositoryPageSchema,
    ],
    [
      "provider text beyond its UTF-8 byte bound",
      repositoryPageFixture({
        repositories: [
          repositoryFixture({ defaultBranch: String.fromCodePoint(0xe9).repeat(600) }),
        ],
      }),
      repositoryPageSchema,
    ],
    [
      "unpaired provider surrogate",
      repositoryPageFixture({
        repositories: [repositoryFixture({ defaultBranch: String.fromCharCode(0xd800) })],
      }),
      repositoryPageSchema,
    ],
    ["contradictory terminal page", repositoryPageFixture({ totalCount: 2 }), repositoryPageSchema],
  ])("rejects %s", (_label, value, schema) => {
    expect(schema.safeParse(value).success).toBe(false);
  });

  test("admits provider text across the former generic identifier limit", () => {
    expect(
      repositoryPageSchema.safeParse(
        repositoryPageFixture({
          repositories: [repositoryFixture({ defaultBranch: "a".repeat(600) })],
        }),
      ).success,
    ).toBe(true);
  });

  test("rejects retry metadata on a non-rate-limited error", () => {
    expect(
      providerInventoryErrorSchema.safeParse({
        error: "unavailable",
        ok: false,
        retryAfterSeconds: 30,
      }).success,
    ).toBe(false);
  });
});

describe("governance observation response admission", () => {
  test("admits a canonical unbaselined traversal observation", () => {
    const observation = governanceObservationSchema.parse(governanceObservationFixture());

    expect(observation.rules).toHaveLength(1);
    expect(observation.baselineState).toBe("unbaselined");
  });

  test.each([
    [
      "inconsistent repository name",
      governanceObservationFixture({
        repository: {
          ...governanceObservationFixture().repository,
          fullName: "example-org/other",
        },
      }),
    ],
    [
      "unsupported provider API version",
      { ...governanceObservationFixture(), apiVersion: "2022-11-28" },
    ],
    [
      "non-canonical rule JSON",
      governanceObservationFixture({
        rules: [
          {
            ...governanceRuleFixture(),
            canonicalJson:
              '{"type":"required_status_checks","ruleset_source_type":"Repository",' +
              '"ruleset_source":"example-org/ci-coordinator","ruleset_id":41,' +
              '"parameters":{"required_status_checks":["Full CI"]}}',
          },
        ],
      }),
    ],
    [
      "contradictory rule metadata",
      governanceObservationFixture({
        rules: [
          {
            ...governanceRuleFixture(),
            ruleType: "pull_request",
          },
        ],
      }),
    ],
    [
      "duplicate rule bytes",
      governanceObservationFixture({
        rules: [governanceRuleFixture(), governanceRuleFixture()],
      }),
    ],
    [
      "rule beyond the admitted JSON depth",
      governanceObservationFixture({
        rules: [governanceRuleFixture(deepGovernanceRuleJson())],
      }),
    ],
    [
      "unsafe JSON number",
      governanceObservationFixture({
        rules: [governanceRuleFixture(ruleJsonWithParameters("9007199254740992"))],
      }),
    ],
  ])("rejects %s", (_label, value) => {
    expect(governanceObservationSchema.safeParse(value).success).toBe(false);
  });

  test.each([
    "/main",
    "main/",
    "../main",
    "feature//main",
    ".hidden",
    "main.lock",
    "main..next",
    "main@{1}",
    "main\nnext",
    "main~1",
    "main\\next",
  ])("rejects non-canonical default branch %j", (defaultBranch) => {
    const observation = governanceObservationFixture();
    expect(
      governanceObservationSchema.safeParse({
        ...observation,
        repository: { ...observation.repository, defaultBranch },
      }).success,
    ).toBe(false);
  });

  test.each([
    ["single at-sign branch", "@", true],
    ["ASCII branch at the byte bound", "a".repeat(512), true],
    ["ASCII branch beyond the byte bound", "a".repeat(513), false],
    ["Unicode branch at the byte bound", "\u00e9".repeat(256), true],
    ["Unicode branch beyond the byte bound", "\u00e9".repeat(257), false],
  ])("%s", (_label, defaultBranch, admitted) => {
    const observation = governanceObservationFixture();
    expect(
      governanceObservationSchema.safeParse({
        ...observation,
        repository: { ...observation.repository, defaultBranch },
      }).success,
    ).toBe(admitted);
  });

  test.each([
    ["rule count at the bound", governanceRules(1_000), true],
    ["rule count beyond the bound", governanceRules(1_001), false],
    ["one rule at its byte bound", [governanceRuleWithBytes(1_048_576, 1)], true],
    ["one rule beyond its byte bound", [governanceRuleWithBytes(1_048_577, 1)], false],
    [
      "aggregate rules at the byte bound",
      sortedGovernanceRules([
        governanceRuleWithBytes(1_048_576, 1),
        governanceRuleWithBytes(1_048_576, 2),
      ]),
      true,
    ],
    [
      "aggregate rules beyond the byte bound",
      sortedGovernanceRules([
        governanceRuleWithBytes(1_048_576, 1),
        governanceRuleWithBytes(1_048_576, 2),
        governanceRuleWithBytes(256, 3),
      ]),
      false,
    ],
  ])("%s", (_label, rules, admitted) => {
    expect(
      governanceObservationSchema.safeParse(
        governanceObservationFixture({
          rules,
        }),
      ).success,
    ).toBe(admitted);
  });

  test("uses the backend canonical number and Unicode scalar ordering", () => {
    expect(canonicalJsonText({ "\ue000": 1, "\u{10000}": 2, n: 0.000001 })).toBe(
      '{"n":0.000001,"\ue000":1,"\u{10000}":2}',
    );
  });

  test("rejects retry metadata on a non-rate-limited governance failure", () => {
    expect(
      governanceObservationErrorSchema.safeParse({
        error: "unavailable",
        ok: false,
        retryAfterSeconds: 30,
      }).success,
    ).toBe(false);
  });
});

function governanceRuleFixture(canonicalJson?: string) {
  const rule = governanceObservationFixture().rules[0];
  if (!rule) throw new Error("governance fixture requires one rule");
  return canonicalJson === undefined ? rule : { ...rule, canonicalJson };
}

function governanceRules(count: number) {
  return sortedGovernanceRules(
    Array.from({ length: count }, (_, index) => governanceRuleWithBytes(256, index + 1)),
  );
}

function governanceRuleWithBytes(targetBytes: number, rulesetId: number) {
  const base = {
    parameters: { payload: "" },
    ruleset_id: rulesetId,
    ruleset_source: "example-org/ci-coordinator",
    ruleset_source_type: "Repository",
    type: "required_status_checks",
  };
  const baseJson = canonicalJsonText(base);
  const payloadBytes = targetBytes - new TextEncoder().encode(baseJson).byteLength;
  if (payloadBytes < 0) throw new RangeError("target rule byte count is below its metadata");
  const canonicalJson = canonicalJsonText({
    ...base,
    parameters: { payload: "x".repeat(payloadBytes) },
  });
  if (new TextEncoder().encode(canonicalJson).byteLength !== targetBytes) {
    throw new Error("governance rule fixture failed to reach its target byte count");
  }
  return {
    canonicalJson,
    rulesetId,
    rulesetSource: base.ruleset_source,
    rulesetSourceType: base.ruleset_source_type,
    ruleType: base.type,
  };
}

function sortedGovernanceRules<T extends { readonly canonicalJson: string }>(
  rules: readonly T[],
): T[] {
  return [...rules].sort((left, right) =>
    left.canonicalJson < right.canonicalJson
      ? -1
      : left.canonicalJson > right.canonicalJson
        ? 1
        : 0,
  );
}

function ruleJsonWithParameters(parameters: string): string {
  return (
    `{"parameters":${parameters},"ruleset_id":41,` +
    '"ruleset_source":"example-org/ci-coordinator",' +
    '"ruleset_source_type":"Repository","type":"required_status_checks"}'
  );
}

function deepGovernanceRuleJson(): string {
  let parameters: unknown = null;
  for (let depth = 0; depth < 17; depth += 1) parameters = { a: parameters };
  return canonicalJsonText({
    parameters,
    ruleset_id: 41,
    ruleset_source: "example-org/ci-coordinator",
    ruleset_source_type: "Repository",
    type: "required_status_checks",
  });
}

describe("workflow discovery response admission", () => {
  test("admits an exact, provenance-bound discovery report", () => {
    const report = workflowDiscoverySchema.parse(workflowDiscoveryFixture());

    expect(report.adoptionAssessments[0]?.state).toBe("full_only");
    expect(report.proposal.state).toBe("reviewable");
    expect(report.revision).toHaveLength(40);
    expect(report.targetProjection.status).toBe("absent");
  });

  test("admits a proven aggregate terminal signal", () => {
    const report = workflowDiscoverySchema.parse(reportWithAggregateSignal());

    expect(report.proposal.selectedJobId).toBe("test");
  });

  test("admits a closed non-authoritative witness-shard assessment", () => {
    const value = workflowDiscoveryFixture();
    const assessment = value.adoptionAssessments[0];
    if (!assessment) throw new Error("workflow discovery fixture requires one assessment");

    const report = workflowDiscoverySchema.parse({
      ...value,
      adoptionAssessments: [
        {
          ...assessment,
          blockers: [],
          recommendedAdapter: "none",
          requiredOwnerInputs: [],
          state: "witness_shards",
        },
      ],
    });

    expect(report.adoptionAssessments[0]?.state).toBe("witness_shards");
  });

  test("admits derived evidence at the originating YAML predicate", () => {
    const value = workflowDiscoveryFixture();
    const fact = value.facts[0];
    if (!fact) throw new Error("workflow discovery fixture requires one fact");

    const report = workflowDiscoverySchema.parse({
      ...value,
      facts: value.facts.map((entry, index) =>
        index === 0
          ? {
              ...fact,
              field: "call.target",
              provenance: {
                ...fact.provenance,
                location: { ...fact.provenance.location, path: "job.uses" },
              },
            }
          : entry,
      ),
    });

    expect(report.facts[0]?.provenance.location.path).toBe("job.uses");
  });

  test("admits a call graph closed over its job uses predicates", () => {
    const report = workflowDiscoverySchema.parse(reportWithRemoteCall());

    expect(report.callEdges[0]?.kind).toBe("remote");
    expect(report.localGraphClosed).toBe(true);
  });

  test.each([
    [
      "adoption assessment for another inventory",
      () => {
        const value = workflowDiscoveryFixture();
        const assessment = value.adoptionAssessments[0];
        if (!assessment) throw new Error("workflow discovery fixture requires one assessment");
        return {
          ...value,
          adoptionAssessments: [{ ...assessment, inventoryDigest: "d".repeat(64) }],
        };
      },
    ],
    [
      "adoption assessment omitted for a source",
      () => ({ ...workflowDiscoveryFixture(), adoptionAssessments: [] }),
    ],
    [
      "available target projection without an identity",
      () => ({
        ...workflowDiscoveryFixture(),
        targetProjection: { registryHash: null, status: "available" },
      }),
    ],
    [
      "absent target projection carrying authority",
      () => ({
        ...workflowDiscoveryFixture(),
        targetProjection: { registryHash: "d".repeat(64), status: "absent" },
      }),
    ],
    [
      "contradictory selectable adoption assessment",
      () => {
        const value = workflowDiscoveryFixture();
        const assessment = value.adoptionAssessments[0];
        if (!assessment) throw new Error("workflow discovery fixture requires one assessment");
        return {
          ...value,
          adoptionAssessments: [
            {
              ...assessment,
              state: "in_place_job_set",
            },
          ],
        };
      },
    ],
    [
      "contradictory witness-shard assessment",
      () => {
        const value = workflowDiscoveryFixture();
        const assessment = value.adoptionAssessments[0];
        if (!assessment) throw new Error("workflow discovery fixture requires one assessment");
        return {
          ...value,
          adoptionAssessments: [
            {
              ...assessment,
              recommendedAdapter: "none",
              state: "witness_shards",
            },
          ],
        };
      },
    ],
    [
      "proposal for another inventory",
      () => {
        const value = workflowDiscoveryFixture();
        return { ...value, proposal: { ...value.proposal, inventoryDigest: "d".repeat(64) } };
      },
    ],
    [
      "cross-revision evidence",
      () => {
        const value = workflowDiscoveryFixture();
        const fact = value.facts[0];
        if (!fact) throw new Error("workflow discovery fixture requires one fact");
        return {
          ...value,
          facts: [
            {
              ...fact,
              provenance: { ...fact.provenance, revision: "e".repeat(40) },
            },
          ],
        };
      },
    ],
    [
      "incomplete proposal unknown ledger",
      () => {
        const value = workflowDiscoveryFixture();
        return { ...value, proposal: { ...value.proposal, unknownIds: [] } };
      },
    ],
    [
      "call edge without its caller job",
      () => {
        const value = reportWithRemoteCall();
        const edge = value.callEdges[0];
        if (!edge) throw new Error("remote-call fixture requires one edge");
        return { ...value, callEdges: [{ ...edge, callerJobId: "missing" }] };
      },
    ],
    [
      "local graph flag that contradicts its edges",
      () => ({ ...reportWithRemoteCall(), localGraphClosed: false }),
    ],
    [
      "reviewable proposal with blockers",
      () => {
        const value = workflowDiscoveryFixture();
        return { ...value, proposal: { ...value.proposal, blockers: ["ambiguous_signal"] } };
      },
    ],
    [
      "reviewable proposal for another provider signal",
      () => {
        const value = workflowDiscoveryFixture();
        return {
          ...value,
          proposal: { ...value.proposal, selectedJobName: "Another CI" },
        };
      },
    ],
    [
      "reviewable proposal for another event set",
      () => {
        const value = workflowDiscoveryFixture();
        return {
          ...value,
          proposal: { ...value.proposal, selectedEvents: ["pull_request"] },
        };
      },
    ],
    [
      "reviewable proposal from an unknown generator",
      () => {
        const value = workflowDiscoveryFixture();
        return {
          ...value,
          proposal: { ...value.proposal, generatorVersion: "workflow-discovery-proposal/v3" },
        };
      },
    ],
    [
      "reviewable proposal with an impossible epoch identity",
      () => {
        const value = workflowDiscoveryFixture();
        return {
          ...value,
          proposal: { ...value.proposal, admittedEpochId: `epoch:${"4".repeat(32)}` },
        };
      },
    ],
    [
      "reviewable proposal with an open-ended non-claim catalog",
      () => {
        const value = workflowDiscoveryFixture();
        return { ...value, proposal: { ...value.proposal, nonClaims: ["not active"] } };
      },
    ],
    [
      "reviewable proposal from an incomplete inventory",
      () => ({ ...workflowDiscoveryFixture(), complete: false }),
    ],
    [
      "reviewable proposal without proven default-branch event coverage",
      () => {
        const value = workflowDiscoveryFixture();
        return {
          ...value,
          facts: value.facts.filter((entry) => entry.field !== "workflow.default_branch_ci_events"),
        };
      },
    ],
    [
      "reviewable proposal policy for another repository",
      () => {
        const value = workflowDiscoveryFixture();
        return {
          ...value,
          proposal: {
            ...value.proposal,
            policySource: value.proposal.policySource?.replace(
              '"repositoryId":1',
              '"repositoryId":2',
            ),
          },
        };
      },
    ],
    ["reviewable proposal with an open local call graph", reportWithOpenLocalGraph],
    ["reviewable proposal with a duplicate provider signal", reportWithSignalCollision],
    ["aggregate signal with a disconnected job", () => breakAggregateTopology(false)],
    ["aggregate signal with a dependency cycle", () => breakAggregateTopology(true)],
    [
      "complete report without its parsed workflow",
      () => ({ ...workflowDiscoveryFixture(), workflows: [] }),
    ],
    [
      "non-canonical evidence",
      () => {
        const value = workflowDiscoveryFixture();
        return { ...value, facts: [value.facts[0], value.facts[0]] };
      },
    ],
  ])("rejects %s", (_label, candidate) => {
    expect(workflowDiscoverySchema.safeParse(candidate()).success).toBe(false);
  });
});

function reportWithRemoteCall() {
  const value = workflowDiscoveryFixture();
  const workflow = value.workflows[0];
  const job = workflow?.jobs[0];
  const provenance = value.facts[0]?.provenance;
  if (!workflow || !job || !provenance) {
    throw new Error("workflow discovery fixture requires one workflow, job, and provenance");
  }
  const uses = "example/shared/.github/workflows/python.yml@main";
  return {
    ...value,
    callEdges: [
      {
        callerJobId: job.jobId,
        callerWorkflowPath: workflow.path,
        edgeId: `edge:${"7".repeat(32)}`,
        kind: "remote" as const,
        provenance: {
          ...provenance,
          location: { column: 1, line: 1, path: "job.uses" },
          workflowPath: workflow.path,
        },
        remoteRef: "main",
        remoteRefImmutable: false,
        status: "remote" as const,
        targetPath: null,
        uses,
      },
    ],
    proposal: {
      ...value.proposal,
      admittedEpochId: null,
      blockers: ["stable_provider_signal_absent"],
      policySource: null,
      selectedEvents: [],
      selectedJobName: null,
      state: "blocked" as const,
    },
    workflows: [
      {
        ...workflow,
        jobs: [{ ...job, uses }],
      },
    ],
  };
}

function reportWithOpenLocalGraph() {
  const value = workflowDiscoveryFixture();
  const primary = value.workflows[0];
  const templateJob = primary?.jobs[0];
  const provenance = value.facts[0]?.provenance;
  if (!primary || !templateJob || !provenance) {
    throw new Error("workflow discovery fixture requires one workflow, job, and provenance");
  }
  const path = ".github/workflows/reusable.yml";
  const targetPath = ".github/workflows/missing.yml";
  const uses = `./${targetPath}`;
  const blobSha = "d".repeat(40);
  const workflowSubjectId = `workflow:${"d".repeat(32)}`;
  const jobSubjectId = `job:${"e".repeat(32)}`;
  const secondaryProvenance = {
    ...provenance,
    blobSha,
    location: { ...provenance.location, path: "job.uses" },
    workflowPath: path,
  };
  return {
    ...value,
    callEdges: [
      {
        callerJobId: "call",
        callerWorkflowPath: path,
        edgeId: `edge:${"d".repeat(32)}`,
        kind: "local" as const,
        provenance: secondaryProvenance,
        remoteRef: null,
        remoteRefImmutable: null,
        status: "missing" as const,
        targetPath,
        uses,
      },
    ],
    facts: [
      ...value.facts,
      {
        category: "invocation" as const,
        criticality: "informational" as const,
        factId: `fact:${"d".repeat(32)}`,
        field: "workflow.triggers",
        provenance: {
          ...secondaryProvenance,
          location: { ...provenance.location, path: "workflow.on" },
        },
        subjectId: workflowSubjectId,
        value: ["workflow_call"],
      },
    ],
    localGraphClosed: false,
    sources: [...value.sources, { blobSha, path, size: 128 }],
    workflows: [
      primary,
      {
        ...primary,
        jobs: [
          {
            ...templateJob,
            jobId: "call",
            name: "Call",
            providerSignalName: null,
            runsOn: null,
            steps: null,
            subjectId: jobSubjectId,
            uses,
          },
        ],
        name: "Reusable",
        path,
        subjectId: workflowSubjectId,
        triggers: ["workflow_call"],
      },
    ],
  };
}

function reportWithSignalCollision() {
  const value = workflowDiscoveryFixture();
  const primary = value.workflows[0];
  const templateJob = primary?.jobs[0];
  const provenance = value.facts[0]?.provenance;
  if (!primary || !templateJob || !provenance) {
    throw new Error("workflow discovery fixture requires one workflow, job, and provenance");
  }
  const path = ".github/workflows/secondary.yml";
  const blobSha = "d".repeat(40);
  const workflowSubjectId = `workflow:${"d".repeat(32)}`;
  const jobSubjectId = `job:${"e".repeat(32)}`;
  const secondaryProvenance = { ...provenance, blobSha, workflowPath: path };
  return {
    ...value,
    facts: [
      ...value.facts,
      {
        category: "invocation" as const,
        criticality: "informational" as const,
        factId: `fact:${"a".repeat(32)}`,
        field: "workflow.triggers",
        provenance: {
          ...secondaryProvenance,
          location: { ...provenance.location, path: "workflow.on" },
        },
        subjectId: workflowSubjectId,
        value: ["workflow_dispatch"],
      },
      {
        category: "identity" as const,
        criticality: "informational" as const,
        factId: `fact:${"b".repeat(32)}`,
        field: "job.name",
        provenance: {
          ...secondaryProvenance,
          location: { ...provenance.location, path: "job.name" },
        },
        subjectId: jobSubjectId,
        value: "Full CI",
      },
    ],
    sources: [...value.sources, { blobSha, path, size: 128 }],
    workflows: [
      primary,
      {
        ...primary,
        jobs: [{ ...templateJob, subjectId: jobSubjectId }],
        name: "Secondary CI",
        path,
        subjectId: workflowSubjectId,
        triggers: ["workflow_dispatch"],
      },
    ],
  };
}

function reportWithAggregateSignal() {
  const value = workflowDiscoveryFixture();
  const workflow = value.workflows[0];
  const terminal = workflow?.jobs[0];
  const provenance = value.facts[0]?.provenance;
  if (!workflow || !terminal || !provenance) {
    throw new Error("workflow discovery fixture requires one workflow, job, and provenance");
  }
  const lintSubjectId = `job:${"8".repeat(32)}`;
  return {
    ...value,
    facts: [
      ...value.facts.map((entry) => {
        if (entry.subjectId !== terminal.subjectId) return entry;
        if (entry.field === "job.condition.syntax") return { ...entry, value: "always()" };
        if (entry.field === "job.needs") return { ...entry, value: ["lint"] };
        return entry;
      }),
      {
        category: "invocation" as const,
        criticality: "informational" as const,
        factId: `fact:${"a".repeat(32)}`,
        field: "job.needs",
        provenance: { ...provenance, location: { ...provenance.location, path: "job.needs" } },
        subjectId: lintSubjectId,
        value: [],
      },
      {
        category: "identity" as const,
        criticality: "informational" as const,
        factId: `fact:${"b".repeat(32)}`,
        field: "job.name",
        provenance: { ...provenance, location: { ...provenance.location, path: "job.name" } },
        subjectId: lintSubjectId,
        value: "Lint",
      },
    ],
    workflows: [
      {
        ...workflow,
        jobs: [
          {
            ...terminal,
            jobId: "lint",
            name: "Lint",
            needs: [],
            providerSignalName: "Lint",
            subjectId: lintSubjectId,
          },
          { ...terminal, condition: "always()", needs: ["lint"] },
        ],
      },
    ],
  };
}

function breakAggregateTopology(cyclic: boolean) {
  const value = reportWithAggregateSignal();
  const workflow = value.workflows[0];
  const lint = workflow?.jobs[0];
  const terminal = workflow?.jobs[1];
  if (!workflow || !lint || !terminal) throw new Error("aggregate fixture is incomplete");
  const subjectId = cyclic ? lint.subjectId : terminal.subjectId;
  const needs = cyclic ? [terminal.jobId] : [];
  return {
    ...value,
    facts: value.facts.map((entry) =>
      entry.subjectId === subjectId && entry.field === "job.needs"
        ? { ...entry, value: needs }
        : entry,
    ),
    workflows: [
      {
        ...workflow,
        jobs: cyclic ? [{ ...lint, needs }, terminal] : [lint, { ...terminal, needs }],
      },
    ],
  };
}

interface WorkbenchContract {
  readonly paths: {
    readonly "/api/v1/workbench/repositories/{installation_id}/{repository_id}": {
      readonly get: {
        readonly parameters: readonly {
          readonly name: string;
          readonly schema: { readonly maximum?: number };
        }[];
      };
    };
  };
}
