import { z } from "zod";
import type { components } from "../generated";
import { detailRetentionSchema, historyDatasetSchema, sameDetailRetention } from "./historySchema";
import {
  observationCounter,
  observationInstant,
  observationMicroseconds,
  observationTimestamp,
} from "./observationSchema";
import { positiveInteger, repositoryScopeSchema, sameScope } from "./sourceSchema";

export type HistoryStatus = components["schemas"]["HistoryStatusResponse"];
const boundary = observationTimestamp.refine(
  (value) => observationInstant(value)?.slice(20, 26) === "000000",
);
const scanSchema = z
  .strictObject({
    revision: positiveInteger,
    createdFrom: boundary,
    createdThrough: boundary,
    windowFrom: boundary,
    windowThrough: boundary,
    pageNumber: z.number().int().min(1).max(10),
    cycleStartedAt: observationTimestamp,
    nextAttemptAt: observationTimestamp,
    leaseExpiresAt: observationTimestamp.nullable(),
    pagesSeen: observationCounter,
    attemptsSeen: observationCounter,
    lastOutcome: z
      .enum([
        "page_recorded",
        "attempt_recorded",
        "refined",
        "replayed",
        "incomparable",
        "conflict",
        "unavailable",
        "capacity_reached",
        "provider_unavailable",
        "provider_malformed",
        "access_unavailable",
        "timed_out",
        "unselected",
        "recheck_queued",
      ])
      .nullable(),
    traversalComplete: z.boolean(),
  })
  .refine((scan) => {
    const from = observationMicroseconds(scan.createdFrom);
    const through = observationMicroseconds(scan.createdThrough);
    const start = observationMicroseconds(scan.windowFrom);
    const end = observationMicroseconds(scan.windowThrough);
    const cycle = observationMicroseconds(scan.cycleStartedAt);
    return (
      from !== undefined &&
      through !== undefined &&
      start !== undefined &&
      end !== undefined &&
      cycle !== undefined &&
      from <= start &&
      start <= end &&
      end <= through &&
      through <= cycle &&
      end - start <= 604_800_000_000n &&
      (from === through || start < end)
    );
  });

const discoverySchema = z
  .strictObject({
    recoveryFloor: boundary,
    completedThrough: boundary.nullable(),
    pendingRuns: z.number().int().min(0).max(100),
    progress: scanSchema,
  })
  .refine((discovery) => {
    const floor = observationMicroseconds(discovery.recoveryFloor);
    const from = observationMicroseconds(discovery.progress.createdFrom);
    const through = observationMicroseconds(discovery.progress.createdThrough);
    const completed =
      discovery.completedThrough === null
        ? null
        : observationMicroseconds(discovery.completedThrough);
    return (
      floor !== undefined &&
      from !== undefined &&
      through !== undefined &&
      from >= floor &&
      discovery.progress.attemptsSeen === 0 &&
      (completed === null ||
        (completed !== undefined && completed >= floor && completed <= through)) &&
      (!discovery.progress.traversalComplete ||
        (completed === through && discovery.pendingRuns === 0))
    );
  });

export const historyStatusSchema: z.ZodType<HistoryStatus> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    schemaVersion: z.literal("ci-economics-history-status/v1"),
    defaults: z.strictObject({
      revision: positiveInteger,
      detailRetention: detailRetentionSchema,
      updatedAt: observationTimestamp,
    }),
    effectiveDetailRetention: z
      .strictObject({
        source: z.enum(["service_default", "repository_override"]),
        revision: positiveInteger,
        policy: detailRetentionSchema,
      })
      .nullable(),
    snapshot: historyDatasetSchema.nullable(),
    scan: scanSchema.nullable(),
    discovery: discoverySchema.nullable(),
    pendingRechecks: z.number().int().min(0).max(256),
    observedAt: observationTimestamp,
  })
  .refine((status) => {
    const observed = observationMicroseconds(status.observedAt);
    const defaultsAt = observationMicroseconds(status.defaults.updatedAt);
    if (observed === undefined || defaultsAt === undefined || defaultsAt > observed) return false;
    const { snapshot, scan, discovery, effectiveDetailRetention: effective } = status;
    if (snapshot === null)
      return (
        scan === null && discovery === null && effective === null && status.pendingRechecks === 0
      );
    if (scan === null || discovery === null || effective === null || !sameScope(snapshot, status))
      return false;
    const configured = observationMicroseconds(snapshot.configuredAt);
    const cycle = observationMicroseconds(scan.cycleStartedAt);
    const discoveryCycle = observationMicroseconds(discovery.progress.cycleStartedAt);
    const inherited = snapshot.configuration.detailRetention === null;
    return (
      configured !== undefined &&
      cycle !== undefined &&
      discoveryCycle !== undefined &&
      discoveryCycle <= observed &&
      configured <= observed &&
      cycle <= observed &&
      effective.source === (inherited ? "service_default" : "repository_override") &&
      effective.revision ===
        (inherited ? status.defaults.revision : snapshot.configurationRevision) &&
      sameDetailRetention(
        effective.policy,
        snapshot.configuration.detailRetention ?? status.defaults.detailRetention,
      )
    );
  });
