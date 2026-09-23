import { createHash } from "node:crypto";
import type {
  ObservationCommand,
  ObservationConfiguration,
  ObservationMutation,
  ObservationSnapshot,
} from "../src/api/ciEconomics/observationSchema";
import type {
  ObservationGap,
  ObservationGaps,
  ObservationScan,
  ObservationStatus,
} from "../src/api/ciEconomics/observationStatusSchema";
import type { ObservationWorkflows } from "../src/api/ciEconomics/observationWorkflowSchema";

export const OBSERVATION_SCOPE = { installationId: 1, repositoryId: 1, limit: 10 };
export const OBSERVED = "2026-09-10T10:00:00.000001Z";
export const OBSERVATION_CONFIG: ObservationConfiguration = {
  enabled: true,
  selector: { kind: "all", workflowIds: null },
  backfillDays: 1,
};
export function observationSnapshot(
  configuration = OBSERVATION_CONFIG,
  revision = 1,
): ObservationSnapshot {
  return {
    schemaVersion: "ci-economics-observation/v1",
    installationId: 1,
    repositoryId: 1,
    revision,
    configuration,
    configuredAt: "2026-09-10T09:00:00+00:00",
  };
}
export function observationScan(lane: ObservationScan["lane"] = "recent"): ObservationScan {
  return {
    lane,
    interval: null,
    window: null,
    pageNumber: null,
    cycleStartedAt: null,
    nextAttemptAt: OBSERVED,
    leaseExpiresAt: null,
    lastCompletedThrough: null,
    lastPageAt: null,
    pagesSeen: 0,
    sourcesRegistered: 0,
    lastOutcome: null,
  };
}
export function observationStatus(
  snapshot: ObservationSnapshot | null = observationSnapshot(),
): ObservationStatus {
  return {
    schemaVersion: "ci-economics-observation-status/v1",
    installationId: 1,
    repositoryId: 1,
    snapshot,
    scans: snapshot === null ? [] : [observationScan(), observationScan("backfill")],
    occupiedSourceSlots: 12,
    maximumSourceSlots: 10_000,
    detailTruncatedUntil: null,
    observedAt: OBSERVED,
  };
}
export function observationCommand(configuration = OBSERVATION_CONFIG): ObservationCommand {
  return {
    installationId: 1,
    repositoryId: 1,
    expectedRevision: 0,
    operationId: "observation-1",
    configuration,
  };
}
export function observationMutation(command = observationCommand()): ObservationMutation {
  const configuration = command.configuration;
  const canonical = {
    ...configuration,
    selector: {
      ...configuration.selector,
      workflowIds: configuration.selector.workflowIds?.toSorted((a, b) => a - b) ?? null,
    },
  };
  return {
    schemaVersion: "ci-economics-observation-mutation/v1",
    operationId: command.operationId,
    outcome: "committed",
    snapshot: observationSnapshot(canonical, command.expectedRevision + 1),
  };
}
export function observationGap(): ObservationGap {
  const gap: ObservationGap["gap"] = {
    configRevision: 1,
    createdFrom: "2026-09-09T00:00:00+00:00",
    createdThrough: "2026-09-09T01:00:00+00:00",
    cycleStartedAt: "2026-09-10T09:00:00.000001+00:00",
    installationId: 1,
    lane: "recent",
    reason: "provider_truncated",
    repositoryId: 1,
    schemaVersion: "ci-economics-observation-gap/v1",
    selectorDigest: "a".repeat(64),
  };
  return {
    gap,
    gapId: createHash("sha256").update(JSON.stringify(gap)).digest("hex"),
    expiresAt: "2026-12-09T09:00:00.000001Z",
  };
}
export function observationGaps(items = [observationGap()]): ObservationGaps {
  return {
    schemaVersion: "ci-economics-observation-gaps/v1",
    installationId: 1,
    repositoryId: 1,
    items,
    nextCursor: null,
    observedAt: OBSERVED,
  };
}
export function observationWorkflows(): ObservationWorkflows {
  return {
    schemaVersion: "ci-economics-observation-workflows/v1",
    installationId: 1,
    repositoryId: 1,
    pageNumber: 1,
    providerTotal: 2,
    termination: "exhausted",
    items: [
      {
        workflowId: 101,
        name: "Full Check",
        path: ".github/workflows/full-check.yml",
        state: "active",
      },
      {
        workflowId: 102,
        name: "Shared tests",
        path: ".github/workflows/_tests.yml",
        state: "disabled_manually",
      },
    ],
  };
}
