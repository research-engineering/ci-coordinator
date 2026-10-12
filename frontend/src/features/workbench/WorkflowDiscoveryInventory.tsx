import { GitFork, ListTree, Workflow } from "lucide-react";
import type { WorkflowDiscoveryReport } from "../../api/workflowDiscovery/schema";
import type { DataColumn } from "../../components/DataTable";
import { StatusBadge } from "../../components/StatusBadge";
import { formatCount, formatInteger } from "../../domain/format";
import { DiscoveryTable } from "./DiscoveryTable";
import { WorkflowRoleInventory } from "./WorkflowRoleInventory";

type WorkflowItem = WorkflowDiscoveryReport["workflows"][number];
type JobItem = WorkflowItem["jobs"][number];
type CallEdge = WorkflowDiscoveryReport["callEdges"][number];
type SourceItem = WorkflowDiscoveryReport["sources"][number];
type AdoptionItem = WorkflowDiscoveryReport["adoptionAssessments"][number];
type RequiredChange = AdoptionItem["requiredChanges"][number];
type UnknownItem = WorkflowDiscoveryReport["unknowns"][number];

interface JobRow {
  readonly job: JobItem;
  readonly workflowPath: string;
}

interface WorkflowDiscoveryInventoryProps {
  readonly report: WorkflowDiscoveryReport;
}

const SOURCE_COLUMNS: readonly DataColumn<SourceItem>[] = [
  { header: "Path", render: (source) => <code>{source.path}</code> },
  { header: "Blob", render: (source) => <code>{source.blobSha.slice(0, 12)}</code> },
  { header: "Bytes", render: (source) => formatInteger(source.size) },
];

const JOB_ROLE_LABELS: Readonly<Record<NonNullable<RequiredChange["role"]>, string>> = {
  execution: "execution",
  fallback: "FullCI fallback",
  gate: "aggregate gate",
  invocation_classifier: "invocation classifier",
  plan: "plan",
  plan_request: "plan request",
};

function adoptionColumns(
  unknownsById: ReadonlyMap<string, UnknownItem>,
): readonly DataColumn<AdoptionItem>[] {
  return [
    {
      header: "Workflow",
      render: (item) => <code>{item.workflowPath}</code>,
    },
    {
      header: "State",
      render: (item) => (
        <StatusBadge
          tone={
            item.state === "in_place_job_set" ||
            item.state === "reusable_workflow_set" ||
            item.state === "witness_shards"
              ? "positive"
              : "warning"
          }
        >
          {formatAdoptionValue(item.state)}
        </StatusBadge>
      ),
    },
    {
      header: "Recommended adapter",
      render: (item) => formatAdoptionValue(item.recommendedAdapter),
    },
    {
      header: "Support profile",
      render: (item) =>
        item.supportProfileId === null ? (
          <StatusBadge tone="warning">unadmitted: full validation only</StatusBadge>
        ) : (
          <code>{item.supportProfileId}</code>
        ),
    },
    {
      header: "Consumer changes",
      render: (item) => <RequiredChangeList changes={item.requiredChanges} />,
    },
    {
      header: "Unknown semantics",
      render: (item) => <UnknownList ids={item.unknownIds} unknownsById={unknownsById} />,
    },
    {
      header: "Owner action",
      render: (item) =>
        item.requiredOwnerInputs.length === 0 ? "None" : item.requiredOwnerInputs.join(", "),
    },
    {
      header: "Blockers",
      render: (item) =>
        item.blockers.length === 0 ? "None" : item.blockers.map(formatAdoptionValue).join(", "),
    },
  ];
}

const WORKFLOW_COLUMNS: readonly DataColumn<WorkflowItem>[] = [
  {
    header: "Workflow",
    render: (item) => (
      <span className="discovery-primary-cell">
        <strong>{item.name ?? "Unnamed workflow"}</strong>
        <code>{item.path}</code>
      </span>
    ),
  },
  { header: "Triggers", render: (item) => formatList(item.triggers, "Unknown") },
  { header: "Jobs", render: (item) => item.jobs.length },
  { header: "Permissions", render: (item) => formatPermissions(item.permissions) },
  {
    header: "Concurrency",
    render: (item) => formatConcurrency(item.concurrency),
  },
  {
    header: "Secret names",
    render: (item) => formatList(item.staticSecretNames, "Unknown"),
  },
];

const JOB_COLUMNS: readonly DataColumn<JobRow>[] = [
  {
    header: "Job",
    render: ({ job, workflowPath }) => (
      <span className="discovery-primary-cell">
        <strong>{job.name ?? job.jobId}</strong>
        <code>
          {workflowPath} / {job.jobId}
        </code>
      </span>
    ),
  },
  {
    header: "Provider signal",
    render: ({ job }) =>
      job.providerSignalName ? (
        <code>{job.providerSignalName}</code>
      ) : (
        <StatusBadge tone="warning">unknown</StatusBadge>
      ),
  },
  {
    header: "Execution",
    render: ({ job }) => (
      <CompactFacts
        values={[
          ["Runs on", formatList(job.runsOn, "Unknown")],
          ["Matrix", formatMatrix(job.matrix)],
          ["Services", formatList(job.serviceIds, "None")],
          ["Timeout", job.timeoutMinutes === null ? "Unknown" : `${job.timeoutMinutes} min`],
        ]}
      />
    ),
  },
  {
    header: "Authority",
    render: ({ job }) => (
      <CompactFacts
        values={[
          ["Permissions", formatPermissions(job.permissions)],
          ["Environment", job.environment ?? "None"],
          ["Secret names", formatList(job.staticSecretNames, "Unknown")],
        ]}
      />
    ),
  },
  {
    header: "Invocation",
    render: ({ job }) => (
      <CompactFacts
        values={[
          ["Workflow call", job.uses ?? "None"],
          ["Needs", formatList(job.needs, "None")],
          ["Actions", formatActions(job)],
          ["Run steps", formatRunSteps(job)],
        ]}
      />
    ),
  },
];

const CALL_EDGE_COLUMNS: readonly DataColumn<CallEdge>[] = [
  {
    header: "Caller",
    render: (edge) => (
      <span className="discovery-primary-cell">
        <code>{edge.callerWorkflowPath}</code>
        <span>{edge.callerJobId}</span>
      </span>
    ),
  },
  { header: "Uses", render: (edge) => <code>{edge.uses}</code> },
  { header: "Kind", render: (edge) => edge.kind },
  {
    header: "Resolution",
    render: (edge) => (
      <StatusBadge tone={edge.status === "resolved" ? "positive" : "warning"}>
        {edge.status}
      </StatusBadge>
    ),
  },
  {
    header: "Target",
    render: (edge) => edge.targetPath ?? edge.remoteRef ?? "Unresolved",
  },
  {
    header: "Source",
    render: (edge) => formatLocation(edge.provenance.location),
  },
];

export function WorkflowDiscoveryInventory({ report }: WorkflowDiscoveryInventoryProps) {
  const jobs: JobRow[] = report.workflows.flatMap((workflowItem) =>
    workflowItem.jobs.map((jobItem) => ({ job: jobItem, workflowPath: workflowItem.path })),
  );
  const unknownsById = new Map(report.unknowns.map((entry) => [entry.unknownId, entry]));
  return (
    <div className="discovery-section-stack">
      <details className="evidence-section" open>
        <summary>
          <h3>
            <ListTree className="evidence-icon" aria-hidden="true" />
            Adoption readiness
          </h3>
          <span>{formatCount(report.adoptionAssessments.length, "assessment")}</span>
        </summary>
        <DiscoveryTable
          columns={adoptionColumns(unknownsById)}
          emptyLabel="No workflow assessments"
          keyOf={(item) => item.workflowPath}
          label="workflow adoption assessment"
          rows={report.adoptionAssessments}
        />
      </details>
      <WorkflowRoleInventory report={report} />
      <details className="evidence-section" open>
        <summary>
          <h3>
            <Workflow className="evidence-icon" aria-hidden="true" />
            Workflow topology
          </h3>
          <span>{formatCount(report.workflows.length, "workflow")}</span>
        </summary>
        <DiscoveryTable
          columns={WORKFLOW_COLUMNS}
          emptyLabel="No workflow summaries"
          keyOf={(item) => item.path}
          label="workflow"
          rows={report.workflows}
        />
        <h4 className="discovery-subheading">Jobs</h4>
        <DiscoveryTable
          columns={JOB_COLUMNS}
          emptyLabel="No jobs"
          keyOf={({ job }) => job.subjectId}
          label="job"
          rows={jobs}
        />
      </details>
      <details className="evidence-section">
        <summary>
          <h3>
            <GitFork className="evidence-icon" aria-hidden="true" />
            Reusable workflow calls
          </h3>
          <span>{formatCount(report.callEdges.length, "edge")}</span>
        </summary>
        <DiscoveryTable
          columns={CALL_EDGE_COLUMNS}
          emptyLabel="No reusable workflow calls"
          keyOf={(edge) => edge.edgeId}
          label="call edge"
          rows={report.callEdges}
        />
      </details>
      <details className="evidence-section">
        <summary>
          <h3>
            <ListTree className="evidence-icon" aria-hidden="true" />
            Exact source set
          </h3>
          <span>{formatCount(report.sources.length, "blob")}</span>
        </summary>
        <DiscoveryTable
          columns={SOURCE_COLUMNS}
          emptyLabel="No workflow sources"
          keyOf={(sourceItem) => sourceItem.path}
          label="source"
          rows={report.sources}
        />
      </details>
    </div>
  );
}

function RequiredChangeList({ changes }: { readonly changes: readonly RequiredChange[] }) {
  if (changes.length === 0) return "None";
  return (
    <ul className="workflow-call-list">
      {changes.map((change) => (
        <li key={`${change.blocker}/${change.action}/${change.role ?? ""}/${change.jobId ?? ""}`}>
          {describeRequiredChange(change)}
        </li>
      ))}
    </ul>
  );
}

function UnknownList({
  ids,
  unknownsById,
}: {
  readonly ids: readonly string[];
  readonly unknownsById: ReadonlyMap<string, UnknownItem>;
}) {
  if (ids.length === 0) return "None";
  return (
    <details>
      <summary>{formatCount(ids.length, "unknown")}</summary>
      <ul className="workflow-call-list">
        {ids.map((id) => {
          const unknown = unknownsById.get(id);
          return (
            <li key={id}>
              {unknown ? (
                <>
                  <code>{unknown.field}</code>: {formatAdoptionValue(unknown.reason)}
                  {unknown.criticality === "safety" ? (
                    <>
                      {" "}
                      <StatusBadge tone="warning">safety</StatusBadge>
                    </>
                  ) : null}
                </>
              ) : (
                <code>{id}</code>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function describeRequiredNeeds(change: RequiredChange): string {
  const values = [
    ...(change.needs === null
      ? ["its execution dependencies declared statically (currently unknown)"]
      : change.needs),
    ...change.needsControlRoles.map((role) => `the owner-named ${JOB_ROLE_LABELS[role]} job`),
  ];
  return values.length === 0 ? "none" : values.join(", ");
}

function describeRequiredChange(change: RequiredChange): string {
  const role = change.role === null ? "" : JOB_ROLE_LABELS[change.role];
  const job = change.jobId ?? "";
  const needs = describeRequiredNeeds(change);
  const target = change.jobId === null ? `the owner-named ${role} job` : `${role} job ${job}`;
  switch (change.action) {
    case "add_control_job":
      return `Add an owner-named ${role} job with needs: ${needs}`;
    case "add_job":
      return `Add ${role} job ${job} with needs: ${needs}`;
    case "close_report_call_graph":
      return "Resolve the unresolved reusable-workflow calls in other workflows";
    case "declare_registry_workflow":
      return "Declare this workflow in the target execution registry";
    case "register_execution_job":
      return `Register job ${job} as an execution job with needs: ${needs}`;
    case "remove_or_register_job":
      return `Remove job ${job} or register it in the target execution registry`;
    case "repair_other_workflow_sources":
      return "Repair the other workflow sources that failed to parse";
    case "repair_target_registry":
      return "Repair the target execution registry";
    case "repair_workflow_syntax":
      return "Repair this workflow's syntax";
    case "resolve_reusable_workflow_call":
      return `Resolve the reusable-workflow call in job ${job}`;
    case "set_job_condition":
      return `Set ${target} condition to ${change.expectedValue ?? ""}`;
    case "set_job_name":
      return `Set ${role} job ${job} name to ${change.expectedValue ?? ""}`;
    case "set_job_needs":
      return `Set ${role} job ${job} needs to: ${needs}`;
  }
}

function CompactFacts({ values }: { readonly values: readonly (readonly [string, string])[] }) {
  return (
    <dl className="compact-facts">
      {values.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function formatList(values: readonly string[] | null, fallback: string): string {
  if (values === null) return fallback;
  return values.length === 0 ? "None" : values.join(", ");
}

function formatPermissions(value: WorkflowItem["permissions"]): string {
  if (value === null) return "Unknown";
  if (value.kind === "absent") return "Not declared";
  if (value.kind === "all") return value.allLevel ?? "Unknown";
  return value.entries.length === 0
    ? "None"
    : value.entries.map((entry) => `${entry.name}: ${entry.level}`).join(", ");
}

function formatConcurrency(value: WorkflowItem["concurrency"]): string {
  if (value === null) return "Unknown";
  return `${value.group ?? "Dynamic group"}; cancel: ${String(value.cancelInProgress ?? "dynamic")}`;
}

function formatMatrix(value: JobItem["matrix"]): string {
  if (value === null) return "Unknown";
  if (value.length === 0) return "None";
  return value.map((dimension) => `${dimension.name} (${dimension.values.length})`).join(", ");
}

function formatActions(jobItem: JobItem): string {
  if (jobItem.steps === null) return "Unknown";
  const actions = jobItem.steps.flatMap((stepItem) => (stepItem.uses ? [stepItem.uses] : []));
  return actions.length === 0 ? "None" : actions.join(", ");
}

function formatRunSteps(jobItem: JobItem): string {
  if (jobItem.steps === null) return "Unknown";
  return String(jobItem.steps.filter((stepItem) => stepItem.hasRun).length);
}

function formatLocation(location: CallEdge["provenance"]["location"]): string {
  return `${location.path}:${location.line}:${location.column}`;
}

function formatAdoptionValue(value: string): string {
  return value.replaceAll("_", " ");
}
