import { GitFork, ListTree, Workflow } from "lucide-react";
import type { WorkflowDiscoveryReport } from "../../api/workflowDiscovery/schema";
import type { DataColumn } from "../../components/DataTable";
import { StatusBadge } from "../../components/StatusBadge";
import { formatCount, formatInteger } from "../../domain/format";
import { DiscoveryTable } from "./DiscoveryTable";

type WorkflowItem = WorkflowDiscoveryReport["workflows"][number];
type JobItem = WorkflowItem["jobs"][number];
type CallEdge = WorkflowDiscoveryReport["callEdges"][number];
type SourceItem = WorkflowDiscoveryReport["sources"][number];
type AdoptionItem = WorkflowDiscoveryReport["adoptionAssessments"][number];

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

const ADOPTION_COLUMNS: readonly DataColumn<AdoptionItem>[] = [
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
          columns={ADOPTION_COLUMNS}
          emptyLabel="No workflow assessments"
          keyOf={(item) => item.workflowPath}
          label="workflow adoption assessment"
          rows={report.adoptionAssessments}
        />
      </details>
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
