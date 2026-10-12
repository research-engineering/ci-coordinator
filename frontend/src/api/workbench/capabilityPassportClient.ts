import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { caughtFailure } from "../shared/responseFailure";
import {
  type CapabilityPassport,
  capabilityPassportErrorSchema,
  capabilityPassportSchema,
} from "./capabilityPassportSchema";

const REQUEST_TIMEOUT_MS = 15_000;

export interface CapabilityPassportScope {
  readonly installationId: number;
  readonly repositoryId: number;
}

export type CapabilityPassportResult =
  | { readonly kind: "ready"; readonly passport: CapabilityPassport }
  | { readonly kind: "unauthenticated" }
  | { readonly kind: "forbidden" }
  | { readonly kind: "unavailable" }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export async function fetchCapabilityPassport(
  scope: CapabilityPassportScope,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<CapabilityPassportResult> {
  if (
    !Number.isSafeInteger(scope.installationId) ||
    scope.installationId < 1 ||
    !Number.isSafeInteger(scope.repositoryId) ||
    scope.repositoryId < 1
  ) {
    return { kind: "invalid-response" };
  }
  const path = `/api/v1/workbench/repositories/${scope.installationId}/${scope.repositoryId}/capabilities`;
  try {
    const response = await boundedFetch(
      new Request(new URL(path, globalThis.location.origin), {
        credentials: "same-origin",
        headers: { accept: "application/json" },
        signal: combinedSignal(signal, timeoutMs),
      }),
    );
    if (response.status !== 200) {
      const failure = capabilityPassportErrorSchema.parse(await response.json());
      const expected = { unauthenticated: 401, forbidden: 403, unavailable: 503 }[failure.error];
      return response.status === expected ? { kind: failure.error } : { kind: "invalid-response" };
    }
    const passport = capabilityPassportSchema.parse(await response.json());
    if (
      passport.scope.installationId !== scope.installationId ||
      passport.scope.repositoryId !== scope.repositoryId
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", passport };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}
