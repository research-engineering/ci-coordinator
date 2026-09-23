import { z } from "zod";
import type { components } from "../generated";
import {
  observationCounter,
  observationInstant,
  observationLane,
  observationMicroseconds,
  observationPayloadInstant,
  observationSnapshotSchema,
  observationTimestamp,
} from "./observationSchema";
import { digest, positiveInteger, repositoryScopeSchema, sameScope } from "./sourceSchema";

export type ObservationStatus = components["schemas"]["ObservationStatusResponse"];
export type ObservationScan = components["schemas"]["ObservationScanResponse"];
export type ObservationGaps = components["schemas"]["ObservationGapsResponse"];
export type ObservationGap = components["schemas"]["ObservationGapResponse"];

const windowSchema = z
  .strictObject({ createdFrom: observationTimestamp, createdThrough: observationTimestamp })
  .refine((window) => {
    const from = observationMicroseconds(window.createdFrom);
    const through = observationMicroseconds(window.createdThrough);
    return (
      from !== undefined &&
      through !== undefined &&
      from % 1_000_000n === 0n &&
      through % 1_000_000n === 0n &&
      through >= from &&
      through - from <= 604_800_000_000n
    );
  });

const scanSchema: z.ZodType<ObservationScan> = z
  .strictObject({
    lane: observationLane,
    interval: windowSchema.nullable(),
    window: windowSchema
      .refine((window) => {
        const from = observationMicroseconds(window.createdFrom);
        const through = observationMicroseconds(window.createdThrough);
        return from !== undefined && through !== undefined && through - from <= 21_600_000_000n;
      })
      .nullable(),
    pageNumber: z.number().int().min(1).max(10).nullable(),
    cycleStartedAt: observationTimestamp.nullable(),
    nextAttemptAt: observationTimestamp,
    leaseExpiresAt: observationTimestamp.nullable(),
    lastCompletedThrough: observationTimestamp.nullable(),
    lastPageAt: observationTimestamp.nullable(),
    pagesSeen: observationCounter,
    sourcesRegistered: observationCounter,
    lastOutcome: z
      .enum([
        "provider_unavailable",
        "provider_binding_mismatch",
        "provider_malformed",
        "provider_incomplete",
        "provider_not_terminal",
        "provider_unstable",
        "access_unavailable",
        "timed_out",
        "page_recorded",
        "capacity_reached",
      ])
      .nullable(),
  })
  .refine((scan) => {
    if (!Number.isSafeInteger(scan.pagesSeen) || !Number.isSafeInteger(scan.sourcesRegistered))
      return false;
    const cursor = [scan.interval, scan.window, scan.pageNumber, scan.cycleStartedAt];
    if (
      !cursor.every((value) => (value === null) === (scan.interval === null)) ||
      (scan.leaseExpiresAt !== null && scan.interval === null) ||
      (scan.pagesSeen === 0) !== (scan.lastPageAt === null) ||
      BigInt(scan.sourcesRegistered) > 100n * BigInt(scan.pagesSeen) ||
      (scan.pagesSeen > 0 && scan.lastOutcome === null)
    )
      return false;
    if (scan.interval && scan.window && scan.cycleStartedAt) {
      const from = observationInstant(scan.interval.createdFrom);
      const through = observationInstant(scan.interval.createdThrough);
      const start = observationInstant(scan.window.createdFrom);
      const end = observationInstant(scan.window.createdThrough);
      const cycle = observationInstant(scan.cycleStartedAt);
      if (
        !from ||
        !through ||
        !start ||
        !end ||
        !cycle ||
        from > start ||
        end > through ||
        through > cycle
      )
        return false;
    }
    if (scan.lastCompletedThrough !== null) {
      const completed = observationMicroseconds(scan.lastCompletedThrough);
      const recorded =
        scan.lastPageAt === null ? undefined : observationMicroseconds(scan.lastPageAt);
      if (
        completed === undefined ||
        recorded === undefined ||
        completed % 1_000_000n !== 0n ||
        completed > recorded
      )
        return false;
    }
    return true;
  });

export const observationStatusSchema: z.ZodType<ObservationStatus> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    schemaVersion: z.literal("ci-economics-observation-status/v1"),
    snapshot: observationSnapshotSchema.nullable(),
    scans: z.array(scanSchema).max(2),
    occupiedSourceSlots: z.number().int().min(0).max(10_000),
    maximumSourceSlots: z.literal(10_000),
    detailTruncatedUntil: observationTimestamp.nullable(),
    observedAt: observationTimestamp,
  })
  .refine((status) =>
    status.snapshot === null
      ? status.scans.length === 0 && status.detailTruncatedUntil === null
      : sameScope(status, status.snapshot) &&
        status.scans.length === 2 &&
        new Set(status.scans.map((scan) => scan.lane)).size === 2,
  );

const gapSchema: z.ZodType<ObservationGap> = z
  .strictObject({
    gapId: digest,
    expiresAt: observationTimestamp,
    gap: z.strictObject({
      ...repositoryScopeSchema.shape,
      schemaVersion: z.literal("ci-economics-observation-gap/v1"),
      configRevision: positiveInteger,
      selectorDigest: digest,
      lane: observationLane,
      cycleStartedAt: observationTimestamp,
      createdFrom: observationTimestamp,
      createdThrough: observationTimestamp,
      reason: z.enum([
        "provider_truncated",
        "outside_source_window",
        "source_conflict",
        "outage_window_lost",
      ]),
    }),
  })
  .refine((item) => {
    const from = observationMicroseconds(item.gap.createdFrom);
    const through = observationMicroseconds(item.gap.createdThrough);
    const cycle = observationMicroseconds(item.gap.cycleStartedAt);
    const expires = observationMicroseconds(item.expiresAt);
    return (
      from !== undefined &&
      through !== undefined &&
      cycle !== undefined &&
      expires !== undefined &&
      from <= through &&
      through <= cycle &&
      expires - cycle === 7_776_000_000_000n
    );
  });

export function parseObservationGapCursor(value: string) {
  const match = /^([1-9][0-9]{0,15})\.([1-9][0-9]{0,15})\.([1-9][0-9]{0,15})\.([0-9a-f]{64})$/.exec(
    value,
  );
  if (match === null || match[0] !== value || match[4] === undefined) return undefined;
  const installationId = Number(match[1]);
  const repositoryId = Number(match[2]);
  const configRevision = Number(match[3]);
  if (![installationId, repositoryId, configRevision].every(Number.isSafeInteger)) return undefined;
  return { installationId, repositoryId, configRevision, gapId: match[4] };
}

const observationGapCursorSchema = z
  .string()
  .max(115)
  .refine((value) => parseObservationGapCursor(value) !== undefined);

export const observationGapsSchema: z.ZodType<ObservationGaps> = z
  .strictObject({
    ...repositoryScopeSchema.shape,
    schemaVersion: z.literal("ci-economics-observation-gaps/v1"),
    items: z.array(gapSchema).max(50),
    nextCursor: observationGapCursorSchema.nullable(),
    observedAt: observationTimestamp,
  })
  .refine((page) => {
    const observed = observationInstant(page.observedAt);
    const cursor =
      page.nextCursor === null ? undefined : parseObservationGapCursor(page.nextCursor);
    return (
      observed !== undefined &&
      (page.nextCursor === null ||
        (cursor !== undefined &&
          sameScope(cursor, page) &&
          cursor.gapId === page.items.at(-1)?.gapId)) &&
      page.items.every((item, index) => {
        const expires = observationInstant(item.expiresAt);
        return (
          sameScope(item.gap, page) &&
          expires !== undefined &&
          expires > observed &&
          (index === 0 || item.gapId > (page.items[index - 1]?.gapId ?? item.gapId))
        );
      })
    );
  });

export async function observationGapIdentityIsCanonical(item: ObservationGap): Promise<boolean> {
  const gap = item.gap;
  const canonical = JSON.stringify({
    configRevision: gap.configRevision,
    createdFrom: observationPayloadInstant(gap.createdFrom),
    createdThrough: observationPayloadInstant(gap.createdThrough),
    cycleStartedAt: observationPayloadInstant(gap.cycleStartedAt),
    installationId: gap.installationId,
    lane: gap.lane,
    reason: gap.reason,
    repositoryId: gap.repositoryId,
    schemaVersion: gap.schemaVersion,
    selectorDigest: gap.selectorDigest,
  });
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return (
    Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("") ===
    item.gapId
  );
}
