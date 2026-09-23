import {
  controlPlaneIdentityErrorSchema,
  controlPlaneSessionSchema,
} from "../src/api/controlPlaneIdentity/schema";
import { governanceComparisonErrorSchema } from "../src/api/governanceComparison/schema";
import {
  installationCatalogSchema,
  providerInventoryErrorSchema,
  repositoryPageSchema,
} from "../src/api/providerInventory/schema";
import { type WorkbenchSnapshot, workbenchSnapshotSchema } from "../src/api/workbench/schema";
import {
  workflowDiscoveryErrorSchema,
  workflowDiscoverySchema,
} from "../src/api/workflowDiscovery/schema";
import { economicsSource } from "../tests/economicsConsoleFixture";
import { economicsScenario } from "./economics";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "./fixtures";
import { exactQuery, response, type SyntheticRequest, type SyntheticResponse } from "./response";
import { DEMO_TIME, type ScenarioName } from "./scenarioNames";

export function retainedRuns(): WorkbenchSnapshot["runs"] {
  return (["success", "failure"] as const).map((state, index) => ({
    attemptCount: 1,
    baseSha: "b".repeat(40),
    claimGeneration: 1,
    contractHash: "c".repeat(64),
    createdAt: "2026-09-08T08:00:00Z",
    deadlineAt: "2026-09-08T10:00:00Z",
    eventName: "push",
    findings:
      state === "failure"
        ? [
            {
              kind: "required_signal_failed",
              message: "Synthetic required check failed",
              signalId: "Full CI",
            },
          ]
        : [],
    headSha: "a".repeat(40),
    leaseActive: false,
    leaseExpiresAt: null,
    maxAttempts: 8,
    nextAttemptAt: "2026-09-08T09:00:00Z",
    ref: "refs/heads/master",
    revision: 1,
    runAttempt: 2,
    state,
    subjectId: `synthetic-run-${state}`,
    workflowRunId: 4201 + index,
  }));
}

export function createScenario(name: ScenarioName) {
  const economics = economicsScenario(
    name === "economics" || name === "stale-navigation"
      ? [economicsSource(4202), economicsSource(4201), economicsSource(9002, 2)]
      : [],
  );
  const held = Promise.withResolvers<void>();
  const requested = Promise.withResolvers<void>();
  const providerFailureDelivered = Promise.withResolvers<void>();
  let failurePending = name === "provider-failure-retry";
  let disposed = false;
  const reads: string[] = [];
  const startPath =
    name === "runs"
      ? "/workbench?installationId=1&repositoryId=1&limit=10&tab=runs"
      : name === "economics" || name === "stale-navigation"
        ? "/workbench?installationId=1&repositoryId=1&limit=10&view=economics"
        : "/workbench";

  async function handle(request: SyntheticRequest): Promise<SyntheticResponse | undefined> {
    if (disposed) return undefined;
    const { url, method } = request;
    const path = url.pathname;
    reads.push(`${method} ${path}${url.search}`);
    if (path === "/api/v1/auth/session" && method === "GET" && !url.search) {
      return name === "unauthorized"
        ? response(controlPlaneIdentityErrorSchema, { ok: false, error: "unauthenticated" }, 401)
        : response(
            controlPlaneSessionSchema,
            controlPlaneSessionFixture({
              roles: name === "economics" ? ["audit", "configure", "read"] : ["read"],
              user: {
                actorId: `keycloak-human:v1:${"a".repeat(64)}`,
                displayName: "Synthetic operator",
                preferredUsername: "synthetic",
              },
            }),
          );
    }
    if (
      path === "/api/v1/workbench/installations" &&
      method === "GET" &&
      exactQuery(url, { page: "1", perPage: "30" })
    ) {
      if (name === "unauthorized")
        return response(
          providerInventoryErrorSchema,
          { ok: false, error: "unauthenticated", retryAfterSeconds: null },
          401,
        );
      if (failurePending) {
        return response(
          providerInventoryErrorSchema,
          { ok: false, error: "unavailable", retryAfterSeconds: null },
          503,
        );
      }
      return response(
        installationCatalogSchema,
        installationCatalogFixture({
          observedAt: DEMO_TIME,
          ...(name === "empty" ? { installations: [] } : {}),
        }),
      );
    }
    if (name === "unauthorized") return undefined;
    if (
      path === "/api/v1/workbench/installations/1/repositories" &&
      method === "GET" &&
      exactQuery(url, { page: "1", perPage: "100" })
    ) {
      return response(
        repositoryPageSchema,
        repositoryPageFixture({
          observedAt: DEMO_TIME,
          totalCount: 2,
          repositories: [
            repositoryFixture(),
            repositoryFixture({
              scope: { installationId: 1, repositoryId: 2 },
              nodeId: "R_2",
              name: "synthetic-service",
              fullName: "example-org/synthetic-service",
            }),
          ],
        }),
      );
    }
    for (const repositoryId of [1, 2]) {
      const root = `/api/v1/workbench/repositories/1/${repositoryId}`;
      if (path === root && method === "GET" && exactQuery(url, { limit: "10" }))
        return response(
          workbenchSnapshotSchema,
          workbenchFixture({
            observedAt: DEMO_TIME,
            scope: { installationId: 1, repositoryId },
            runs: name === "runs" ? retainedRuns() : [],
          }),
        );
      if (path === `${root}/workflow-discovery` && method === "GET" && !url.search)
        return repositoryId === 1
          ? response(workflowDiscoverySchema, workflowDiscoveryFixture())
          : response(workflowDiscoveryErrorSchema, { ok: false, error: "not_found" }, 404);
      if (path === `${root}/governance-comparison` && method === "GET" && !url.search)
        return response(
          governanceComparisonErrorSchema,
          { ok: false, error: "forbidden", retryAfterSeconds: null },
          403,
        );
    }
    const result = economics.handle(request);
    if (
      result &&
      name === "stale-navigation" &&
      path === "/api/v2/economics/repositories/1/1/sources"
    ) {
      requested.resolve();
      await held.promise;
    }
    return disposed ? undefined : result;
  }
  return {
    name,
    startPath,
    handle,
    reads,
    commands: economics.commands,
    staleRequested: requested.promise,
    providerFailureDelivered: providerFailureDelivered.promise,
    confirmDelivery(path: string, status: number) {
      if (path === "/api/v1/workbench/installations" && status === 503) {
        failurePending = false;
        providerFailureDelivered.resolve();
      }
    },
    releaseStale: () => held.resolve(),
    dispose() {
      disposed = true;
      held.resolve();
      providerFailureDelivered.resolve();
    },
  };
}
