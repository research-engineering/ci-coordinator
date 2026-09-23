import {
  type RetentionPreview,
  type RetentionRequest,
  type RetentionResult,
  type RetentionSelection,
  retentionPreviewSchema,
  retentionRequestSchema,
  retentionResultSchema,
  retentionSelectionSchema,
  sameRetentionSelection,
} from "./archiveRetentionSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

export async function previewArchiveRetention(
  input: RetentionSelection,
  signal?: AbortSignal,
): Promise<EconomicsResult<RetentionPreview>> {
  const parsed = retentionSelectionSchema.safeParse(input);
  if (!parsed.success) return { kind: "invalid-response" };
  return requestEconomics({
    path: "/api/v2/economics/history/retention/preview",
    body: parsed.data,
    schema: retentionPreviewSchema,
    maximumBytes: 256 * 1024,
    signal,
    admits: (value) => sameRetentionSelection(value.preview.selection, parsed.data),
  });
}

export async function applyArchiveRetention(
  input: RetentionRequest,
  reviewed: RetentionPreview,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<EconomicsResult<RetentionResult>> {
  const parsed = retentionRequestSchema.safeParse(input);
  if (
    !parsed.success ||
    !/^[A-Za-z0-9_-]{43}$/.test(csrfToken) ||
    parsed.data.reviewedDigest !== reviewed.reviewedDigest ||
    !sameRetentionSelection(parsed.data.selection, reviewed.preview.selection)
  )
    return { kind: "invalid-response" };
  return requestEconomics({
    path: "/api/v2/economics/history/retention/apply",
    body: parsed.data,
    schema: retentionResultSchema,
    maximumBytes: 256 * 1024,
    csrfToken,
    signal,
    successStatuses: [200, 409],
    admits: (value, status) =>
      value.operationId === parsed.data.operationId &&
      (value.preview === null
        ? status === 409
        : status === 200 &&
          value.dataRevision === parsed.data.selection.dataRevision + 1 &&
          JSON.stringify(value.preview) === JSON.stringify(reviewed.preview)),
  });
}
