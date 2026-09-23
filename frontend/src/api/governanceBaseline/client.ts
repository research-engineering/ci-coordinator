import type { components } from "../generated";
import { governanceStateDigestMatches } from "../governanceObservation/identity";
import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { caughtFailure } from "../shared/responseFailure";
import { governanceBaselineReasonIsAdmitted } from "./reason";
import {
  type GovernanceBaselineApproval,
  type GovernanceBaselineError,
  type GovernanceBaselinePointer,
  type GovernanceBaselineRead,
  governanceBaselineApprovalSchema,
  governanceBaselineErrorSchema,
  governanceBaselineReadSchema,
} from "./schema";

type RepositoryScope = components["schemas"]["GovernanceScopeResponse"];

const REQUEST_TIMEOUT_MS = 30_000;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export interface GovernanceBaselineApprovalCommand {
  readonly expectedActive: GovernanceBaselinePointer | null;
  readonly expectedStateDigest: string;
  readonly operationId: string;
  readonly reason: string;
  readonly scope: RepositoryScope;
}

export type GovernanceBaselineReadResult =
  | { readonly kind: "ready"; readonly read: GovernanceBaselineRead }
  | { readonly kind: GovernanceBaselineError["error"] }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export type GovernanceBaselineApprovalResult =
  | { readonly kind: "complete"; readonly approval: GovernanceBaselineApproval }
  | { readonly kind: GovernanceBaselineError["error"] }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

type GovernanceBaselineFailure =
  | { readonly kind: GovernanceBaselineError["error"] }
  | { readonly kind: "invalid-response" };

export async function fetchGovernanceBaseline(
  scope: RepositoryScope,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<GovernanceBaselineReadResult> {
  if (!scopeIsAdmitted(scope)) return { kind: "invalid-response" };
  try {
    const response = await boundedFetch(
      new Request(new URL(baselinePath(scope), globalThis.location.origin), {
        credentials: "same-origin",
        headers: { accept: "application/json" },
        signal: combinedSignal(signal, timeoutMs),
      }),
      MAX_RESPONSE_BYTES,
    );
    if (response.status !== 200) return await baselineFailure(response);
    const read = governanceBaselineReadSchema.parse(await response.json());
    if (
      !sameScope(read.scope, scope) ||
      (read.baseline !== null && !(await governanceStateDigestMatches(read.baseline.state)))
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", read };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

export async function approveGovernanceBaseline(
  command: GovernanceBaselineApprovalCommand,
  csrfToken: string,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<GovernanceBaselineApprovalResult> {
  if (!commandIsAdmitted(command) || !/^[A-Za-z0-9_-]{43}$/.test(csrfToken)) {
    return { kind: "invalid-response" };
  }
  try {
    const response = await boundedFetch(
      new Request(new URL(baselinePath(command.scope), globalThis.location.origin), {
        body: JSON.stringify({
          expectedActive: command.expectedActive,
          expectedStateDigest: command.expectedStateDigest,
          operationId: command.operationId,
          reason: command.reason,
        }),
        credentials: "same-origin",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
          "x-csrf-token": csrfToken,
        },
        method: "POST",
        signal: combinedSignal(signal, timeoutMs),
      }),
      MAX_RESPONSE_BYTES,
    );
    if (response.status !== 200 && response.status !== 201) {
      return await baselineFailure(response);
    }
    const approval = governanceBaselineApprovalSchema.parse(await response.json());
    if (
      !sameScope(approval.scope, command.scope) ||
      approval.requestOperationId !== command.operationId ||
      approval.baseline.pointer.stateDigest !== command.expectedStateDigest ||
      (response.status === 201) !== (approval.state === "accepted") ||
      !outcomePointerIsAdmitted(approval, command.expectedActive) ||
      !(await governanceStateDigestMatches(approval.baseline.state))
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "complete", approval };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

async function baselineFailure(response: Response): Promise<GovernanceBaselineFailure> {
  let body: GovernanceBaselineError;
  try {
    body = governanceBaselineErrorSchema.parse(await response.json());
  } catch {
    return { kind: "invalid-response" };
  }
  const statusByError: Readonly<Record<GovernanceBaselineError["error"], number>> = {
    unauthenticated: 401,
    forbidden: 403,
    stale: 409,
    baseline_conflict: 409,
    operation_conflict: 409,
    overloaded: 503,
    unavailable: 503,
  };
  return response.status === statusByError[body.error]
    ? { kind: body.error }
    : { kind: "invalid-response" };
}

function outcomePointerIsAdmitted(
  approval: GovernanceBaselineApproval,
  expected: GovernanceBaselinePointer | null,
): boolean {
  if (approval.state === "unchanged") {
    return expected !== null && samePointer(approval.baseline.pointer, expected);
  }
  const supersedes = approval.baseline.supersedes;
  return (
    (expected === null && supersedes === null && approval.baseline.pointer.version === 1) ||
    (expected !== null &&
      supersedes !== null &&
      samePointer(supersedes, expected) &&
      approval.baseline.pointer.version === expected.version + 1)
  );
}

function commandIsAdmitted(command: GovernanceBaselineApprovalCommand): boolean {
  return (
    scopeIsAdmitted(command.scope) &&
    UUID.test(command.operationId) &&
    /^[0-9a-f]{64}$/.test(command.expectedStateDigest) &&
    governanceBaselineReasonIsAdmitted(command.reason) &&
    (command.expectedActive === null ||
      (Number.isSafeInteger(command.expectedActive.version) &&
        command.expectedActive.version > 0 &&
        /^governance-baseline:[0-9a-f]{64}$/.test(command.expectedActive.baselineId) &&
        /^[0-9a-f]{64}$/.test(command.expectedActive.stateDigest)))
  );
}

function baselinePath(scope: RepositoryScope): string {
  return (
    `/api/v1/workbench/repositories/${scope.installationId}/${scope.repositoryId}` +
    "/governance-baselines"
  );
}

function scopeIsAdmitted(scope: RepositoryScope): boolean {
  return (
    Number.isSafeInteger(scope.installationId) &&
    scope.installationId > 0 &&
    Number.isSafeInteger(scope.repositoryId) &&
    scope.repositoryId > 0
  );
}

function sameScope(left: RepositoryScope, right: RepositoryScope): boolean {
  return left.installationId === right.installationId && left.repositoryId === right.repositoryId;
}

function samePointer(left: GovernanceBaselinePointer, right: GovernanceBaselinePointer): boolean {
  return (
    left.baselineId === right.baselineId &&
    left.version === right.version &&
    left.stateDigest === right.stateDigest
  );
}
