import { CheckCircle2, ShieldCheck } from "lucide-react";
import type { GovernanceBaselineRecord } from "../../api/governanceBaseline/schema";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateTime } from "../../domain/format";
import { GovernanceRuleEvidence } from "./GovernanceRuleEvidence";

export function GovernanceBaselineEvidence({
  record,
}: {
  readonly record: GovernanceBaselineRecord | null;
}) {
  if (record === null) {
    return (
      <div className="governance-baseline-empty" role="status">
        <ShieldCheck aria-hidden="true" />
        <div>
          <strong>No approved baseline</strong>
          <span>This repository has no retained expected governance state.</span>
        </div>
        <StatusBadge tone="warning">absent</StatusBadge>
      </div>
    );
  }
  return (
    <div className="governance-baseline-evidence">
      <div className="governance-baseline-summary" role="status">
        <CheckCircle2 aria-hidden="true" />
        <div>
          <strong>Approved expected state</strong>
          <span>
            Version {record.pointer.version} approved {formatDateTime(record.approvedAt)}
          </span>
        </div>
        <StatusBadge tone="positive">approved</StatusBadge>
      </div>
      <dl className="governance-summary">
        <Metadata label="Approved by" value={record.actor} />
        <Metadata label="Reason" value={record.reason} />
        <Metadata label="Observed" value={formatDateTime(record.observedAt)} />
        <Metadata label="State digest" value={record.pointer.stateDigest} code />
      </dl>
      <details className="governance-baseline-details">
        <summary>Retained baseline evidence</summary>
        <dl className="governance-rule-metadata">
          <Metadata label="Baseline ID" value={record.pointer.baselineId} code />
          <Metadata label="Operation ID" value={record.operationId} code />
          <Metadata label="Audit event" value={record.auditEventId} code />
        </dl>
        {record.state.rules.length === 0 ? (
          <p className="governance-baseline-no-rules">No effective rules were retained.</p>
        ) : (
          <GovernanceRuleEvidence rules={record.state.rules} />
        )}
      </details>
    </div>
  );
}

function Metadata({
  code = false,
  label,
  value,
}: {
  readonly code?: boolean;
  readonly label: string;
  readonly value: string;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{code ? <code>{value}</code> : value}</dd>
    </div>
  );
}
