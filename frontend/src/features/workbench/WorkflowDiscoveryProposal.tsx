import { FileJson2, ShieldAlert, ShieldCheck } from "lucide-react";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { ExpectedActiveEpoch } from "../../api/repositoryAttestation/client";
import type { WorkbenchScope } from "../../api/workbench/client";
import type { WorkflowDiscoveryReport } from "../../api/workflowDiscovery/schema";
import type { DataColumn } from "../../components/DataTable";
import { StatusBadge } from "../../components/StatusBadge";
import { formatCount, formatPolicySource } from "../../domain/format";
import { DiscoveryTable } from "./DiscoveryTable";
import { RepositoryActivationControl } from "./RepositoryActivationControl";

type Diagnostic = WorkflowDiscoveryReport["proposal"]["diagnostics"][number];

interface WorkflowDiscoveryProposalProps {
  readonly report: WorkflowDiscoveryReport;
  readonly scope: WorkbenchScope;
  readonly session: ControlPlaneSession | undefined;
  readonly expectedActive: ExpectedActiveEpoch | null | undefined;
}

const DIAGNOSTIC_COLUMNS: readonly DataColumn<Diagnostic>[] = [
  { header: "Code", render: (item) => <code>{item.code}</code> },
  { header: "Phase", render: (item) => item.phase },
  { header: "Rule", render: (item) => <code>{item.ruleId}</code> },
  { header: "Location", render: (item) => <code>{item.instancePointer}</code> },
];

export function WorkflowDiscoveryProposal({
  report,
  scope,
  session,
  expectedActive,
}: WorkflowDiscoveryProposalProps) {
  const { proposal } = report;
  const reviewable = proposal.state === "reviewable";
  const Icon = reviewable ? ShieldCheck : ShieldAlert;
  return (
    <section className="discovery-proposal" aria-labelledby="discovery-proposal-heading">
      <header>
        <div>
          <p className="eyebrow">Generated configuration</p>
          <h3 id="discovery-proposal-heading">Repository policy proposal</h3>
        </div>
        <StatusBadge tone={reviewable ? "positive" : "warning"}>{proposal.state}</StatusBadge>
      </header>
      <div className="proposal-state">
        <Icon aria-hidden="true" />
        <div>
          <strong>
            {reviewable ? "Observe-only policy admitted" : "Policy generation blocked"}
          </strong>
          <p>
            {reviewable
              ? "The source passed existing policy admission. It is not registered or active."
              : "No policy bytes were emitted because a required static identity is unproven."}
          </p>
        </div>
      </div>
      <RepositoryActivationControl
        expectedActive={expectedActive}
        proposal={proposal}
        scope={scope}
        session={session}
      />
      <dl className="proposal-metadata">
        <div>
          <dt>Manifest</dt>
          <dd>
            <code>{proposal.manifestId}</code>
          </dd>
        </div>
        <div>
          <dt>Workflow</dt>
          <dd>
            <code>{proposal.selectedWorkflowPath ?? "Not selected"}</code>
          </dd>
        </div>
        <div>
          <dt>Provider signal</dt>
          <dd>
            <code>{proposal.selectedJobName ?? "Not selected"}</code>
          </dd>
        </div>
        <div>
          <dt>Job ID</dt>
          <dd>
            <code>{proposal.selectedJobId ?? "Not selected"}</code>
          </dd>
        </div>
        <div>
          <dt>Events</dt>
          <dd>
            <code>
              {proposal.selectedEvents.length > 0 ? proposal.selectedEvents.join(", ") : "None"}
            </code>
          </dd>
        </div>
        <div>
          <dt>Admitted draft</dt>
          <dd>
            <code>{proposal.admittedEpochId ?? "None"}</code>
          </dd>
        </div>
      </dl>
      {proposal.blockers.length > 0 ? (
        <div className="proposal-blockers" role="status">
          <strong>Blocking predicates</strong>
          <ul>
            {proposal.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {proposal.diagnostics.length > 0 ? (
        <details className="evidence-section" open>
          <summary>
            <h4>Admission diagnostics</h4>
            <span>{formatCount(proposal.diagnostics.length, "record")}</span>
          </summary>
          <DiscoveryTable
            columns={DIAGNOSTIC_COLUMNS}
            emptyLabel="No admission diagnostics"
            keyOf={(item) => `${item.phase}:${item.ruleId}:${item.instancePointer}:${item.code}`}
            label="diagnostic"
            rows={proposal.diagnostics}
          />
        </details>
      ) : null}
      {proposal.policySource !== null ? (
        <details className="policy-source" open>
          <summary>
            <FileJson2 aria-hidden="true" />
            Admitted policy source
          </summary>
          <section
            className="policy-source-scroll"
            aria-label="Admitted policy source"
            // biome-ignore lint/a11y/noNoninteractiveTabindex: Safari requires keyboard access to scroll regions.
            tabIndex={0}
          >
            <pre>{formatPolicySource(proposal.policySource)}</pre>
          </section>
        </details>
      ) : null}
      <details className="proposal-nonclaims">
        <summary>Authority boundaries</summary>
        <ul>
          {proposal.nonClaims.map((nonClaim) => (
            <li key={nonClaim}>{nonClaim}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
