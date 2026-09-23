import { canonicalUtcInstant } from "./schema";
import {
  type ProviderSource,
  providerSourceSchema,
  type SourceDiscovery,
  type SourceDiscoveryInput,
  type SourceRegistration,
  sameScope,
  sameSourceIdentity,
  sourceDiscoveryInputSchema,
  sourceDiscoverySchema,
  sourceIdentityIsCanonical,
  sourceRegistrationSchema,
} from "./sourceSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

export async function discoverEconomicsSources(
  input: SourceDiscoveryInput,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<EconomicsResult<SourceDiscovery>> {
  if (!sourceDiscoveryInputSchema.safeParse(input).success || !csrfIsAdmitted(csrfToken))
    return { kind: "invalid-response" };
  return requestEconomics({
    path: "/api/v2/economics/source-discovery",
    schema: sourceDiscoverySchema,
    maximumBytes: 256 * 1024,
    signal,
    body: input,
    csrfToken,
    admits: async (page) =>
      sameScope(input, page) &&
      page.pageNumber === input.pageNumber &&
      canonicalUtcInstant(page.createdFrom) === canonicalUtcInstant(input.createdFrom) &&
      canonicalUtcInstant(page.createdThrough) === canonicalUtcInstant(input.createdThrough) &&
      (await Promise.all(page.sources.map(sourceIdentityIsCanonical))).every(Boolean),
  });
}

export async function registerEconomicsSource(
  source: ProviderSource,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<EconomicsResult<SourceRegistration>> {
  if (!providerSourceSchema.safeParse(source).success || !csrfIsAdmitted(csrfToken))
    return { kind: "invalid-response" };
  if (!(await sourceIdentityIsCanonical(source))) return { kind: "invalid-response" };
  const { installationId, repositoryId, workflowRunId, runAttempt } = source.attempt;
  return requestEconomics({
    path: "/api/v2/economics/sources",
    schema: sourceRegistrationSchema,
    maximumBytes: 4096,
    signal,
    body: { installationId, repositoryId, workflowRunId, runAttempt },
    csrfToken,
    successStatuses: [200, 201, 409],
    admits: (result, status) =>
      sameSourceIdentity(source, result.source) &&
      (result.outcome === "registered"
        ? status === 201
        : result.outcome === "replayed"
          ? status === 200
          : status === 409),
  });
}

function csrfIsAdmitted(value: string): boolean {
  return /^[A-Za-z0-9_-]{43}$/.test(value);
}
