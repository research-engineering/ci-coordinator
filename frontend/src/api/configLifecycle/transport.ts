import { ZodError } from "zod";
import { boundedFetch, combinedSignal, ResponseLimitError } from "../shared/boundedFetch";
import { type ConfigError, errorSchema } from "./schema";

export type LifecycleFailure = {
  readonly kind: ConfigError["error"] | "invalid-response" | "invalid-request" | "network-failure";
  readonly diagnostics?: ConfigError["diagnostics"];
};
export type LifecycleResult<T> = { readonly kind: "ready"; readonly value: T } | LifecycleFailure;

export async function lifecycleRequest(
  path: string,
  signal: AbortSignal | undefined,
  body?: unknown,
  csrfToken?: string,
): Promise<Response> {
  signal?.throwIfAborted();
  if (body !== undefined && (!csrfToken || !/^[A-Za-z0-9_-]{43}$/.test(csrfToken)))
    throw new TypeError("Current CSRF required");
  const headers = new Headers({ accept: "application/json, application/yaml" });
  if (body !== undefined) {
    headers.set("content-type", "application/json");
    headers.set("x-csrf-token", csrfToken ?? "");
  }
  return boundedFetch(
    new Request(new URL(path, globalThis.location.origin), {
      method: body === undefined ? "GET" : "POST",
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      headers,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      signal: combinedSignal(signal, 30_000),
    }),
  );
}

export function noStore(response: Response): boolean {
  return (
    response.headers
      .get("cache-control")
      ?.split(",")
      .some((value) => value.trim().toLowerCase() === "no-store") === true
  );
}

export async function failure(response: Response): Promise<LifecycleFailure> {
  const parsed = errorSchema.safeParse(await response.json());
  if (!parsed.success || !noStore(response)) return { kind: "invalid-response" };
  const statuses: Record<ConfigError["error"], readonly number[]> = {
    unauthenticated: [401],
    forbidden: [403],
    invalid_config: [400, 413, 422],
    conflict: [409],
    revision_conflict: [409],
    target_unavailable: [404],
    attestation_invalid: [409],
    coverage_reducing: [409],
    coverage_unproven: [409],
    overloaded: [503],
    unavailable: [503],
  };
  return statuses[parsed.data.error].includes(response.status)
    ? { kind: parsed.data.error, diagnostics: parsed.data.diagnostics }
    : { kind: "invalid-response" };
}

export async function jsonResult<T>(response: Response, parse: (input: unknown) => T): Promise<T> {
  if (
    !noStore(response) ||
    response.headers.get("content-type")?.split(";")[0]?.trim() !== "application/json"
  )
    throw new SyntaxError("Invalid response metadata");
  return parse(await response.json());
}

export async function attempt<T>(
  action: () => Promise<LifecycleResult<T>>,
): Promise<LifecycleResult<T>> {
  try {
    return await action();
  } catch (error) {
    if (
      error instanceof ZodError ||
      error instanceof SyntaxError ||
      error instanceof ResponseLimitError
    )
      return { kind: "invalid-response" };
    return { kind: "network-failure" };
  }
}
