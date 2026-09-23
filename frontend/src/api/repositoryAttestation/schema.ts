import { z } from "zod";
import type { components } from "../generated";

export type RepositoryAttestationStart =
  components["schemas"]["RepositoryAttestationStartResponse"];
type RepositoryAttestationErrorResponse =
  components["schemas"]["RepositoryAttestationErrorResponse"];

export const repositoryAttestationStartSchema: z.ZodType<RepositoryAttestationStart> =
  z.strictObject({
    authorizationUrl: z
      .url()
      .max(2_048)
      .refine((value) => {
        const url = new URL(value);
        return url.origin === "https://github.com" && url.pathname === "/login/oauth/authorize";
      }, "reviewer authorization URL is not admitted"),
    ok: z.literal(true),
  });

export const repositoryAttestationErrorSchema: z.ZodType<RepositoryAttestationErrorResponse> =
  z.strictObject({
    error: z.enum([
      "already_reviewed",
      "baseline_conflict",
      "blocked",
      "diff_limit",
      "epoch_conflict",
      "forbidden",
      "invalid_callback",
      "operation_conflict",
      "overloaded",
      "rate_limited",
      "replayed",
      "stale",
      "unauthenticated",
      "unavailable",
    ]),
    ok: z.literal(false),
  });

export type RepositoryAttestationError = RepositoryAttestationErrorResponse["error"];
