import { Network } from "lucide-react";
import {
  projectWorkflowRoles,
  type UnclassifiedReason,
  type WorkflowRoleCallEdge,
  type WorkflowRoleInput,
  type WorkflowRoleRow,
} from "../../api/workflowDiscovery/workflowRoles";
import type { DataColumn } from "../../components/DataTable";
import { StatusBadge } from "../../components/StatusBadge";
import { formatCount } from "../../domain/format";
import { DiscoveryTable } from "./DiscoveryTable";

interface WorkflowRoleInventoryProps {
  readonly report: WorkflowRoleInput;
}

const UNCLASSIFIED_LABELS: Readonly<Record<UnclassifiedReason, string>> = {
  no_triggers: "No declared triggers",
  source_unparsed: "Source not parsed",
  triggers_unknown: "Triggers unknown",
};

const WORKFLOW_COLUMN: DataColumn<WorkflowRoleRow> = {
  header: "Workflow",
  render: (row) => (
    <span className="discovery-primary-cell">
      <strong>{row.name ?? "Unnamed workflow"}</strong>
      <code>{row.path}</code>
    </span>
  ),
};

const ENTRYPOINT_COLUMNS: readonly DataColumn<WorkflowRoleRow>[] = [
  WORKFLOW_COLUMN,
  { header: "Triggers", render: (row) => (row.triggers ?? []).join(", ") },
  {
    header: "Also component",
    render: (row) => (row.component ? "Yes" : "No"),
  },
  {
    header: "Reusable calls",
    render: (row) => <CallTree calls={row.reachableCalls} />,
  },
];

const COMPONENT_COLUMNS: readonly DataColumn<WorkflowRoleRow>[] = [
  WORKFLOW_COLUMN,
  {
    header: "Also entrypoint",
    render: (row) => (row.entrypoint ? "Yes" : "No"),
  },
  {
    header: "Callers",
    render: (row) => <CallerList callers={row.callers} />,
  },
  {
    header: "Reusable calls",
    render: (row) => <CallTree calls={row.reachableCalls} />,
  },
];

const UNCLASSIFIED_COLUMNS: readonly DataColumn<WorkflowRoleRow>[] = [
  WORKFLOW_COLUMN,
  {
    header: "Role",
    render: (row) => (
      <StatusBadge tone="warning">
        {row.unclassifiedReason ? UNCLASSIFIED_LABELS[row.unclassifiedReason] : "Unknown"}
      </StatusBadge>
    ),
  },
  {
    header: "Callers",
    render: (row) => <CallerList callers={row.callers} />,
  },
];

export function WorkflowRoleInventory({ report }: WorkflowRoleInventoryProps) {
  const roles = projectWorkflowRoles(report);
  return (
    <details className="evidence-section" open>
      <summary>
        <h3>
          <Network className="evidence-icon" aria-hidden="true" />
          Entrypoints and components
        </h3>
        <span>
          {formatCount(roles.entrypoints.length, "entrypoint")},{" "}
          {formatCount(roles.components.length, "component")}
        </span>
      </summary>
      <h4 className="discovery-subheading">Entrypoints</h4>
      <DiscoveryTable
        columns={ENTRYPOINT_COLUMNS}
        emptyLabel="No standalone-event entrypoints"
        keyOf={(row) => row.path}
        label="entrypoint"
        rows={roles.entrypoints}
      />
      <h4 className="discovery-subheading">Components</h4>
      <DiscoveryTable
        columns={COMPONENT_COLUMNS}
        emptyLabel="No reusable workflow components"
        keyOf={(row) => row.path}
        label="component"
        rows={roles.components}
      />
      <h4 className="discovery-subheading">Unknown role</h4>
      <DiscoveryTable
        columns={UNCLASSIFIED_COLUMNS}
        emptyLabel="No workflows with unknown role"
        keyOf={(row) => row.path}
        label="unknown-role workflow"
        rows={roles.unclassified}
      />
    </details>
  );
}

function CallTree({ calls }: { readonly calls: readonly WorkflowRoleCallEdge[] }) {
  if (calls.length === 0) return "None";
  return (
    <details>
      <summary>{formatCount(calls.length, "call")}</summary>
      <ul className="workflow-call-list">
        {calls.map((edge) => (
          <li key={edge.edgeId}>
            <code>
              {edge.callerWorkflowPath} / {edge.callerJobId}
            </code>{" "}
            <code>{edge.uses}</code> <CallStatus edge={edge} />
          </li>
        ))}
      </ul>
    </details>
  );
}

function CallerList({ callers }: { readonly callers: readonly WorkflowRoleCallEdge[] }) {
  if (callers.length === 0) return "No same-snapshot callers";
  return (
    <ul className="workflow-call-list">
      {callers.map((edge) => (
        <li key={edge.edgeId}>
          <code>
            {edge.callerWorkflowPath} / {edge.callerJobId}
          </code>{" "}
          <CallStatus edge={edge} />
        </li>
      ))}
    </ul>
  );
}

function CallStatus({ edge }: { readonly edge: WorkflowRoleCallEdge }) {
  return (
    <StatusBadge tone={edge.status === "resolved" ? "positive" : "warning"}>
      {edge.status.replaceAll("_", " ")}
    </StatusBadge>
  );
}
