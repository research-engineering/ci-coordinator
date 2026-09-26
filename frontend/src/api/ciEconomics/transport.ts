import { z } from "zod";
import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { caughtFailure } from "../shared/responseFailure";

export type EconomicsFailure =
  | {
      readonly kind:
        | "unauthenticated"
        | "forbidden"
        | "not-found"
        | "invalid-response"
        | "network-failure";
    }
  | { readonly kind: "unavailable"; readonly reason: string };
export type EconomicsResult<T> = { readonly kind: "ready"; readonly value: T } | EconomicsFailure;
const errorSchema = z.strictObject({
  ok: z.literal(false),
  error: z.enum([
    "invalid_request",
    "unauthenticated",
    "forbidden",
    "not_found",
    "unavailable",
    "overloaded",
    "provider_unavailable",
    "provider_binding_mismatch",
    "provider_malformed",
    "provider_incomplete",
    "provider_not_terminal",
    "provider_unstable",
    "invalid_cursor",
    "stale_cursor",
    "dataset_fenced",
  ]),
});

export async function requestEconomics<T>(options: {
  readonly path: string;
  readonly schema: z.ZodType<T>;
  readonly maximumBytes: number;
  readonly signal?: AbortSignal | undefined;
  readonly body?: object;
  readonly method?: "PUT";
  readonly csrfToken?: string;
  readonly successStatuses?: readonly number[];
  readonly decode?: (response: Response) => Promise<unknown>;
  readonly admits: (value: T, status: number) => boolean | Promise<boolean>;
}): Promise<EconomicsResult<T>> {
  try {
    const response = await boundedFetch(
      new Request(new URL(options.path, globalThis.location.origin), {
        method: options.method ?? (options.body === undefined ? "GET" : "POST"),
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          accept: "application/json",
          ...(options.body === undefined ? {} : { "content-type": "application/json" }),
          ...(options.csrfToken === undefined ? {} : { "x-csrf-token": options.csrfToken }),
        },
        ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
        signal: combinedSignal(options.signal, 15_000),
      }),
      options.maximumBytes,
    );
    if (
      response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() !==
      "application/json"
    ) {
      return { kind: "invalid-response" };
    }
    const data: unknown = options.decode ? await options.decode(response) : await response.json();
    if ((options.successStatuses ?? [200]).includes(response.status)) {
      const value = options.schema.parse(data);
      return (await options.admits(value, response.status))
        ? { kind: "ready", value }
        : { kind: "invalid-response" };
    }
    const { error } = errorSchema.parse(data);
    if (response.status === 401 && error === "unauthenticated") return { kind: "unauthenticated" };
    if (response.status === 403 && error === "forbidden") return { kind: "forbidden" };
    if (response.status === 404 && error === "not_found") return { kind: "not-found" };
    if (response.status === 409 && (error === "stale_cursor" || error === "dataset_fenced"))
      return { kind: "unavailable", reason: error };
    if (
      response.status === 503 &&
      (error === "unavailable" || error === "overloaded" || error.startsWith("provider_"))
    ) {
      return { kind: "unavailable", reason: error };
    }
    return { kind: "invalid-response" };
  } catch (error) {
    if (options.signal?.aborted) throw error;
    return caughtFailure(error);
  }
}
