import type { ConfigActivation } from "../src/api/configActivation/schema";
import type { ControlPlaneSession } from "../src/api/controlPlaneIdentity/schema";
import type { GovernanceObservation } from "../src/api/governanceObservation/schema";
import type { InstallationCatalog, RepositoryPage } from "../src/api/providerInventory/schema";
import type { WorkbenchSnapshot } from "../src/api/workbench/schema";
import type { WorkflowDiscoveryReport } from "../src/api/workflowDiscovery/schema";

export function installationFixture(
  overrides: Partial<InstallationCatalog["installations"][number]> = {},
): InstallationCatalog["installations"][number] {
  return {
    accountId: 101,
    accountLogin: "example-org",
    accountType: "Organization",
    installationId: 1,
    repositorySelection: "selected",
    state: "active",
    ...overrides,
  };
}

export function repositoryFixture(
  overrides: Partial<RepositoryPage["repositories"][number]> = {},
): RepositoryPage["repositories"][number] {
  return {
    archived: false,
    defaultBranch: "master",
    disabled: false,
    fork: false,
    fullName: "example-org/ci-coordinator",
    name: "ci-coordinator",
    nodeId: "R_1",
    ownerId: 101,
    ownerLogin: "example-org",
    scope: { installationId: 1, repositoryId: 1 },
    visibility: "private",
    workbenchAuthorized: true,
    ...overrides,
  };
}

export function installationCatalogFixture(
  overrides: Partial<InstallationCatalog> = {},
): InstallationCatalog {
  return {
    complete: true,
    failures: [],
    installations: [installationFixture()],
    page: 1,
    perPage: 30,
    hasNextPage: false,
    consistency: "best_effort",
    observedAt: "2026-07-18T08:00:00Z",
    ok: true,
    ...overrides,
  };
}

export function repositoryPageFixture(overrides: Partial<RepositoryPage> = {}): RepositoryPage {
  return {
    consistency: "best_effort",
    hasNextPage: false,
    installation: installationFixture(),
    observedAt: "2026-07-18T08:00:00Z",
    ok: true,
    page: 1,
    perPage: 100,
    repositories: [repositoryFixture()],
    totalCount: 1,
    ...overrides,
  };
}

export function controlPlaneSessionFixture(
  overrides: Partial<ControlPlaneSession> = {},
): ControlPlaneSession {
  return {
    csrfToken: "c".repeat(43),
    expiresAt: "2026-09-08T13:00:00.000Z",
    ok: true,
    roles: ["activate", "configure", "read"],
    user: {
      actorId: `keycloak-human:v1:${"a".repeat(64)}`,
      displayName: "Bart Simpson",
      preferredUsername: "bart.simpson",
    },
    ...overrides,
  };
}

export function governanceObservationFixture(
  overrides: Partial<GovernanceObservation> = {},
): GovernanceObservation {
  return {
    apiVersion: "2026-03-10",
    baselineState: "unbaselined",
    consistency: "best_effort",
    observedAt: "2026-07-26T12:00:00Z",
    ok: true,
    repository: {
      defaultBranch: "master",
      fullName: "example-org/ci-coordinator",
      name: "ci-coordinator",
      owner: "example-org",
      ownerId: 101,
      scope: { installationId: 1, repositoryId: 1 },
    },
    rules: [
      {
        canonicalJson:
          '{"parameters":{"required_status_checks":["Full CI"]},"ruleset_id":41,' +
          '"ruleset_source":"example-org/ci-coordinator",' +
          '"ruleset_source_type":"Repository","type":"required_status_checks"}',
        rulesetId: 41,
        rulesetSource: "example-org/ci-coordinator",
        rulesetSourceType: "Repository",
        ruleType: "required_status_checks",
      },
    ],
    stateDigest: "84e824c649cc36e3ec99cb36c0b37181e7b5fe6e0ed38e4b66bc5b016c821cb7",
    ...overrides,
  };
}

export function configActivationFixture(
  overrides: Partial<ConfigActivation> = {},
): ConfigActivation {
  return {
    duplicate: false,
    epochId: "4".repeat(64),
    ok: true,
    revision: 1,
    schemaVersion: "ci-config-epoch-activation-result/v1",
    ...overrides,
  };
}

export function workbenchFixture(overrides: Partial<WorkbenchSnapshot> = {}): WorkbenchSnapshot {
  return {
    auditEvents: [],
    configEpochs: [],
    ledgerRevision: 42,
    observedAt: "2026-07-18T08:00:00Z",
    ok: true,
    overrides: [],
    plans: [],
    replay: {
      reason: null,
      snapshotRevision: 42,
      status: "valid",
      verifiedRevision: 42,
    },
    runs: [],
    scope: { installationId: 1, repositoryId: 1 },
    truncated: {
      auditEvents: false,
      configEpochs: false,
      overrides: false,
      plans: false,
      runs: false,
    },
    ...overrides,
  };
}

export function workflowDiscoveryFixture(
  overrides: Partial<WorkflowDiscoveryReport> = {},
): WorkflowDiscoveryReport {
  const sourcePath = ".github/workflows/ci.yml";
  const revision = "a".repeat(40);
  const blobSha = "b".repeat(40);
  const inventoryDigest = "c".repeat(64);
  const workflowSubjectId = `workflow:${"3".repeat(32)}`;
  const jobSubjectId = `job:${"6".repeat(32)}`;
  const provenance = (predicatePath: string) => ({
    blobSha,
    location: { column: 1, line: 1, path: predicatePath },
    parserVersion: "workflow-static-v1",
    revision,
    scope: { installationId: 1, repositoryId: 1 },
    workflowPath: sourcePath,
  });
  const unknownId = `unknown:${"2".repeat(32)}`;
  const policySource = JSON.stringify({
    repository: {
      defaultBranch: "master",
      dynamicCi: null,
      installationId: 1,
      name: "ci-coordinator",
      owner: "example-org",
      repositoryId: 1,
      rules: [
        {
          expectedSignals: [
            {
              kind: "workflow",
              name: "Full CI",
              required: true,
              requiredConclusion: "success",
              source: "native",
              workflowFile: sourcePath,
            },
          ],
          mode: "observe",
          name: "discovered-push-default-branch-ci",
          omittedSignals: [],
          on: { branches: ["master"], event: "push" },
        },
      ],
    },
    schemaVersion: "ci-repository-policy/v1",
  });
  return {
    adoptionAssessments: [
      {
        blockers: ["target_semantic_projection_absent"],
        inventoryDigest,
        nonClaims: [
          "assessment is not omission authority",
          "assessment is not owner approval",
          "assessment is not production admission",
          "assessment is not provider inventory proof",
          "assessment is not runtime behavior proof",
        ],
        recommendedAdapter: "in_place_job_set",
        requiredOwnerInputs: [
          "aggregate gate contract",
          "capability-to-obligation mapping",
          "execution profile ownership",
          "full-validation fallback contract",
        ],
        state: "full_only",
        workflowPath: sourcePath,
      },
    ],
    callEdges: [],
    complete: true,
    facts: [
      {
        category: "identity",
        criticality: "informational",
        factId: `fact:${"1".repeat(32)}`,
        field: "workflow.name",
        provenance: provenance("workflow.name"),
        subjectId: workflowSubjectId,
        value: "CI",
      },
      {
        category: "invocation",
        criticality: "informational",
        factId: `fact:${"2".repeat(32)}`,
        field: "workflow.triggers",
        provenance: provenance("workflow.triggers"),
        subjectId: workflowSubjectId,
        value: ["push", "workflow_dispatch"],
      },
      {
        category: "identity",
        criticality: "safety",
        factId: `fact:${"3".repeat(32)}`,
        field: "job.provider_signal_name",
        provenance: provenance("job.name"),
        subjectId: jobSubjectId,
        value: "Full CI",
      },
      {
        category: "execution",
        criticality: "informational",
        factId: `fact:${"4".repeat(32)}`,
        field: "job.matrix",
        provenance: provenance("job.strategy.matrix"),
        subjectId: jobSubjectId,
        value: [],
      },
      {
        category: "invocation",
        criticality: "informational",
        factId: `fact:${"5".repeat(32)}`,
        field: "job.uses",
        provenance: provenance("job.uses"),
        subjectId: jobSubjectId,
        value: null,
      },
      {
        category: "semantics",
        criticality: "informational",
        factId: `fact:${"6".repeat(32)}`,
        field: "job.condition.syntax",
        provenance: provenance("job.if"),
        subjectId: jobSubjectId,
        value: null,
      },
      {
        category: "invocation",
        criticality: "informational",
        factId: `fact:${"7".repeat(32)}`,
        field: "job.needs",
        provenance: provenance("job.needs"),
        subjectId: jobSubjectId,
        value: [],
      },
      {
        category: "invocation",
        criticality: "safety",
        factId: `fact:${"8".repeat(32)}`,
        field: "workflow.default_branch_ci_events",
        provenance: provenance("workflow.on"),
        subjectId: workflowSubjectId,
        value: ["push"],
      },
      {
        category: "identity",
        criticality: "informational",
        factId: `fact:${"9".repeat(32)}`,
        field: "job.name",
        provenance: provenance("job.name"),
        subjectId: jobSubjectId,
        value: "Full CI",
      },
    ],
    inventoryDigest,
    localGraphClosed: true,
    nonClaims: ["Static syntax does not prove runtime behavior."],
    ok: true,
    parserVersion: "workflow-static-v1",
    proposal: {
      admittedEpochId: "4".repeat(64),
      blockers: [],
      diagnostics: [],
      generatorVersion: "workflow-discovery-proposal/v2",
      inventoryDigest,
      manifestId: "proposal:c0169591134297170c6402e83fc6b67f",
      nonClaims: [
        "proposal is not active policy",
        "proposal is not omission authority",
        "proposal is not owner approval",
        "proposal is not proof of FullCI completeness",
        "proposal is not provider mutation authority",
      ],
      policySource,
      selectedEvents: ["push"],
      selectedJobId: "test",
      selectedJobName: "Full CI",
      selectedWorkflowPath: sourcePath,
      state: "reviewable",
      unknownIds: [unknownId],
    },
    targetProjection: {
      registryHash: null,
      status: "absent",
    },
    repository: {
      defaultBranch: "master",
      name: "ci-coordinator",
      owner: "example-org",
      scope: { installationId: 1, repositoryId: 1 },
    },
    revision,
    sources: [{ blobSha, path: sourcePath, size: 128 }],
    unknowns: [
      {
        category: "authority",
        criticality: "safety",
        field: "job.permissions.effective",
        observedSyntax: null,
        provenance: provenance("job.permissions.effective"),
        reason: "requires_provider_state",
        subjectId: jobSubjectId,
        unknownId,
      },
    ],
    workflows: [
      {
        concurrency: null,
        jobs: [
          {
            concurrency: null,
            condition: null,
            environment: null,
            jobId: "test",
            matrix: [],
            name: "Full CI",
            needs: [],
            permissions: { allLevel: null, entries: [], kind: "absent" },
            providerSignalName: "Full CI",
            runsOn: ["ubuntu-latest"],
            serviceIds: [],
            staticSecretNames: [],
            steps: [
              {
                condition: null,
                hasRun: true,
                index: 0,
                name: "Test",
                uses: null,
                usesDynamic: false,
              },
            ],
            subjectId: jobSubjectId,
            timeoutMinutes: 30,
            uses: null,
            usesDynamic: false,
          },
        ],
        name: "CI",
        path: sourcePath,
        permissions: { allLevel: null, entries: [], kind: "absent" },
        staticSecretNames: [],
        subjectId: workflowSubjectId,
        triggers: ["push", "workflow_dispatch"],
      },
    ],
    ...overrides,
  };
}
