import { analyticsMappingIsCanonical } from "./analyticsIdentity";
import {
  type AnalyticsQuery,
  type AnalyticsResponse,
  analyticsQuerySchema,
  analyticsResponseSchema,
} from "./analyticsSchema";
import { observationInstant } from "./observationSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

export async function fetchHistoryAnalytics(
  input: AnalyticsQuery,
  signal?: AbortSignal,
): Promise<EconomicsResult<AnalyticsResponse>> {
  const parsed = analyticsQuerySchema.safeParse(input);
  if (!parsed.success) return { kind: "invalid-response" };
  const query = parsed.data;
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (key !== "installationId" && key !== "repositoryId" && value !== null)
      parameters.set(key, String(value));
  }
  return requestEconomics({
    path: `/api/v2/economics/repositories/${query.installationId}/${query.repositoryId}/history/analytics?${parameters}`,
    schema: analyticsResponseSchema,
    maximumBytes: 1024 * 1024,
    signal,
    admits: async (response) =>
      response.outcome === "unavailable" ||
      ((await analyticsMappingIsCanonical(response.report)) &&
        Object.entries(query).every(([key, value]) => {
          const actual = response.report.query[key as keyof AnalyticsQuery];
          return key === "createdFrom" || key === "createdUntil"
            ? typeof actual === "string" &&
                typeof value === "string" &&
                observationInstant(actual) === observationInstant(value)
            : actual === value;
        })),
  });
}
