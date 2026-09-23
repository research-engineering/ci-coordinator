import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { caughtFailure } from "../shared/responseFailure";
import {
  type ControlPlaneSession,
  controlPlaneIdentityErrorSchema,
  controlPlaneLogoutSchema,
  controlPlaneSessionSchema,
} from "./schema";

const REQUEST_TIMEOUT_MS = 15_000;

export type ControlPlaneSessionResult =
  | { readonly kind: "authenticated"; readonly session: ControlPlaneSession }
  | { readonly kind: "anonymous" }
  | { readonly kind: "overloaded" }
  | { readonly kind: "unavailable" }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export type ControlPlaneLogoutResult =
  | { readonly kind: "complete"; readonly redirectUrl: string }
  | { readonly kind: "anonymous" }
  | { readonly kind: "forbidden" }
  | { readonly kind: "overloaded" }
  | { readonly kind: "unavailable" }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export async function fetchControlPlaneSession(
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<ControlPlaneSessionResult> {
  try {
    const response = await boundedFetch(
      new Request(new URL("/api/v1/auth/session", globalThis.location.origin), {
        cache: "no-store",
        credentials: "same-origin",
        headers: { accept: "application/json" },
        signal: combinedSignal(signal, timeoutMs),
      }),
    );
    if (response.status === 401) return { kind: "anonymous" };
    if (response.status === 503) return await identityServiceFailure(response);
    if (response.status !== 200) return { kind: "invalid-response" };
    return {
      kind: "authenticated",
      session: controlPlaneSessionSchema.parse(await response.json()),
    };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

export function isSessionChallenge(request: Request, status: number): boolean {
  const url = new URL(request.url);
  return (
    status === 401 &&
    !request.signal.aborted &&
    url.origin === globalThis.location.origin &&
    (url.pathname.startsWith("/api/v1/") || url.pathname.startsWith("/api/v2/")) &&
    !url.pathname.startsWith("/api/v1/auth/") &&
    request.credentials !== "omit" &&
    !request.headers.has("authorization")
  );
}

export async function logoutControlPlaneSession(
  csrfToken: string,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<ControlPlaneLogoutResult> {
  try {
    const response = await boundedFetch(
      new Request(new URL("/api/v1/auth/keycloak/logout", globalThis.location.origin), {
        body: "{}",
        credentials: "same-origin",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
          "x-csrf-token": csrfToken,
        },
        method: "POST",
        signal: combinedSignal(signal, timeoutMs),
      }),
    );
    if (response.status === 200) {
      const result = controlPlaneLogoutSchema.parse(await response.json());
      return { kind: "complete", redirectUrl: result.redirectUrl };
    }
    if (response.status === 401) return { kind: "anonymous" };
    if (response.status === 403) return { kind: "forbidden" };
    if (response.status === 503) return await identityServiceFailure(response);
    return { kind: "invalid-response" };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

async function identityServiceFailure(
  response: Response,
): Promise<
  | { readonly kind: "overloaded" }
  | { readonly kind: "unavailable" }
  | { readonly kind: "invalid-response" }
> {
  const failure = controlPlaneIdentityErrorSchema.parse(await response.json());
  return failure.error === "overloaded"
    ? { kind: "overloaded" }
    : failure.error === "unavailable"
      ? { kind: "unavailable" }
      : { kind: "invalid-response" };
}
