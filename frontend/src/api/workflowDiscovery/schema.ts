import { z } from "zod";
import type { components } from "../generated";
import { SUPPORTED_CI_EVENTS } from "./events";
import { reviewableProposalMismatches } from "./policyProof";

export type WorkflowDiscoveryReport = components["schemas"]["WorkflowDiscoveryResponse"];
export type WorkflowDiscoveryError = components["schemas"]["WorkflowDiscoveryErrorResponse"];

const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const MAX_TEXT_BYTES = 4_096;
const MAX_WORKFLOW_PATH_BYTES = 512;
const MAX_WORKFLOW_FILES = 64;
const MAX_WORKFLOW_FILE_BYTES = 262_144;
const MAX_REPORT_JOBS = 1_024;
const MAX_CALL_EDGES = 4_096;
const MAX_EVIDENCE_ITEMS = 32_768;
const MAX_COLLECTION_ITEMS = 20_000;
const MAX_POLICY_SOURCE_BYTES = 1_572_864;

const exactSha1 = z.string().regex(/^[0-9a-f]{40}$/);
const exactSha256 = z.string().regex(/^[0-9a-f]{64}$/);
const positiveInteger = z.number().int().min(1).max(MAX_SAFE_INTEGER);
const nonNegativeInteger = z.number().int().min(0).max(MAX_SAFE_INTEGER);
const identifier = boundedText(128);
const text = boundedText(MAX_TEXT_BYTES);
const nullableText = text.nullable();
const workflowPath = boundedText(MAX_WORKFLOW_PATH_BYTES).refine(
  (value) => /^\.github\/workflows\/[^/]+\.(?:yml|yaml)$/.test(value),
  "workflow path is not admitted",
);
const category = z.enum(["identity", "invocation", "graph", "authority", "execution", "semantics"]);
const criticality = z.enum(["informational", "safety"]);
const policyEvent = z.enum(SUPPORTED_CI_EVENTS);
const PROPOSAL_GENERATOR_VERSION = "workflow-discovery-proposal/v2";
const scalar = z.union([
  boundedText(MAX_TEXT_BYTES, true),
  z.number().finite().min(-MAX_SAFE_INTEGER).max(MAX_SAFE_INTEGER),
  z.boolean(),
  z.null(),
]);
const factValue = z.custom<components["schemas"]["FactValue"]>(isFactValue, {
  message: "fact value escaped its bounded scalar-array algebra",
});

const scope = z.strictObject({ installationId: positiveInteger, repositoryId: positiveInteger });
const source = z.strictObject({
  blobSha: exactSha1,
  path: workflowPath,
  size: nonNegativeInteger.max(MAX_WORKFLOW_FILE_BYTES),
});
const location = z.strictObject({
  column: positiveInteger,
  line: positiveInteger,
  path: boundedText(1_024, true),
});
const provenance = z.strictObject({
  blobSha: exactSha1,
  location,
  parserVersion: identifier,
  revision: exactSha1,
  scope,
  workflowPath,
});

const permissions = z
  .strictObject({
    allLevel: z.enum(["read-all", "write-all"]).nullable(),
    entries: canonicalObjects(
      z.strictObject({ level: boundedText(128), name: boundedText(128) }),
      (entry) => entry.name,
    ),
    kind: z.enum(["absent", "all", "entries"]),
  })
  .superRefine((value, context) => {
    const aggregateValid =
      value.kind === "all"
        ? value.allLevel !== null && value.entries.length === 0
        : value.allLevel === null;
    const entriesValid = value.kind === "entries" || value.entries.length === 0;
    if (!aggregateValid || !entriesValid) {
      context.addIssue({ code: "custom", message: "declared permissions are contradictory" });
    }
  });

const concurrency = z.strictObject({
  cancelInProgress: z.boolean().nullable(),
  group: nullableText,
});
const matrixDimension = z.strictObject({
  name: boundedText(128),
  values: z.array(scalar).min(1).max(256),
});
const step = z
  .strictObject({
    condition: nullableText,
    hasRun: z.boolean(),
    index: nonNegativeInteger,
    name: nullableText,
    uses: nullableText,
    usesDynamic: z.boolean(),
  })
  .superRefine((value, context) => {
    if (value.usesDynamic && value.uses === null) {
      context.addIssue({ code: "custom", message: "dynamic action syntax is missing" });
    }
  });

const job = z
  .strictObject({
    concurrency: concurrency.nullable(),
    condition: nullableText,
    environment: nullableText,
    jobId: boundedText(128),
    matrix: canonicalObjects(matrixDimension, (entry) => entry.name).nullable(),
    name: nullableText,
    needs: canonicalTextArray().nullable(),
    permissions: permissions.nullable(),
    providerSignalName: nullableText,
    runsOn: canonicalTextArray().nullable(),
    serviceIds: canonicalTextArray().nullable(),
    staticSecretNames: canonicalTextArray().nullable(),
    steps: z.array(step).max(MAX_COLLECTION_ITEMS).nullable(),
    subjectId: boundedText(128),
    timeoutMinutes: z.number().int().min(1).max(2_147_483_647).nullable(),
    uses: nullableText,
    usesDynamic: z.boolean(),
  })
  .superRefine((value, context) => {
    if (value.usesDynamic && value.uses === null) {
      context.addIssue({ code: "custom", message: "dynamic workflow call syntax is missing" });
    }
    if (value.steps?.some((item, index) => item.index !== index)) {
      context.addIssue({ code: "custom", message: "step indexes are not contiguous" });
    }
  });

const workflow = z.strictObject({
  concurrency: concurrency.nullable(),
  jobs: canonicalObjects(job, (entry) => entry.jobId, MAX_REPORT_JOBS),
  name: nullableText,
  path: workflowPath,
  permissions: permissions.nullable(),
  staticSecretNames: canonicalTextArray().nullable(),
  subjectId: boundedText(128),
  triggers: canonicalTextArray().nullable(),
});

const fact = z.strictObject({
  category,
  criticality,
  factId: z.string().regex(/^fact:[0-9a-f]{32}$/),
  field: identifier,
  provenance,
  subjectId: boundedText(128),
  value: factValue,
});
const unknown = z.strictObject({
  category,
  criticality,
  field: identifier,
  observedSyntax: nullableText,
  provenance,
  reason: identifier,
  subjectId: boundedText(128),
  unknownId: z.string().regex(/^unknown:[0-9a-f]{32}$/),
});
const callEdge = z
  .strictObject({
    callerJobId: boundedText(128),
    callerWorkflowPath: workflowPath,
    edgeId: z.string().regex(/^edge:[0-9a-f]{32}$/),
    kind: z.enum(["local", "remote", "dynamic", "unknown"]),
    provenance,
    remoteRef: nullableText,
    remoteRefImmutable: z.boolean().nullable(),
    status: z.enum([
      "resolved",
      "missing",
      "remote",
      "dynamic",
      "invalid",
      "cycle",
      "depth_exceeded",
    ]),
    targetPath: workflowPath.nullable(),
    uses: text,
  })
  .superRefine((value, context) => {
    if (value.provenance.location.path !== "job.uses") {
      context.addIssue({ code: "custom", message: "call edge lacks job.uses provenance" });
    }
    if (value.provenance.workflowPath !== value.callerWorkflowPath) {
      context.addIssue({ code: "custom", message: "call edge provenance escaped its caller" });
    }
  });

const proposalNonClaims = z.tuple([
  z.literal("proposal is not active policy"),
  z.literal("proposal is not omission authority"),
  z.literal("proposal is not owner approval"),
  z.literal("proposal is not proof of FullCI completeness"),
  z.literal("proposal is not provider mutation authority"),
]);

const proposal = z
  .strictObject({
    admittedEpochId: exactSha256.nullable(),
    blockers: canonicalTextArray(MAX_EVIDENCE_ITEMS),
    diagnostics: z
      .array(
        z.strictObject({
          code: identifier,
          instancePointer: text,
          phase: identifier,
          ruleId: identifier,
        }),
      )
      .max(MAX_EVIDENCE_ITEMS),
    generatorVersion: z.literal(PROPOSAL_GENERATOR_VERSION),
    inventoryDigest: exactSha256,
    manifestId: z.string().regex(/^proposal:[0-9a-f]{32}$/),
    nonClaims: proposalNonClaims,
    policySource: boundedText(MAX_POLICY_SOURCE_BYTES, true).nullable(),
    selectedEvents: z
      .array(policyEvent)
      .max(SUPPORTED_CI_EVENTS.length)
      .superRefine((value, context) => requireCanonicalKeys(value, context)),
    selectedJobId: nullableText,
    selectedJobName: nullableText,
    selectedWorkflowPath: workflowPath.nullable(),
    state: z.enum(["reviewable", "blocked"]),
    unknownIds: canonicalTextArray(MAX_EVIDENCE_ITEMS),
  })
  .superRefine((value, context) => {
    const selectionComplete =
      value.selectedWorkflowPath !== null &&
      value.selectedJobId !== null &&
      value.selectedJobName !== null &&
      value.selectedEvents.length > 0;
    if (
      value.state === "reviewable" &&
      (!selectionComplete ||
        value.policySource === null ||
        value.admittedEpochId === null ||
        value.blockers.length > 0 ||
        value.diagnostics.length > 0)
    ) {
      context.addIssue({ code: "custom", message: "reviewable proposal is incomplete" });
    }
    if (
      value.state === "blocked" &&
      (value.policySource !== null || value.admittedEpochId !== null || value.blockers.length === 0)
    ) {
      context.addIssue({ code: "custom", message: "blocked proposal is contradictory" });
    }
  });

const adoptionNonClaims = z.tuple([
  z.literal("assessment is not omission authority"),
  z.literal("assessment is not owner approval"),
  z.literal("assessment is not production admission"),
  z.literal("assessment is not provider inventory proof"),
  z.literal("assessment is not runtime behavior proof"),
]);

const adoptionAssessment = z
  .strictObject({
    blockers: canonicalTextArray(MAX_EVIDENCE_ITEMS),
    inventoryDigest: exactSha256,
    nonClaims: adoptionNonClaims,
    recommendedAdapter: z.enum(["in_place_job_set", "reusable_workflow_set", "full_only", "none"]),
    requiredOwnerInputs: canonicalTextArray(MAX_EVIDENCE_ITEMS),
    state: z.enum([
      "in_place_job_set",
      "reusable_workflow_set",
      "witness_shards",
      "full_only",
      "invalid",
    ]),
    workflowPath,
  })
  .superRefine((value, context) => {
    const selectable =
      value.state === "in_place_job_set" || value.state === "reusable_workflow_set";
    if (
      selectable &&
      (value.recommendedAdapter !== value.state ||
        value.blockers.length > 0 ||
        value.requiredOwnerInputs.length > 0)
    ) {
      context.addIssue({ code: "custom", message: "selectable adoption is contradictory" });
    }
    if (
      value.state === "witness_shards" &&
      (value.recommendedAdapter !== "none" ||
        value.blockers.length > 0 ||
        value.requiredOwnerInputs.length > 0)
    ) {
      context.addIssue({ code: "custom", message: "witness-shard adoption is contradictory" });
    }
    if (
      value.state === "full_only" &&
      (value.recommendedAdapter === "none" ||
        value.blockers.length === 0 ||
        value.requiredOwnerInputs.length === 0)
    ) {
      context.addIssue({ code: "custom", message: "full-only adoption is contradictory" });
    }
    if (
      value.state === "invalid" &&
      (value.recommendedAdapter !== "none" ||
        value.blockers.length === 0 ||
        value.requiredOwnerInputs.length === 0)
    ) {
      context.addIssue({ code: "custom", message: "invalid adoption is contradictory" });
    }
  });

const targetProjection = z
  .strictObject({
    registryHash: exactSha256.nullable(),
    status: z.enum(["absent", "available", "invalid", "unavailable"]),
  })
  .superRefine((value, context) => {
    if ((value.status === "available") !== (value.registryHash !== null)) {
      context.addIssue({
        code: "custom",
        message: "target projection status and registry hash are incoherent",
      });
    }
  });

export const workflowDiscoveryErrorSchema: z.ZodType<WorkflowDiscoveryError> = z.strictObject({
  error: z.enum([
    "forbidden",
    "invalid_revision",
    "malformed_provider_response",
    "not_found",
    "overloaded",
    "provider_binding_mismatch",
    "rate_limited",
    "report_limit_exceeded",
    "source_limit_exceeded",
    "source_tree_limit_exceeded",
    "unauthenticated",
    "unavailable",
  ]),
  ok: z.literal(false),
});

export const workflowDiscoverySchema: z.ZodType<WorkflowDiscoveryReport> = z
  .strictObject({
    adoptionAssessments: canonicalObjects(
      adoptionAssessment,
      (entry) => entry.workflowPath,
      MAX_WORKFLOW_FILES,
    ),
    callEdges: canonicalObjects(callEdge, (entry) => entry.edgeId, MAX_CALL_EDGES),
    complete: z.boolean(),
    facts: canonicalObjects(fact, (entry) => entry.factId, MAX_EVIDENCE_ITEMS),
    inventoryDigest: exactSha256,
    localGraphClosed: z.boolean(),
    nonClaims: canonicalTextArray(MAX_EVIDENCE_ITEMS).min(1),
    ok: z.literal(true),
    parserVersion: identifier,
    proposal,
    repository: z.strictObject({
      defaultBranch: boundedText(1_024),
      name: boundedText(512),
      owner: boundedText(512),
      scope,
    }),
    revision: exactSha1,
    sources: canonicalObjects(source, (entry) => entry.path, MAX_WORKFLOW_FILES),
    targetProjection,
    unknowns: canonicalObjects(unknown, (entry) => entry.unknownId, MAX_EVIDENCE_ITEMS),
    workflows: canonicalObjects(workflow, (entry) => entry.path, MAX_WORKFLOW_FILES),
  })
  .superRefine((value, context) => {
    if (
      !sameValues(
        value.sources.map((entry) => entry.path),
        value.adoptionAssessments.map((entry) => entry.workflowPath),
      ) ||
      value.adoptionAssessments.some((entry) => entry.inventoryDigest !== value.inventoryDigest)
    ) {
      context.addIssue({
        code: "custom",
        message: "adoption assessments do not totally bind the report",
      });
    }
    if (value.proposal.inventoryDigest !== value.inventoryDigest) {
      context.addIssue({ code: "custom", message: "proposal does not bind the report" });
    }
    const unknownIds = value.unknowns.map((entry) => entry.unknownId);
    if (!sameValues(value.proposal.unknownIds, unknownIds)) {
      context.addIssue({ code: "custom", message: "proposal unknown ledger is incomplete" });
    }
    if (value.proposal.state === "reviewable") {
      const mismatches = reviewableProposalMismatches(value);
      if (mismatches.selection) {
        context.addIssue({
          code: "custom",
          message: "reviewable proposal contradicts its selection",
        });
      }
      if (mismatches.policy) {
        context.addIssue({
          code: "custom",
          message: "reviewable proposal policy source contradicts its selection",
        });
      }
    }
    if (value.workflows.reduce((total, item) => total + item.jobs.length, 0) > MAX_REPORT_JOBS) {
      context.addIssue({ code: "custom", message: "report exceeds its aggregate job bound" });
    }
    const sourceByPath = new Map(value.sources.map((entry) => [entry.path, entry.blobSha]));
    if (value.workflows.some((entry) => !sourceByPath.has(entry.path))) {
      context.addIssue({ code: "custom", message: "workflow escaped the source snapshot" });
    }
    const callJobs = new Map(
      value.workflows.flatMap((workflowEntry) =>
        workflowEntry.jobs
          .filter((jobEntry) => jobEntry.uses !== null)
          .map(
            (jobEntry) => [callCoordinate(workflowEntry.path, jobEntry.jobId), jobEntry] as const,
          ),
      ),
    );
    const edgeByCoordinate = new Map(
      value.callEdges.map((entry) => [
        callCoordinate(entry.callerWorkflowPath, entry.callerJobId),
        entry,
      ]),
    );
    if (
      edgeByCoordinate.size !== value.callEdges.length ||
      callJobs.size !== edgeByCoordinate.size ||
      [...callJobs].some(([coordinate, jobEntry]) => {
        const edge = edgeByCoordinate.get(coordinate);
        return (
          !edge || edge.uses !== jobEntry.uses || jobEntry.usesDynamic !== (edge.kind === "dynamic")
        );
      })
    ) {
      context.addIssue({ code: "custom", message: "call graph is not closed over job uses" });
    }
    const expectedGraphClosed = value.callEdges.every(
      (entry) => entry.kind === "remote" || (entry.kind === "local" && entry.status === "resolved"),
    );
    if (value.localGraphClosed !== expectedGraphClosed) {
      context.addIssue({ code: "custom", message: "local graph closure contradicts its edges" });
    }
    const provenanceRecords = [
      ...value.facts.map((entry) => entry.provenance),
      ...value.unknowns.map((entry) => entry.provenance),
      ...value.callEdges.map((entry) => entry.provenance),
    ];
    if (
      provenanceRecords.some(
        (entry) =>
          entry.revision !== value.revision ||
          entry.parserVersion !== value.parserVersion ||
          entry.scope.installationId !== value.repository.scope.installationId ||
          entry.scope.repositoryId !== value.repository.scope.repositoryId ||
          sourceByPath.get(entry.workflowPath) !== entry.blobSha,
      )
    ) {
      context.addIssue({ code: "custom", message: "evidence provenance escaped the snapshot" });
    }
    if (
      value.complete &&
      !sameValues(
        value.sources.map((entry) => entry.path),
        value.workflows.map((entry) => entry.path),
      )
    ) {
      context.addIssue({ code: "custom", message: "complete report omits a workflow" });
    }
  });

function boundedText(maximumBytes: number, allowEmpty = false) {
  return z
    .string()
    .max(maximumBytes)
    .refine((value) => allowEmpty || value.length > 0, "text is empty")
    .refine(isUnicodeScalarText, "text contains an unpaired surrogate")
    .refine(
      (value) => new TextEncoder().encode(value).byteLength <= maximumBytes,
      "text exceeds its byte bound",
    );
}

function canonicalTextArray(maximumItems = MAX_COLLECTION_ITEMS) {
  return z
    .array(text)
    .max(maximumItems)
    .superRefine((value, context) => requireCanonicalKeys(value, context));
}

function canonicalObjects<T extends z.ZodTypeAny>(
  item: T,
  key: (value: z.output<T>) => string,
  maximumItems = MAX_COLLECTION_ITEMS,
) {
  return z
    .array(item)
    .max(maximumItems)
    .superRefine((value, context) => requireCanonicalKeys(value.map(key), context));
}

function requireCanonicalKeys(values: readonly string[], context: z.RefinementCtx): void {
  if (
    values.some((value, index) => {
      const previous = values[index - 1];
      return previous !== undefined && previous >= value;
    })
  ) {
    context.addIssue({ code: "custom", message: "collection is not unique and canonical" });
  }
}

function sameValues(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function callCoordinate(workflowPathValue: string, jobId: string): string {
  return JSON.stringify([workflowPathValue, jobId]);
}

function isUnicodeScalarText(value: string): boolean {
  for (const character of value) {
    const codePoint = character.codePointAt(0);
    if (codePoint !== undefined && codePoint >= 0xd800 && codePoint <= 0xdfff) return false;
  }
  return true;
}

function isFactValue(value: unknown): value is components["schemas"]["FactValue"] {
  const pending: Array<{ depth: number; value: unknown }> = [{ depth: 0, value }];
  let nodes = 0;
  while (pending.length > 0) {
    const current = pending.pop();
    if (!current || current.depth > 8 || ++nodes > 1_024) return false;
    if (current.value === null || typeof current.value === "boolean") continue;
    if (typeof current.value === "string") {
      if (
        !isUnicodeScalarText(current.value) ||
        new TextEncoder().encode(current.value).byteLength > MAX_TEXT_BYTES
      ) {
        return false;
      }
      continue;
    }
    if (typeof current.value === "number") {
      if (!Number.isFinite(current.value) || Math.abs(current.value) > MAX_SAFE_INTEGER)
        return false;
      continue;
    }
    if (!Array.isArray(current.value)) return false;
    for (const item of current.value) pending.push({ depth: current.depth + 1, value: item });
  }
  return true;
}
