import { useCallback } from "react";
import { fetchObservationStatus } from "../../api/ciEconomics/observationClient";
import type { ObservationStatus } from "../../api/ciEconomics/observationStatusSchema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { useEconomicsStatus } from "./useEconomicsStatus";

const shouldPoll = (value: ObservationStatus) => value.snapshot?.configuration.enabled === true;

export function useObservationStatus(
  { installationId, repositoryId }: WorkbenchScope,
  active = true,
) {
  const read = useCallback(
    (signal: AbortSignal) => fetchObservationStatus({ installationId, repositoryId }, signal),
    [installationId, repositoryId],
  );
  return useEconomicsStatus(`${installationId}:${repositoryId}`, read, shouldPoll, active);
}
