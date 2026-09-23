import {
  type ArchiveDetailQuery,
  type ArchiveDetailRead,
  type ArchiveQuery,
  type ArchiveRead,
  archiveDetailQuerySchema,
  archiveDetailReadSchema,
  archiveQuerySchema,
  archiveReadSchema,
  sameArchiveQuery,
} from "./archiveReadSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

export const MAX_ARCHIVE_RESPONSE_BYTES = 2 * 1024 * 1024;
export const MAX_ARCHIVE_DETAIL_RESPONSE_BYTES = 512 * 1024;

export async function fetchArchivePage(
  input: ArchiveQuery,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<EconomicsResult<ArchiveRead>> {
  const parsed = archiveQuerySchema.safeParse(input);
  if (!parsed.success || (cursor !== null && (cursor.length === 0 || cursor.length > 4096)))
    return { kind: "invalid-response" };
  const query = parsed.data;
  const parameters = new URLSearchParams({
    generation: String(query.generation),
    limit: String(query.limit),
  });
  const keys = {
    createdFrom: "created_from",
    createdThrough: "created_through",
    workflowId: "workflow_id",
    workflowRunId: "workflow_run_id",
    runAttempt: "run_attempt",
    jobName: "job_name",
  } as const;
  for (const [name, wire] of Object.entries(keys)) {
    const value = query[name as keyof typeof keys];
    if (value !== null) parameters.set(wire, String(value));
  }
  if (cursor !== null) parameters.set("cursor", cursor);
  return requestEconomics({
    path: `/api/v2/economics/repositories/${query.installationId}/${query.repositoryId}/history/archive/${query.kind}?${parameters}`,
    schema: archiveReadSchema,
    maximumBytes: MAX_ARCHIVE_RESPONSE_BYTES,
    signal,
    admits: (page) => sameArchiveQuery(query, page.query),
  });
}

export async function fetchArchiveDetail(
  query: ArchiveDetailQuery,
  signal?: AbortSignal,
): Promise<EconomicsResult<ArchiveDetailRead>> {
  const parsed = archiveDetailQuerySchema.safeParse(query);
  if (!parsed.success) return { kind: "invalid-response" };
  const selected = parsed.data;
  return requestEconomics({
    path: `/api/v2/economics/repositories/${selected.installationId}/${selected.repositoryId}/history/attempts/${selected.workflowRunId}/${selected.runAttempt}/detail?generation=${selected.generation}`,
    schema: archiveDetailReadSchema,
    maximumBytes: MAX_ARCHIVE_DETAIL_RESPONSE_BYTES,
    signal,
    admits: (page) =>
      page.query.installationId === selected.installationId &&
      page.query.repositoryId === selected.repositoryId &&
      page.query.generation === selected.generation &&
      page.query.workflowRunId === selected.workflowRunId &&
      page.query.runAttempt === selected.runAttempt,
  });
}
