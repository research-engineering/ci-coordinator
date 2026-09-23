import {
  type GapRepairInterval,
  type GapRepairRequest,
  type GapRepairResult,
  gapRepairRequestSchema,
  gapRepairResultSchema,
} from "./archiveGapRepairSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

export async function retryArchiveGaps(
  request: GapRepairRequest,
  intervals: readonly GapRepairInterval[],
  csrfToken: string,
  signal?: AbortSignal,
): Promise<EconomicsResult<GapRepairResult>> {
  const parsed = gapRepairRequestSchema.safeParse(request);
  if (!parsed.success || csrfToken.length !== 43 || !/^[A-Za-z0-9_-]{43}$/.test(csrfToken))
    return { kind: "invalid-response" };
  return requestEconomics({
    path: "/api/v2/economics/history/gaps/retry",
    body: parsed.data,
    schema: gapRepairResultSchema,
    maximumBytes: 32 * 1024,
    csrfToken,
    signal,
    successStatuses: [200, 409],
    admits: (value, status) =>
      value.operationId === parsed.data.operationId &&
      (value.receipt === null
        ? status === 409
        : status === 200 &&
          JSON.stringify(value.receipt.request) === JSON.stringify(parsed.data) &&
          JSON.stringify(value.receipt.intervals) === JSON.stringify(intervals)),
  });
}
