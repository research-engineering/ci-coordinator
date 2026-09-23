import { z } from "zod";
import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { caughtFailure } from "../shared/responseFailure";
import { MAX_WORKBENCH_SECTION_ITEMS } from "./limits";
import { type WorkbenchSnapshot, workbenchSnapshotSchema } from "./schema";

const REQUEST_TIMEOUT_MS = 15_000;

export interface WorkbenchScope {
  readonly installationId: number;
  readonly repositoryId: number;
  readonly limit: number;
}

export const workbenchScopeSchema = z.object({
  installationId: z.int().positive(),
  repositoryId: z.int().positive(),
  limit: z.int().min(1).max(MAX_WORKBENCH_SECTION_ITEMS),
});

export function admitWorkbenchScope(candidate: WorkbenchScope): WorkbenchScope | undefined {
  return workbenchScopeSchema.safeParse(candidate).success ? candidate : undefined;
}

export type WorkbenchResult =
  | { readonly kind: "ready"; readonly snapshot: WorkbenchSnapshot }
  | { readonly kind: "unauthenticated" }
  | { readonly kind: "forbidden" }
  | { readonly kind: "unavailable" }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export async function fetchWorkbenchSnapshot(
  scope: WorkbenchScope,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<WorkbenchResult> {
  const path = `/api/v1/workbench/repositories/${scope.installationId}/${scope.repositoryId}`;
  const query = new URLSearchParams({ limit: String(scope.limit) });
  try {
    const url = new URL(`${path}?${query}`, globalThis.location.origin);
    const response = await boundedFetch(
      new Request(url, {
        credentials: "same-origin",
        headers: { accept: "application/json" },
        signal: combinedSignal(signal, timeoutMs),
      }),
    );
    if (response.status === 401) return { kind: "unauthenticated" };
    if (response.status === 403) return { kind: "forbidden" };
    if (response.status === 503) return { kind: "unavailable" };
    if (response.status !== 200) return { kind: "invalid-response" };
    const snapshot = workbenchSnapshotSchema.parse(await response.json());
    if (
      snapshot.scope.installationId !== scope.installationId ||
      snapshot.scope.repositoryId !== scope.repositoryId
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", snapshot };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}
