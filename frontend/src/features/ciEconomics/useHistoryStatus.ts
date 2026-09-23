import { useCallback } from "react";
import { fetchHistoryStatus } from "../../api/ciEconomics/historyClient";
import type { HistoryStatus } from "../../api/ciEconomics/historyStatusSchema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { useEconomicsStatus } from "./useEconomicsStatus";

const shouldPoll = (value: HistoryStatus) => value.snapshot?.configuration.enabled === true;

export function useHistoryStatus({ installationId, repositoryId }: WorkbenchScope, active = true) {
  const read = useCallback(
    (signal: AbortSignal) => fetchHistoryStatus({ installationId, repositoryId }, signal),
    [installationId, repositoryId],
  );
  return useEconomicsStatus(`${installationId}:${repositoryId}`, read, shouldPoll, active);
}
