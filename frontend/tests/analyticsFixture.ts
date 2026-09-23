import {
  type AnalyticsQuery,
  type AnalyticsReport,
  analyticsQuerySchema,
} from "../src/api/ciEconomics/analyticsSchema";

export function analyticsQuery(): AnalyticsQuery {
  return analyticsQuerySchema.parse({
    installationId: 1,
    repositoryId: 2,
    generation: 3,
    createdFrom: "2026-09-10T00:00:00Z",
    createdUntil: "2026-09-13T00:00:00Z",
  });
}
export function analyticsReport(query = analyticsQuery()): AnalyticsReport {
  const days = (Date.parse(query.createdUntil) - Date.parse(query.createdFrom)) / 86400000;
  const buckets: AnalyticsReport["buckets"] = Array.from({ length: days }, (_, index) => {
    const measured = index < 2;
    return {
      day: new Date(Date.parse(query.createdFrom) + index * 86400000).toISOString(),
      coverage: measured ? "complete_retained" : "unknown",
      runs: measured ? 1 : 0,
      attempts: measured ? 1 : 0,
      matchingAttempts: measured ? 1 : 0,
      completeAttempts: measured ? 1 : 0,
      partialAttempts: 0,
      unavailableAttempts: 0,
      conflictAttempts: 0,
      knownMissingJobs: 0,
      unknownPopulationAttempts: 0,
      conflictExcludedJobs: 0,
      selected: {
        jobs: measured ? 1 : 0,
        failures: 0,
        cancellations: 0,
        durationSamples: measured ? 1 : 0,
        queueSamples: 0,
        runnerMs: measured ? 60000 : 0,
        queueMs: 0,
        inconsistentTimings: 0,
        missingDuration: 0,
        missingQueue: measured ? 1 : 0,
        mixedJobs: 0,
        unknownPurposeJobs: measured ? 1 : 0,
      },
      observedRunnerMs: measured ? 60000 : null,
      observedQueueMs: null,
    };
  });
  return {
    schemaVersion: "ci-history-analytics/v1",
    query,
    observedAt: query.createdUntil,
    dataRevision: 5,
    configurationRevision: 4,
    datasetState: "active",
    providerCoverage: "unknown",
    unit: "milliseconds",
    mapping: null,
    mappingDigest: null,
    runs: Math.min(days, 2),
    attempts: Math.min(days, 2),
    cohort: {
      profile: "workflow_blob_job_event/v1",
      knownWorkflowVersions: 0,
      unknownWorkflowAttempts: Math.min(days, 2),
      eventCount: 1,
      definitionStable: false,
      observedDefinitionChanges: false,
      compatible: false,
      contributors: "unclassified",
    },
    buckets,
    forecast: {
      modelVersion: "expanding-daily-mean/v1",
      target: "retained_run_creation_occupancy",
      backtestBasis: "current_archive_reconstructed_chronology",
      status: "unavailable",
      reason: "incomplete_daily_coverage",
      cutoff: query.createdUntil,
      horizonDays: query.horizonDays,
      predictedRunnerMs: null,
      lowerRunnerMs: null,
      upperRunnerMs: null,
      backtestMaeMs: null,
      empiricalCoverageBps: null,
      usableDays: 0,
      durationSamples: 0,
      folds: [],
      monetaryEstimate: null,
      monetaryUnavailableReason: "no_explicit_versioned_tariff",
    },
    degradation: {
      modelVersion: "fixed-baseline-hysteresis/v1",
      status: "unavailable",
      reason: "incompatible_cohort",
      baselineUntil: null,
      baselineMeanMs: null,
      baselineSamples: 0,
      events: [],
      contributor: "unclassified",
    },
  };
}
export function mappedReport(): AnalyticsReport {
  return {
    ...analyticsReport(),
    mapping: {
      installationId: 1,
      repositoryId: 2,
      generation: 3,
      version: "v1",
      provenance: "repository policy",
      entries: [{ workflowId: 17, jobName: "Ruff", purposes: ["lint"] }],
    },
    mappingDigest: "f304d4a3fb80978e9a939fe0c40db02c0fa84e02ffabd62b1e18510657348d23",
  };
}

export function forecastReport(): AnalyticsReport {
  const query = analyticsQuerySchema.parse({
    installationId: 1,
    repositoryId: 2,
    generation: 3,
    createdFrom: "2026-07-01T00:00:00Z",
    createdUntil: "2026-09-09T00:00:00Z",
    workflowId: 17,
    jobName: "Ruff",
  });
  const source = analyticsReport(query);
  const buckets = source.buckets.map((bucket) => ({
    ...bucket,
    coverage: "complete_retained" as const,
    runs: 1,
    attempts: 1,
    matchingAttempts: 1,
    completeAttempts: 1,
    selected: {
      ...bucket.selected,
      jobs: 3,
      durationSamples: 3,
      missingQueue: 3,
      unknownPurposeJobs: 3,
      runnerMs: 180000,
    },
    observedRunnerMs: 180000,
  }));
  const folds: AnalyticsReport["forecast"]["folds"] = Array.from({ length: 8 }, (_, index) => ({
    trainingUntil: new Date(
      Date.parse(query.createdFrom) + (14 + index * 7) * 86400000,
    ).toISOString(),
    testUntil: new Date(Date.parse(query.createdFrom) + (21 + index * 7) * 86400000).toISOString(),
    predictedRunnerMs: 1260000,
    actualRunnerMs: 1260000,
    lowerRunnerMs: index < 4 ? null : 1260000,
    upperRunnerMs: index < 4 ? null : 1260000,
    phase: index < 4 ? "calibration" : "evaluation",
  }));
  return {
    ...source,
    buckets,
    runs: 70,
    attempts: 70,
    cohort: {
      ...source.cohort,
      knownWorkflowVersions: 1,
      unknownWorkflowAttempts: 0,
      definitionStable: true,
      compatible: true,
    },
    forecast: {
      ...source.forecast,
      status: "available",
      reason: "conditional_on_unchanged_collection_and_workload",
      predictedRunnerMs: 1260000,
      lowerRunnerMs: 1260000,
      upperRunnerMs: 1260000,
      backtestMaeMs: 0,
      empiricalCoverageBps: 10000,
      usableDays: 70,
      durationSamples: 210,
      folds,
    },
  };
}
