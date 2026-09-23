import { FileCode2, GitBranch, ListChecks, ShieldCheck, Workflow } from "lucide-react";
import type { WorkflowDiscoveryReport } from "../../api/workflowDiscovery/schema";
import { StatusBadge } from "../../components/StatusBadge";

interface WorkflowDiscoverySummaryProps {
  readonly report: WorkflowDiscoveryReport;
}

export function WorkflowDiscoverySummary({ report }: WorkflowDiscoverySummaryProps) {
  const jobCount = report.workflows.reduce((total, workflow) => total + workflow.jobs.length, 0);
  const safetyUnknowns = report.unknowns.filter((item) => item.criticality === "safety").length;
  return (
    <section className="discovery-summary" aria-label="Workflow discovery summary">
      <SummaryItem icon={GitBranch} label="Revision" value={shortSha(report.revision)} />
      <SummaryItem icon={FileCode2} label="Workflows" value={String(report.workflows.length)} />
      <SummaryItem icon={Workflow} label="Jobs" value={String(jobCount)} />
      <SummaryItem icon={ListChecks} label="Safety unknowns" value={String(safetyUnknowns)} />
      <div className="discovery-summary-item">
        <ShieldCheck aria-hidden="true" />
        <span>Proposal</span>
        <StatusBadge tone={report.proposal.state === "reviewable" ? "positive" : "warning"}>
          {report.proposal.state}
        </StatusBadge>
      </div>
    </section>
  );
}

interface SummaryItemProps {
  readonly icon: typeof GitBranch;
  readonly label: string;
  readonly value: string;
}

function SummaryItem({ icon: Icon, label, value }: SummaryItemProps) {
  return (
    <div className="discovery-summary-item">
      <Icon aria-hidden="true" />
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function shortSha(value: string): string {
  return value.slice(0, 12);
}
