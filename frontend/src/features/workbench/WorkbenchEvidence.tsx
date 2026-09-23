import { Activity, FileClock, GitBranch, ScrollText, ShieldAlert } from "lucide-react";
import type { components } from "../../api/generated";
import type { WorkbenchSnapshot } from "../../api/workbench/schema";
import { type DataColumn, DataTable } from "../../components/DataTable";
import { IdentityDisclosure } from "../../components/IdentityDisclosure";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateTime } from "../../domain/format";

type Plan = components["schemas"]["PlanResponse"];
type Run = components["schemas"]["RunResponse"];
type Override = components["schemas"]["OverrideResponse"];
type ConfigEpoch = components["schemas"]["ConfigEpochResponse"];
type AuditEvent = components["schemas"]["AuditEventResponse"];

const planColumns: readonly DataColumn<Plan>[] = [
  {
    header: "Plan",
    presentation: "identity",
    render: (row) => <IdentityDisclosure value={row.planId} />,
  },
  {
    header: "Mode",
    render: (row) => (
      <StatusBadge tone={row.executionMode === "selected" ? "info" : "warning"}>
        {row.executionMode}
      </StatusBadge>
    ),
  },
  { header: "Ref", render: (row) => row.ref },
  {
    header: "Head",
    presentation: "identity",
    render: (row) => <IdentityDisclosure value={row.headSha} />,
  },
  { header: "Issued", presentation: "time", render: (row) => formatDateTime(row.issuedAt) },
  { header: "Witnesses", render: (row) => row.selectedWitnessIds.length },
  { header: "Fallback", presentation: "prose", render: (row) => row.fallbackReason ?? "None" },
];

const runColumns: readonly DataColumn<Run>[] = [
  {
    header: "Subject",
    presentation: "identity",
    render: (row) => <IdentityDisclosure value={row.subjectId} />,
  },
  {
    header: "State",
    render: (row) => <StatusBadge tone={runTone(row.state)}>{row.state}</StatusBadge>,
  },
  { header: "Event", render: (row) => row.eventName },
  { header: "Revision", render: (row) => row.revision },
  { header: "Attempt", render: (row) => `${row.attemptCount}/${row.maxAttempts}` },
  { header: "Findings", render: (row) => row.findings.length },
  { header: "Deadline", presentation: "time", render: (row) => formatDateTime(row.deadlineAt) },
];

const overrideColumns: readonly DataColumn<Override>[] = [
  {
    header: "Override",
    presentation: "identity",
    render: (row) => <IdentityDisclosure value={row.overrideId} />,
  },
  { header: "Kind", render: (row) => row.kind.replaceAll("_", " ") },
  {
    header: "State",
    render: (row) => (
      <StatusBadge tone={row.active ? "warning" : "neutral"}>
        {row.active ? "active" : "inactive"}
      </StatusBadge>
    ),
  },
  { header: "Actor", presentation: "prose", render: (row) => row.actor },
  { header: "Applied", presentation: "time", render: (row) => formatDateTime(row.appliedAt) },
  { header: "Reason", presentation: "prose", render: (row) => row.reason },
];

const epochColumns: readonly DataColumn<ConfigEpoch>[] = [
  {
    header: "Epoch",
    presentation: "identity",
    render: (row) => <IdentityDisclosure value={row.epochId} />,
  },
  {
    header: "State",
    render: (row) => (
      <StatusBadge tone={row.active ? "positive" : "neutral"}>
        {row.active ? "active" : "inactive"}
      </StatusBadge>
    ),
  },
  { header: "Revision", render: (row) => row.activeRevision ?? "-" },
  { header: "Format", render: (row) => row.sourceFormat },
  {
    header: "Source hash",
    presentation: "identity",
    render: (row) => <IdentityDisclosure value={row.sourceHash} />,
  },
];

const auditColumns: readonly DataColumn<AuditEvent>[] = [
  { header: "Sequence", render: (row) => row.sequence },
  { header: "Event", render: (row) => row.eventType },
  {
    header: "Subject",
    presentation: "identity",
    render: (row) => <IdentityDisclosure value={row.subjectId} />,
  },
  { header: "Actor", presentation: "prose", render: (row) => row.actor },
  { header: "Created", presentation: "time", render: (row) => formatDateTime(row.createdAt) },
  {
    header: "Hash",
    presentation: "identity",
    render: (row) => <IdentityDisclosure value={row.eventHash} />,
  },
];

export function WorkbenchEvidence({
  snapshot,
  section,
}: {
  readonly snapshot: WorkbenchSnapshot;
  readonly section: keyof WorkbenchSnapshot["truncated"];
}) {
  return (
    <div className="evidence-stack">
      {section === "plans" ? (
        <EvidenceSection
          icon={<GitBranch className="evidence-icon" aria-hidden="true" />}
          title="Plans"
          truncated={snapshot.truncated.plans}
        >
          <DataTable
            columns={planColumns}
            emptyLabel="No plans in this snapshot."
            keyOf={(row) => row.recordId}
            rows={snapshot.plans}
          />
        </EvidenceSection>
      ) : null}
      {section === "runs" ? (
        <EvidenceSection
          icon={<Activity className="evidence-icon" aria-hidden="true" />}
          title="Runs"
          truncated={snapshot.truncated.runs}
        >
          <DataTable
            columns={runColumns}
            emptyLabel="No runs in this snapshot."
            keyOf={(row) => `${row.subjectId}:${row.revision}`}
            rows={snapshot.runs}
          />
        </EvidenceSection>
      ) : null}
      {section === "overrides" ? (
        <EvidenceSection
          icon={<ShieldAlert className="evidence-icon" aria-hidden="true" />}
          title="Overrides"
          truncated={snapshot.truncated.overrides}
        >
          <DataTable
            columns={overrideColumns}
            emptyLabel="No overrides in this snapshot."
            keyOf={(row) => row.overrideId}
            rows={snapshot.overrides}
          />
        </EvidenceSection>
      ) : null}
      {section === "configEpochs" ? (
        <EvidenceSection
          icon={<FileClock className="evidence-icon" aria-hidden="true" />}
          title="Configuration epochs"
          truncated={snapshot.truncated.configEpochs}
        >
          <DataTable
            columns={epochColumns}
            emptyLabel="No configuration epochs in this snapshot."
            keyOf={(row) => row.epochId}
            rows={snapshot.configEpochs}
          />
        </EvidenceSection>
      ) : null}
      {section === "auditEvents" ? (
        <EvidenceSection
          icon={<ScrollText className="evidence-icon" aria-hidden="true" />}
          title="Audit events"
          truncated={snapshot.truncated.auditEvents}
        >
          <DataTable
            columns={auditColumns}
            emptyLabel="No audit events in this snapshot."
            keyOf={(row) => row.auditEventId}
            rows={snapshot.auditEvents}
          />
        </EvidenceSection>
      ) : null}
    </div>
  );
}

function EvidenceSection({
  children,
  icon,
  title,
  truncated,
}: {
  readonly children: React.ReactNode;
  readonly icon: React.ReactNode;
  readonly title: string;
  readonly truncated: boolean;
}) {
  return (
    <section className="evidence-section">
      <header className="evidence-section-header">
        <h2>
          {icon}
          {title}
        </h2>
        {truncated ? <StatusBadge tone="warning">truncated</StatusBadge> : null}
      </header>
      <div>{children}</div>
    </section>
  );
}

function runTone(state: Run["state"]): "positive" | "warning" | "negative" | "info" {
  if (state === "success") return "positive";
  if (state === "pending") return "info";
  if (state === "conflict") return "warning";
  return "negative";
}
