import { z } from "zod";
import { boundedFetch, combinedSignal, ResponseLimitError } from "../shared/boundedFetch";
import {
  type ActivityPage,
  type ActivityQuery,
  activityPageMatches,
  activityPageSchema,
  activityQuerySchema,
} from "./schema";

export type ActivityResult =
  | { readonly kind: "ready"; readonly page: ActivityPage }
  | {
      readonly kind:
        | "unauthenticated"
        | "forbidden"
        | "unavailable"
        | "invalid-response"
        | "network-failure";
    };
const failure = z.strictObject({
  ok: z.literal(false),
  error: z.enum(["invalid_request", "unauthenticated", "forbidden", "unavailable"]),
});

export async function fetchActivity(
  query: ActivityQuery,
  cursor: string | null,
  signal: AbortSignal,
  exporting = false,
): Promise<ActivityResult> {
  const admitted = activityQuerySchema.safeParse(query);
  if (!admitted.success || (cursor !== null && (!cursor.length || cursor.length > 1024)))
    return { kind: "invalid-response" };
  const parameters = new URLSearchParams();
  for (const key of ["since", "until", "issuer", "actor", "action", "limit"] as const) {
    const value = admitted.data[key];
    if (value !== null) parameters.set(key, String(value));
  }
  if (cursor !== null) parameters.set("cursor", cursor);
  const resource =
    query.source === "security"
      ? "security"
      : `repositories/${query.installationId}/${query.repositoryId}`;
  try {
    const response = await boundedFetch(
      new Request(
        new URL(
          `/api/v1/activity/${resource}${exporting ? "/export" : ""}?${parameters}`,
          location.origin,
        ),
        {
          credentials: "same-origin",
          cache: "no-store",
          headers: { accept: "application/json" },
          signal: combinedSignal(signal, 10000),
        },
      ),
      1048576,
    );
    if (
      response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() !==
      "application/json"
    )
      return { kind: "invalid-response" };
    const raw: unknown = await response.json();
    if (response.status === 200) {
      const page = activityPageSchema.parse(raw);
      return activityPageMatches(page, admitted.data)
        ? { kind: "ready", page }
        : { kind: "invalid-response" };
    }
    const error = failure.parse(raw).error;
    if (response.status === 401 && error === "unauthenticated") return { kind: "unauthenticated" };
    if (response.status === 403 && error === "forbidden") return { kind: "forbidden" };
    if (response.status === 503 && error === "unavailable") return { kind: "unavailable" };
    return { kind: "invalid-response" };
  } catch (error) {
    if (signal.aborted) throw error;
    return {
      kind:
        error instanceof z.ZodError ||
        error instanceof SyntaxError ||
        error instanceof ResponseLimitError
          ? "invalid-response"
          : "network-failure",
    };
  }
}
