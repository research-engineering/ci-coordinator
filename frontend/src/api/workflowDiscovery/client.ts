import type { components } from "../generated";
import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { caughtFailure } from "../shared/responseFailure";
import { proposalManifestIdentityMatches } from "./manifestIdentity";
import {
  type WorkflowDiscoveryError,
  type WorkflowDiscoveryReport,
  workflowDiscoveryErrorSchema,
  workflowDiscoverySchema,
} from "./schema";

const DISCOVERY_TIMEOUT_MS = 30_000;
type RepositoryScope = components["schemas"]["RepositoryScopeResponse"];

export type WorkflowDiscoveryResult =
  | { readonly kind: "ready"; readonly report: WorkflowDiscoveryReport }
  | { readonly kind: "unauthenticated" }
  | { readonly kind: "forbidden" }
  | { readonly kind: "invalid-revision" }
  | { readonly kind: "not-found" }
  | { readonly kind: "overloaded" }
  | { readonly kind: "rate-limited" }
  | { readonly kind: "unavailable"; readonly reason: WorkflowDiscoveryError["error"] }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export async function fetchWorkflowDiscovery(
  scope: RepositoryScope,
  revision?: string,
  signal?: AbortSignal,
  timeoutMs = DISCOVERY_TIMEOUT_MS,
): Promise<WorkflowDiscoveryResult> {
  if (
    !Number.isSafeInteger(scope.installationId) ||
    scope.installationId < 1 ||
    !Number.isSafeInteger(scope.repositoryId) ||
    scope.repositoryId < 1 ||
    (revision !== undefined && !/^[0-9a-f]{40}$/.test(revision))
  ) {
    return { kind: "invalid-revision" };
  }
  const path =
    `/api/v1/workbench/repositories/${scope.installationId}/${scope.repositoryId}` +
    "/workflow-discovery";
  const query = revision === undefined ? "" : `?${new URLSearchParams({ revision })}`;
  try {
    const response = await boundedFetch(
      new Request(new URL(`${path}${query}`, globalThis.location.origin), {
        credentials: "same-origin",
        headers: { accept: "application/json" },
        signal: combinedSignal(signal, timeoutMs),
      }),
    );
    if (response.status !== 200) return await discoveryFailure(response);
    const report = workflowDiscoverySchema.parse(await response.json());
    if (
      !(await proposalManifestIdentityMatches(report)) ||
      report.repository.scope.installationId !== scope.installationId ||
      report.repository.scope.repositoryId !== scope.repositoryId ||
      (revision !== undefined &&
        (report.revision !== revision || report.proposal.state === "reviewable"))
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", report };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

async function discoveryFailure(response: Response): Promise<WorkflowDiscoveryResult> {
  let body: WorkflowDiscoveryError;
  try {
    body = workflowDiscoveryErrorSchema.parse(await response.json());
  } catch {
    return { kind: "invalid-response" };
  }
  if (response.status === 401 && body.error === "unauthenticated") {
    return { kind: "unauthenticated" };
  }
  if (response.status === 403 && body.error === "forbidden") return { kind: "forbidden" };
  if (response.status === 404 && body.error === "not_found") return { kind: "not-found" };
  if (response.status === 422 && body.error === "invalid_revision") {
    return { kind: "invalid-revision" };
  }
  if (response.status === 429 && body.error === "rate_limited") return { kind: "rate-limited" };
  if (response.status === 503 && body.error === "overloaded") return { kind: "overloaded" };
  if (
    response.status === 503 &&
    ![
      "unauthenticated",
      "forbidden",
      "invalid_revision",
      "not_found",
      "overloaded",
      "rate_limited",
    ].includes(body.error)
  ) {
    return { kind: "unavailable", reason: body.error };
  }
  return { kind: "invalid-response" };
}
