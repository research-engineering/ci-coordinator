import { useState } from "react";
import type { GovernanceBaselineRecord } from "../../api/governanceBaseline/schema";
import type { GovernanceObservation } from "../../api/governanceObservation/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { GovernanceBaselineApprovalControl } from "./GovernanceBaselineApprovalControl";
import { GovernanceBaselineEvidence } from "./GovernanceBaselineEvidence";
import { useGovernanceBaselineApproval } from "./useGovernanceBaselineApproval";

interface GovernanceBaselineControlProps {
  readonly authorityRevision: number;
  readonly baseline: GovernanceBaselineRecord | null;
  readonly csrfToken: string | undefined;
  readonly disabled: boolean;
  readonly generation: string;
  readonly onComplete: () => void;
  readonly observation: GovernanceObservation;
  readonly scope: WorkbenchScope;
}

export function GovernanceBaselineControl({
  authorityRevision,
  baseline,
  csrfToken,
  disabled,
  generation,
  onComplete,
  observation,
  scope,
}: GovernanceBaselineControlProps) {
  const approval = useGovernanceBaselineApproval({
    authorityRevision,
    baseline,
    csrfToken,
    generation,
    onComplete,
    observation,
    scope,
  });
  const [reason, setReason] = useState("");

  return (
    <section className="governance-baseline" aria-labelledby="governance-baseline-heading">
      <header className="governance-baseline-header">
        <div>
          <p className="eyebrow">Expected state</p>
          <h3 id="governance-baseline-heading">Governance baseline</h3>
        </div>
      </header>
      <GovernanceBaselineEvidence record={baseline} />
      <GovernanceBaselineApprovalControl
        baseline={baseline}
        csrfToken={csrfToken}
        disabled={disabled}
        onReasonChange={setReason}
        onRetry={approval.retry}
        onSubmit={() => approval.submit(reason)}
        reason={reason}
        submission={approval.submission}
      />
    </section>
  );
}
