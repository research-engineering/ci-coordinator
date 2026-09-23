import { ZodError } from "zod";
import { ResponseLimitError } from "./boundedFetch";

export function caughtFailure(
  error: unknown,
  signal?: AbortSignal,
): { readonly kind: "invalid-response" } | { readonly kind: "network-failure" } {
  if (
    error instanceof ZodError ||
    error instanceof SyntaxError ||
    error instanceof ResponseLimitError
  ) {
    return { kind: "invalid-response" };
  }
  if (signal?.aborted) throw error;
  return { kind: "network-failure" };
}
