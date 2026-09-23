import { ZodError } from "zod";
import type { operations } from "../generated";
import { boundedFetch, combinedSignal, ResponseLimitError } from "../shared/boundedFetch";
import {
  attemptSummaryCursor,
  CI_ECONOMICS_PAGE_SIZE,
  type CiEconomicsAttemptJobs,
  type CiEconomicsAttemptPage,
  type CiEconomicsAttemptSummary,
  ciEconomicsAttemptCursorSchema,
  ciEconomicsAttemptJobsSchema,
  ciEconomicsAttemptPageSchema,
  ciEconomicsAttemptSummarySchema,
  ciEconomicsErrorSchema,
  MAX_CI_ECONOMICS_PAGE_SIZE,
  sameAttemptEvidence,
} from "./schema";

type ListAttemptsOperation = operations["list_repository_ci_economics_attempts"];
type GetAttemptJobsOperation = operations["get_ci_economics_attempt_jobs"];
type ListAttemptsQuery = NonNullable<ListAttemptsOperation["parameters"]["query"]>;
type ListAttemptsPath = ListAttemptsOperation["parameters"]["path"];
type GetAttemptJobsQuery = GetAttemptJobsOperation["parameters"]["query"];
type GetAttemptJobsPath = GetAttemptJobsOperation["parameters"]["path"];

const REQUEST_TIMEOUT_MS = 15_000;
const ATTEMPT_PAGE_RESPONSE_BYTES = 256 * 1024;
const ATTEMPT_JOBS_RESPONSE_BYTES = 4 * 1024 * 1024;

export interface CiEconomicsRepositoryScope {
  readonly installationId: number;
  readonly repositoryId: number;
}

type CiEconomicsFailure =
  | { readonly kind: "unauthenticated" }
  | { readonly kind: "forbidden" }
  | { readonly kind: "not-found" }
  | { readonly kind: "unavailable" }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };
type CiEconomicsListFailure = Exclude<CiEconomicsFailure, { readonly kind: "not-found" }>;

export type CiEconomicsAttemptPageResult =
  | { readonly kind: "ready"; readonly page: CiEconomicsAttemptPage }
  | CiEconomicsListFailure;

export type CiEconomicsAttemptJobsResult =
  | { readonly kind: "ready"; readonly economics: CiEconomicsAttemptJobs }
  | CiEconomicsFailure;

export async function fetchCiEconomicsAttempts(
  scope: CiEconomicsRepositoryScope,
  afterCursor: string | null,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
  limit = CI_ECONOMICS_PAGE_SIZE,
): Promise<CiEconomicsAttemptPageResult> {
  if (!scopeIsAdmitted(scope) || !limitIsAdmitted(limit) || !cursorIsAdmitted(afterCursor)) {
    return { kind: "invalid-response" };
  }
  const pathParameters: ListAttemptsPath = {
    installation_id: scope.installationId,
    repository_id: scope.repositoryId,
  };
  const queryParameters: ListAttemptsQuery = {
    limit,
    ...(afterCursor === null ? {} : { afterCursor }),
  };
  const path = `/api/v1/economics/repositories/${pathParameters.installation_id}/${pathParameters.repository_id}/attempts`;
  try {
    const response = await boundedFetch(
      request(`${path}?${new URLSearchParams(stringEntries(queryParameters))}`, signal, timeoutMs),
      ATTEMPT_PAGE_RESPONSE_BYTES,
    );
    if (response.status !== 200) return await failure(response, false);
    if (!isJsonResponse(response)) return { kind: "invalid-response" };
    const page = ciEconomicsAttemptPageSchema.parse(await response.json());
    if (
      page.items.length > limit ||
      page.items.some(
        (item) =>
          item.attempt.installationId !== scope.installationId ||
          item.attempt.repositoryId !== scope.repositoryId,
      ) ||
      (afterCursor !== null && page.items.some((item) => attemptSummaryCursor(item) >= afterCursor))
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", page };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

export async function fetchCiEconomicsAttemptJobs(
  summary: CiEconomicsAttemptSummary,
  afterJobId: number | null,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
  limit = CI_ECONOMICS_PAGE_SIZE,
): Promise<CiEconomicsAttemptJobsResult> {
  if (
    !ciEconomicsAttemptSummarySchema.safeParse(summary).success ||
    !limitIsAdmitted(limit) ||
    (afterJobId !== null && (!Number.isSafeInteger(afterJobId) || afterJobId < 1))
  ) {
    return { kind: "invalid-response" };
  }
  const attempt = summary.attempt;
  const pathParameters: GetAttemptJobsPath = {
    installation_id: attempt.installationId,
    repository_id: attempt.repositoryId,
    run_attempt: attempt.runAttempt,
    workflow_run_id: attempt.workflowRunId,
  };
  const queryParameters: GetAttemptJobsQuery = {
    headSha: attempt.headSha,
    limit,
    ...(afterJobId === null ? {} : { afterJobId }),
  };
  const path =
    `/api/v1/economics/repositories/${pathParameters.installation_id}/${pathParameters.repository_id}` +
    `/attempts/${pathParameters.workflow_run_id}/${pathParameters.run_attempt}/jobs`;
  try {
    const response = await boundedFetch(
      request(`${path}?${new URLSearchParams(stringEntries(queryParameters))}`, signal, timeoutMs),
      ATTEMPT_JOBS_RESPONSE_BYTES,
    );
    if (response.status !== 200) return await failure(response, true);
    if (!isJsonResponse(response)) return { kind: "invalid-response" };
    const economics = ciEconomicsAttemptJobsSchema.parse(await response.json());
    if (
      !sameAttemptEvidence(summary, economics) ||
      economics.jobs.length > limit ||
      (afterJobId !== null && economics.jobs.some((job) => job.providerJobId <= afterJobId))
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", economics };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

function request(path: string, signal: AbortSignal | undefined, timeoutMs: number): Request {
  return new Request(new URL(path, globalThis.location.origin), {
    credentials: "same-origin",
    headers: { accept: "application/json" },
    signal: combinedSignal(signal, timeoutMs),
  });
}

function failure(response: Response, allowNotFound: false): Promise<CiEconomicsListFailure>;
function failure(response: Response, allowNotFound: true): Promise<CiEconomicsFailure>;
async function failure(response: Response, allowNotFound: boolean): Promise<CiEconomicsFailure> {
  const expected =
    response.status === 401
      ? "unauthenticated"
      : response.status === 403
        ? "forbidden"
        : response.status === 404 && allowNotFound
          ? "not_found"
          : response.status === 503
            ? "unavailable"
            : undefined;
  if (expected === undefined) return { kind: "invalid-response" };
  if (!isJsonResponse(response)) return { kind: "invalid-response" };
  try {
    const admitted = ciEconomicsErrorSchema.parse(await response.json());
    if (admitted.error !== expected) return { kind: "invalid-response" };
    return { kind: expected === "not_found" ? "not-found" : expected };
  } catch {
    return { kind: "invalid-response" };
  }
}

function caughtFailure(
  error: unknown,
  signal: AbortSignal | undefined,
): CiEconomicsListFailure | never {
  if (
    error instanceof ZodError ||
    error instanceof SyntaxError ||
    error instanceof ResponseLimitError ||
    error instanceof RangeError
  ) {
    return { kind: "invalid-response" };
  }
  if (signal?.aborted) throw error;
  return { kind: "network-failure" };
}

function scopeIsAdmitted(scope: CiEconomicsRepositoryScope): boolean {
  return (
    Number.isSafeInteger(scope.installationId) &&
    scope.installationId > 0 &&
    Number.isSafeInteger(scope.repositoryId) &&
    scope.repositoryId > 0
  );
}

function limitIsAdmitted(limit: number): boolean {
  return Number.isInteger(limit) && limit >= 1 && limit <= MAX_CI_ECONOMICS_PAGE_SIZE;
}

function cursorIsAdmitted(value: string | null): boolean {
  return value === null || ciEconomicsAttemptCursorSchema.safeParse(value).success;
}

function stringEntries(value: object): Record<string, string> {
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, String(item)]));
}

function isJsonResponse(response: Response): boolean {
  return (
    response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() ===
    "application/json"
  );
}
