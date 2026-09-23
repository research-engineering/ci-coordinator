import { z } from "zod";
import type { components } from "../generated";
import { MAX_WORKBENCH_SECTION_ITEMS } from "./limits";

export type WorkbenchSnapshot = components["schemas"]["WorkbenchSnapshotResponse"];

const MAX_NESTED_ITEMS = 1_000;
const text = z.string().max(65_536);
const identifier = z.string().min(1).max(512);
const safeInteger = z.number().int().safe();
const positiveInteger = safeInteger.positive();
const nonNegativeInteger = safeInteger.nonnegative();
const dateTime = z.iso.datetime({ offset: true });
const jsonValue = z.custom<components["schemas"]["JsonValue"]>(isBoundedJsonValue, {
  message: "payload is not bounded JSON",
});

const shardedProfileCapacity = z.strictObject({
  capacityMode: z.enum(["optimized", "conservative"]),
  capacityReason: text.nullable(),
  executionKind: z.literal("witness-shards"),
  maxParallel: nonNegativeInteger,
  profileId: identifier,
  shardIds: z.array(identifier).max(MAX_NESTED_ITEMS),
  testCount: nonNegativeInteger,
  witnessCount: nonNegativeInteger,
});

const nativeProfileExecution = z.strictObject({
  executionKind: z.literal("native-job-set"),
  jobId: identifier,
  profileId: identifier,
  witnessCount: nonNegativeInteger,
});

const profileCapacity = z.discriminatedUnion("executionKind", [
  shardedProfileCapacity,
  nativeProfileExecution,
]);

const plan = z.strictObject({
  baseSha: identifier,
  catalogHash: identifier.nullable(),
  eventName: identifier,
  executionMode: z.enum(["selected", "full-ci"]),
  expiresAt: dateTime,
  fallbackReason: text.nullable(),
  headSha: identifier,
  issuedAt: dateTime,
  omittedObligationIds: z.array(identifier).max(MAX_NESTED_ITEMS),
  planId: identifier,
  productionAdmissionReceiptId: identifier.nullable(),
  profiles: z.array(profileCapacity).max(MAX_NESTED_ITEMS),
  recordId: identifier,
  ref: identifier,
  requestId: identifier,
  runAttempt: positiveInteger,
  selectedObligationIds: z.array(identifier).max(MAX_NESTED_ITEMS),
  selectedWitnessIds: z.array(identifier).max(MAX_NESTED_ITEMS),
  targetRegistryHash: identifier.nullable(),
  testManifestId: identifier.nullable(),
  verifiedPlanId: identifier.nullable(),
  workflowRunId: positiveInteger,
});

const runFinding = z.strictObject({
  kind: identifier,
  message: text,
  signalId: identifier.nullable(),
});

const run = z.strictObject({
  attemptCount: nonNegativeInteger,
  baseSha: identifier,
  claimGeneration: nonNegativeInteger,
  contractHash: identifier,
  createdAt: dateTime,
  deadlineAt: dateTime,
  eventName: identifier,
  findings: z.array(runFinding).max(MAX_NESTED_ITEMS),
  headSha: identifier,
  leaseActive: z.boolean(),
  leaseExpiresAt: dateTime.nullable(),
  maxAttempts: positiveInteger,
  nextAttemptAt: dateTime,
  ref: identifier,
  revision: nonNegativeInteger,
  runAttempt: positiveInteger,
  state: z.enum(["pending", "success", "failure", "conflict"]),
  subjectId: identifier,
  workflowRunId: positiveInteger,
});

const override = z.strictObject({
  active: z.boolean(),
  actor: identifier,
  appliedAt: dateTime,
  auditEventId: identifier,
  expiresAt: dateTime.nullable(),
  kind: z.enum(["force_full_ci", "disable_omission", "enable_omission"]),
  operationId: identifier,
  overrideId: identifier,
  reason: text,
  subjectId: identifier.nullable(),
});

const configEpoch = z.strictObject({
  active: z.boolean(),
  activeRevision: positiveInteger.nullable(),
  compiledSchemaId: identifier,
  documentHash: identifier,
  documentProfileId: identifier,
  documentSchemaId: identifier,
  epochHash: identifier,
  epochId: identifier,
  semanticProfileId: identifier,
  sourceFormat: z.enum(["json", "yaml-1.2"]),
  sourceHash: identifier,
});

const auditEvent = z.strictObject({
  actor: identifier,
  auditEventId: identifier,
  createdAt: text,
  eventHash: identifier,
  eventType: identifier,
  payload: jsonValue,
  payloadHash: identifier,
  previousEventHash: identifier.nullable(),
  sequence: nonNegativeInteger,
  subjectId: identifier,
  subjectType: identifier,
});

export const workbenchSnapshotSchema: z.ZodType<WorkbenchSnapshot> = z
  .strictObject({
    auditEvents: z.array(auditEvent).max(MAX_WORKBENCH_SECTION_ITEMS),
    configEpochs: z.array(configEpoch).max(MAX_WORKBENCH_SECTION_ITEMS),
    ledgerRevision: nonNegativeInteger,
    observedAt: dateTime,
    ok: z.literal(true),
    overrides: z.array(override).max(MAX_WORKBENCH_SECTION_ITEMS),
    plans: z.array(plan).max(MAX_WORKBENCH_SECTION_ITEMS),
    replay: z.strictObject({
      reason: text.nullable(),
      snapshotRevision: nonNegativeInteger,
      status: z.enum(["valid", "in_progress", "invalid", "unavailable"]),
      verifiedRevision: nonNegativeInteger.nullable(),
    }),
    runs: z.array(run).max(MAX_WORKBENCH_SECTION_ITEMS),
    scope: z.strictObject({
      installationId: positiveInteger,
      repositoryId: positiveInteger,
    }),
    truncated: z.strictObject({
      auditEvents: z.boolean(),
      configEpochs: z.boolean(),
      overrides: z.boolean(),
      plans: z.boolean(),
      runs: z.boolean(),
    }),
  })
  .superRefine((value, context) => {
    const active = value.configEpochs.filter((epoch) => epoch.active);
    if (active.length > 1) {
      context.addIssue({ code: "custom", message: "snapshot contains multiple active epochs" });
    }
    if (value.configEpochs.some((epoch) => epoch.active !== (epoch.activeRevision !== null))) {
      context.addIssue({ code: "custom", message: "active epoch revision is inconsistent" });
    }
  });

function isBoundedJsonValue(value: unknown): value is components["schemas"]["JsonValue"] {
  const pending: Array<{ depth: number; value: unknown }> = [{ depth: 0, value }];
  let nodes = 0;
  while (pending.length > 0) {
    const current = pending.pop();
    if (!current || current.depth > 32 || ++nodes > 10_000) return false;
    const candidate = current.value;
    if (candidate === null || typeof candidate === "boolean") continue;
    if (typeof candidate === "string") {
      if (candidate.length > 65_536) return false;
      continue;
    }
    if (typeof candidate === "number") {
      if (!Number.isFinite(candidate) || !Number.isSafeInteger(candidate)) return false;
      continue;
    }
    if (Array.isArray(candidate)) {
      if (candidate.length > MAX_NESTED_ITEMS) return false;
      for (const item of candidate) pending.push({ depth: current.depth + 1, value: item });
      continue;
    }
    if (typeof candidate !== "object") return false;
    const entries = Object.entries(candidate);
    if (entries.length > MAX_NESTED_ITEMS) return false;
    for (const [key, item] of entries) {
      if (key.length > 512) return false;
      pending.push({ depth: current.depth + 1, value: item });
    }
  }
  return true;
}
