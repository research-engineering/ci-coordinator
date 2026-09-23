import {
  CATALOG_PAGE_SIZE,
  type ReportPage,
  reportPageSchema,
  type SourcePage,
  sourceCursorSchema,
  sourceIsBefore,
  sourcePageSchema,
} from "./catalogSchema";
import type { CiEconomicsRepositoryScope } from "./client";
import {
  digest,
  type ProviderSource,
  providerSourceSchema,
  repositoryScopeSchema,
  sameScope,
  sameSourceIdentity,
  sourceIdentityIsCanonical,
} from "./sourceSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

export async function fetchEconomicsSources(
  scope: CiEconomicsRepositoryScope,
  afterCursor: string | null,
  signal?: AbortSignal,
  limit = CATALOG_PAGE_SIZE,
): Promise<EconomicsResult<SourcePage>> {
  if (
    !repositoryScopeSchema.safeParse(scope).success ||
    !pageLimitIsAdmitted(limit) ||
    (afterCursor !== null && !sourceCursorSchema.safeParse(afterCursor).success)
  )
    return { kind: "invalid-response" };
  const query = new URLSearchParams({
    limit: String(limit),
    ...(afterCursor === null ? {} : { afterCursor }),
  });
  return requestEconomics({
    path: `/api/v2/economics/repositories/${scope.installationId}/${scope.repositoryId}/sources?${query}`,
    schema: sourcePageSchema,
    maximumBytes: 256 * 1024,
    signal,
    admits: async (page) =>
      sameScope(scope, page) &&
      page.items.length <= limit &&
      (page.nextCursor === null || page.items.length === limit) &&
      (afterCursor === null ||
        page.items.every((item) => sourceIsBefore(item.source, afterCursor))) &&
      (await Promise.all(page.items.map((item) => sourceIdentityIsCanonical(item.source)))).every(
        Boolean,
      ),
  });
}

export async function fetchEconomicsReports(
  source: ProviderSource,
  afterCursor: string | null,
  signal?: AbortSignal,
  limit = CATALOG_PAGE_SIZE,
): Promise<EconomicsResult<ReportPage>> {
  if (
    !providerSourceSchema.safeParse(source).success ||
    !pageLimitIsAdmitted(limit) ||
    (afterCursor !== null && !digest.safeParse(afterCursor).success)
  )
    return { kind: "invalid-response" };
  if (!(await sourceIdentityIsCanonical(source))) return { kind: "invalid-response" };
  const query = new URLSearchParams({
    limit: String(limit),
    ...(afterCursor === null ? {} : { afterCursor }),
  });
  const scope = source.attempt;
  return requestEconomics({
    path: `/api/v2/economics/repositories/${scope.installationId}/${scope.repositoryId}/sources/${source.sourceId}/reports?${query}`,
    schema: reportPageSchema,
    maximumBytes: 64 * 1024,
    signal,
    admits: (page) =>
      sameSourceIdentity(source, page.source) &&
      page.items.length <= limit &&
      (page.nextCursor === null || page.items.length === limit) &&
      (afterCursor === null || page.items.every((item) => item.reportId > afterCursor)),
  });
}

function pageLimitIsAdmitted(limit: number): boolean {
  return Number.isInteger(limit) && limit >= 1 && limit <= 100;
}
