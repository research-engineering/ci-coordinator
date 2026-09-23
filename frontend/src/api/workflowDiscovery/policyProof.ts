import { z } from "zod";
import type { components } from "../generated";
import { SUPPORTED_CI_EVENTS } from "./events";

type WorkflowDiscoveryReport = components["schemas"]["WorkflowDiscoveryResponse"];
type DecodedFact = WorkflowDiscoveryReport["facts"][number];
type DecodedJob = WorkflowDiscoveryReport["workflows"][number]["jobs"][number];
type DecodedWorkflow = WorkflowDiscoveryReport["workflows"][number];
type PolicyEvent = WorkflowDiscoveryReport["proposal"]["selectedEvents"][number];
type FactIndex = ReadonlyMap<string, unknown>;

interface StableSignalCandidate {
  readonly events: readonly PolicyEvent[];
  readonly jobId: string;
  readonly jobName: string;
  readonly workflowPath: string;
}

const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const ALWAYS_CONDITIONS = new Set(["always()", `\${{ always() }}`]);
const DUPLICATE_FACT = Symbol("duplicate fact");
const positiveInteger = z.number().int().min(1).max(MAX_SAFE_INTEGER);
const policyEvent = z.enum(SUPPORTED_CI_EVENTS);
const proposalExpectedSignal = z.strictObject({
  kind: z.literal("workflow"),
  name: boundedText(4_096),
  required: z.literal(true),
  requiredConclusion: z.literal("success"),
  source: z.literal("native"),
  workflowFile: boundedText(512).refine(
    (value) => /^\.github\/workflows\/[^/]+\.(?:yml|yaml)$/.test(value),
    "workflow path is not admitted",
  ),
});
const proposalRule = z.strictObject({
  expectedSignals: z.tuple([proposalExpectedSignal]),
  mode: z.literal("observe"),
  name: boundedText(4_096),
  omittedSignals: z.array(z.never()).length(0),
  on: z.strictObject({
    branches: z.tuple([boundedText(1_024)]),
    event: policyEvent,
  }),
});
const proposalPolicySource = z.strictObject({
  repository: z.strictObject({
    defaultBranch: boundedText(1_024),
    dynamicCi: z.null(),
    installationId: positiveInteger,
    name: boundedText(512),
    owner: boundedText(512),
    repositoryId: positiveInteger,
    rules: z.array(proposalRule).min(1).max(SUPPORTED_CI_EVENTS.length),
  }),
  schemaVersion: z.literal("ci-repository-policy/v1"),
});

export function reviewableProposalMismatches(report: WorkflowDiscoveryReport): {
  readonly policy: boolean;
  readonly selection: boolean;
} {
  const candidates = stableSignalCandidates(report.workflows, report.facts);
  const selected = candidates[0];
  const selection =
    !report.complete ||
    !report.localGraphClosed ||
    candidates.length !== 1 ||
    !selected ||
    !signalIdentityIsUnique(report.workflows, report.facts, selected) ||
    selected.workflowPath !== report.proposal.selectedWorkflowPath ||
    selected.jobId !== report.proposal.selectedJobId ||
    selected.jobName !== report.proposal.selectedJobName ||
    !sameFactValue(selected.events, report.proposal.selectedEvents);
  return {
    policy: !proposalPolicyMatches(report.proposal, report.repository),
    selection,
  };
}

function stableSignalCandidates(
  workflows: readonly DecodedWorkflow[],
  facts: readonly DecodedFact[],
): StableSignalCandidate[] {
  const factIndex = indexFacts(facts);
  const candidates: StableSignalCandidate[] = [];
  for (const workflow of workflows) {
    const triggers = workflow.triggers;
    const defaultBranchEvents = provenPolicyEvents(
      factIndex,
      workflow.subjectId,
      "workflow.default_branch_ci_events",
    );
    if (
      triggers === null ||
      !triggers.includes("workflow_dispatch") ||
      !isProven(factIndex, workflow.subjectId, "workflow.triggers", triggers) ||
      defaultBranchEvents === null ||
      defaultBranchEvents.some((event) => !triggers.includes(event))
    ) {
      continue;
    }
    if (defaultBranchEvents.length === 0) continue;
    const terminalJobId =
      workflow.jobs.length === 1 ? null : completeTerminalJobId(workflow, factIndex);
    for (const job of workflow.jobs) {
      const stable =
        nativeSignalIsProven(job, factIndex) &&
        (workflow.jobs.length === 1
          ? job.condition === null &&
            job.needs?.length === 0 &&
            isProven(factIndex, job.subjectId, "job.condition.syntax", null) &&
            isProven(factIndex, job.subjectId, "job.needs", [])
          : job.jobId === terminalJobId &&
            job.condition !== null &&
            ALWAYS_CONDITIONS.has(job.condition) &&
            isProven(factIndex, job.subjectId, "job.condition.syntax", job.condition));
      if (stable && job.providerSignalName !== null) {
        candidates.push({
          events: defaultBranchEvents,
          jobId: job.jobId,
          jobName: job.providerSignalName,
          workflowPath: workflow.path,
        });
      }
    }
  }
  return candidates;
}

function signalIdentityIsUnique(
  workflows: readonly DecodedWorkflow[],
  facts: readonly DecodedFact[],
  selected: StableSignalCandidate,
): boolean {
  const factIndex = indexFacts(facts);
  const selectedKey = asciiLower(selected.jobName);
  for (const workflow of workflows) {
    for (const job of workflow.jobs) {
      if (workflow.path === selected.workflowPath && job.jobId === selected.jobId) continue;
      const displayName = effectiveJobDisplayName(job, factIndex);
      if (displayName === null || asciiLower(displayName) === selectedKey) return false;
    }
  }
  return true;
}

function effectiveJobDisplayName(job: DecodedJob, facts: FactIndex): string | null {
  return isProven(facts, job.subjectId, "job.name", job.name) ? (job.name ?? job.jobId) : null;
}

function provenPolicyEvents(
  facts: FactIndex,
  subjectId: string,
  field: string,
): PolicyEvent[] | null {
  const value = facts.get(factCoordinate(subjectId, field));
  if (!Array.isArray(value)) return null;
  const events = SUPPORTED_CI_EVENTS.filter((event) => value.includes(event));
  return sameFactValue(value, events) ? events : null;
}

function proposalPolicyMatches(
  proposal: WorkflowDiscoveryReport["proposal"],
  repository: WorkflowDiscoveryReport["repository"],
): boolean {
  if (proposal.policySource === null) return false;
  let raw: unknown;
  try {
    raw = JSON.parse(proposal.policySource) as unknown;
  } catch {
    return false;
  }
  const decoded = proposalPolicySource.safeParse(raw);
  if (!decoded.success) return false;
  const policy = decoded.data.repository;
  if (
    policy.installationId !== repository.scope.installationId ||
    policy.repositoryId !== repository.scope.repositoryId ||
    policy.owner !== repository.owner ||
    policy.name !== repository.name ||
    policy.defaultBranch !== repository.defaultBranch ||
    policy.rules.length !== proposal.selectedEvents.length
  ) {
    return false;
  }
  return policy.rules.every((rule, index) => {
    const event = proposal.selectedEvents[index];
    const signal = rule.expectedSignals[0];
    return (
      event !== undefined &&
      rule.name === `discovered-${event}-default-branch-ci` &&
      rule.on.event === event &&
      rule.on.branches[0] === repository.defaultBranch &&
      signal.name === proposal.selectedJobName &&
      signal.workflowFile === proposal.selectedWorkflowPath
    );
  });
}

function nativeSignalIsProven(job: DecodedJob, facts: FactIndex): boolean {
  return (
    job.providerSignalName !== null &&
    job.matrix?.length === 0 &&
    job.uses === null &&
    isProven(facts, job.subjectId, "job.provider_signal_name", job.providerSignalName) &&
    isProven(facts, job.subjectId, "job.matrix", []) &&
    isProven(facts, job.subjectId, "job.uses", null)
  );
}

function completeTerminalJobId(workflow: DecodedWorkflow, facts: FactIndex): string | null {
  const dependencies = new Map<string, readonly string[]>();
  for (const job of workflow.jobs) {
    if (job.needs === null || !isProven(facts, job.subjectId, "job.needs", job.needs)) {
      return null;
    }
    dependencies.set(job.jobId, job.needs);
  }
  const indegree = new Map([...dependencies].map(([id, needs]) => [id, needs.length]));
  const dependents = new Map([...dependencies.keys()].map((id) => [id, [] as string[]]));
  for (const [jobId, needs] of dependencies) {
    for (const dependency of needs) {
      const targets = dependents.get(dependency);
      if (targets === undefined) return null;
      targets.push(jobId);
    }
  }
  const ready = [...indegree].filter(([, degree]) => degree === 0).map(([id]) => id);
  let visited = 0;
  while (ready.length > 0) {
    const dependency = ready.pop();
    if (dependency === undefined) return null;
    visited += 1;
    for (const dependent of dependents.get(dependency) ?? []) {
      const degree = indegree.get(dependent);
      if (degree === undefined) return null;
      indegree.set(dependent, degree - 1);
      if (degree === 1) ready.push(dependent);
    }
  }
  if (visited !== dependencies.size) return null;
  const sinks = [...dependents].filter(([, targets]) => targets.length === 0).map(([id]) => id);
  return sinks.length === 1 ? (sinks[0] ?? null) : null;
}

function isProven(facts: FactIndex, subjectId: string, field: string, expected: unknown): boolean {
  const key = factCoordinate(subjectId, field);
  return facts.has(key) && sameFactValue(facts.get(key), expected);
}

function indexFacts(facts: readonly DecodedFact[]): FactIndex {
  const index = new Map<string, unknown>();
  for (const fact of facts) {
    const key = factCoordinate(fact.subjectId, fact.field);
    index.set(key, index.has(key) ? DUPLICATE_FACT : fact.value);
  }
  return index;
}

function factCoordinate(subjectId: string, field: string): string {
  return JSON.stringify([subjectId, field]);
}

function sameFactValue(actual: unknown, expected: unknown): boolean {
  if (!Array.isArray(actual) || !Array.isArray(expected)) return actual === expected;
  return (
    actual.length === expected.length &&
    actual.every((item, index) => sameFactValue(item, expected[index]))
  );
}

function asciiLower(value: string): string {
  return value.replace(/[A-Z]/g, (character) => character.toLowerCase());
}

function boundedText(maximumBytes: number) {
  return z
    .string()
    .min(1)
    .max(maximumBytes)
    .refine(isUnicodeScalarText, "text contains an unpaired surrogate")
    .refine(
      (value) => new TextEncoder().encode(value).byteLength <= maximumBytes,
      "text exceeds its byte bound",
    );
}

function isUnicodeScalarText(value: string): boolean {
  for (const character of value) {
    const codePoint = character.codePointAt(0);
    if (codePoint !== undefined && codePoint >= 0xd800 && codePoint <= 0xdfff) return false;
  }
  return true;
}
