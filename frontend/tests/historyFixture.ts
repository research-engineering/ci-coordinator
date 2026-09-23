import type {
  HistoryCommand,
  HistoryConfiguration,
  HistoryDataset,
  HistoryMutation,
} from "../src/api/ciEconomics/historySchema";
import type { HistoryStatus } from "../src/api/ciEconomics/historyStatusSchema";

export const HISTORY_SCOPE = { installationId: 1, repositoryId: 1, limit: 10 };
export const HISTORY_TIME = "2026-09-12T10:00:00+00:00";

export function historyConfiguration(): HistoryConfiguration {
  return {
    enabled: true,
    workflowIds: null,
    detailRetention: null,
    quota: { attempts: 10_000, jobs: 100_000, gaps: 1000, canonicalBytes: 268_435_456 },
  };
}

export function historyDataset(
  configuration = historyConfiguration(),
  revision = 1,
): HistoryDataset {
  return {
    schemaVersion: "ci-economics-history-dataset/v1",
    installationId: 1,
    repositoryId: 1,
    generation: 1,
    configurationRevision: revision,
    dataRevision: 1,
    configuredAt: HISTORY_TIME,
    state: configuration.enabled ? "active" : "paused",
    configuration,
    usage: { attempts: 42, jobs: 210, gaps: 1, canonicalBytes: 4096 },
  };
}

export function historyCommand(): HistoryCommand {
  return {
    installationId: 1,
    repositoryId: 1,
    expectedRevision: 0,
    configuration: historyConfiguration(),
    initialCreatedFrom: "2020-01-01T00:00:00Z",
    rescan: false,
    operationId: "history-operation",
  };
}

export function historyMutation(command = historyCommand()): HistoryMutation {
  return {
    schemaVersion: "ci-economics-history-mutation/v1",
    operationId: command.operationId,
    outcome: "committed",
    snapshot: {
      ...historyDataset(command.configuration, command.expectedRevision + 1),
      installationId: command.installationId,
      repositoryId: command.repositoryId,
    },
  };
}

export function historyStatus(snapshot: HistoryDataset | null = historyDataset()): HistoryStatus {
  const defaults: HistoryStatus["defaults"] = {
    revision: 1,
    detailRetention: { mode: "days", days: 365, anchor: "first_successful_detail_import" },
    updatedAt: HISTORY_TIME,
  };
  return {
    schemaVersion: "ci-economics-history-status/v1",
    installationId: snapshot?.installationId ?? 1,
    repositoryId: snapshot?.repositoryId ?? 1,
    defaults,
    effectiveDetailRetention:
      snapshot === null
        ? null
        : {
            source:
              snapshot.configuration.detailRetention === null
                ? "service_default"
                : "repository_override",
            revision:
              snapshot.configuration.detailRetention === null
                ? defaults.revision
                : snapshot.configurationRevision,
            policy: snapshot.configuration.detailRetention ?? defaults.detailRetention,
          },
    snapshot,
    scan:
      snapshot === null
        ? null
        : {
            revision: 1,
            createdFrom: "2020-01-01T00:00:00Z",
            createdThrough: "2026-09-12T10:00:00Z",
            windowFrom: "2020-01-01T00:00:00Z",
            windowThrough: "2020-01-08T00:00:00Z",
            pageNumber: 1,
            cycleStartedAt: HISTORY_TIME,
            nextAttemptAt: HISTORY_TIME,
            leaseExpiresAt: null,
            pagesSeen: 3,
            attemptsSeen: 42,
            lastOutcome: "attempt_recorded",
            traversalComplete: false,
          },
    pendingRechecks: snapshot === null ? 0 : 2,
    discovery:
      snapshot === null
        ? null
        : {
            recoveryFloor: HISTORY_TIME,
            completedThrough: null,
            pendingRuns: 0,
            progress: {
              revision: 1,
              createdFrom: HISTORY_TIME,
              createdThrough: HISTORY_TIME,
              windowFrom: HISTORY_TIME,
              windowThrough: HISTORY_TIME,
              pageNumber: 1,
              cycleStartedAt: HISTORY_TIME,
              nextAttemptAt: HISTORY_TIME,
              leaseExpiresAt: null,
              pagesSeen: 0,
              attemptsSeen: 0,
              lastOutcome: null,
              traversalComplete: false,
            },
          },
    observedAt: HISTORY_TIME,
  };
}
