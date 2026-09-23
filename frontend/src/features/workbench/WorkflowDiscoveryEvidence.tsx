import { CircleHelp, ListChecks } from "lucide-react";
import type { WorkflowDiscoveryReport } from "../../api/workflowDiscovery/schema";
import type { DataColumn } from "../../components/DataTable";
import { StatusBadge } from "../../components/StatusBadge";
import { formatCount } from "../../domain/format";
import { DiscoveryTable } from "./DiscoveryTable";

type FactItem = WorkflowDiscoveryReport["facts"][number];
type UnknownItem = WorkflowDiscoveryReport["unknowns"][number];

interface WorkflowDiscoveryEvidenceProps {
  readonly report: WorkflowDiscoveryReport;
}

const FACT_COLUMNS: readonly DataColumn<FactItem>[] = [
  {
    header: "Predicate",
    render: (item) => (
      <span className="discovery-primary-cell">
        <code>{item.field}</code>
        <span>{item.category}</span>
      </span>
    ),
  },
  { header: "Subject", render: (item) => <code>{item.subjectId}</code> },
  { header: "Value", render: (item) => <code>{formatValue(item.value)}</code> },
  {
    header: "Criticality",
    render: (item) => (
      <StatusBadge tone={item.criticality === "safety" ? "warning" : "neutral"}>
        {item.criticality}
      </StatusBadge>
    ),
  },
  { header: "Source", render: (item) => formatSource(item.provenance) },
];

const UNKNOWN_COLUMNS: readonly DataColumn<UnknownItem>[] = [
  {
    header: "Predicate",
    render: (item) => (
      <span className="discovery-primary-cell">
        <code>{item.field}</code>
        <span>{item.category}</span>
      </span>
    ),
  },
  { header: "Reason", render: (item) => item.reason },
  {
    header: "Observed syntax",
    render: (item) => (item.observedSyntax === null ? "None" : <code>{item.observedSyntax}</code>),
  },
  {
    header: "Criticality",
    render: (item) => (
      <StatusBadge tone={item.criticality === "safety" ? "warning" : "neutral"}>
        {item.criticality}
      </StatusBadge>
    ),
  },
  { header: "Source", render: (item) => formatSource(item.provenance) },
];

export function WorkflowDiscoveryEvidence({ report }: WorkflowDiscoveryEvidenceProps) {
  return (
    <div className="discovery-section-stack">
      <details className="evidence-section" open={report.unknowns.length > 0}>
        <summary>
          <h3>
            <CircleHelp className="evidence-icon" aria-hidden="true" />
            Explicit unknowns
          </h3>
          <span>{formatCount(report.unknowns.length, "record")}</span>
        </summary>
        <DiscoveryTable
          columns={UNKNOWN_COLUMNS}
          emptyLabel="No explicit unknowns"
          keyOf={(item) => item.unknownId}
          label="unknown"
          rows={report.unknowns}
        />
      </details>
      <details className="evidence-section">
        <summary>
          <h3>
            <ListChecks className="evidence-icon" aria-hidden="true" />
            Proven assertions
          </h3>
          <span>{formatCount(report.facts.length, "record")}</span>
        </summary>
        <DiscoveryTable
          columns={FACT_COLUMNS}
          emptyLabel="No proven assertions"
          keyOf={(item) => item.factId}
          label="assertion"
          rows={report.facts}
        />
      </details>
    </div>
  );
}

function formatValue(value: unknown): string {
  const encoded = JSON.stringify(value);
  return encoded === undefined ? "null" : encoded;
}

function formatSource(provenance: FactItem["provenance"]): string {
  const { location } = provenance;
  return `${location.path}:${location.line}:${location.column}`;
}
