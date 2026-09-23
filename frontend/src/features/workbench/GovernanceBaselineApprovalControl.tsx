import { AlertTriangle, CheckCircle2, KeyRound, LoaderCircle, RefreshCw, Save } from "lucide-react";
import type { GovernanceBaselineApprovalResult } from "../../api/governanceBaseline/client";
import { governanceBaselineReasonIsAdmitted } from "../../api/governanceBaseline/reason";
import type { GovernanceBaselineRecord } from "../../api/governanceBaseline/schema";
import type { GovernanceBaselineSubmissionState } from "./useGovernanceBaselineApproval";

interface GovernanceBaselineApprovalControlProps {
  readonly baseline: GovernanceBaselineRecord | null;
  readonly csrfToken: string | undefined;
  readonly disabled: boolean;
  readonly onReasonChange: (value: string) => void;
  readonly onRetry: () => void;
  readonly onSubmit: () => void;
  readonly reason: string;
  readonly submission: GovernanceBaselineSubmissionState;
}

export function GovernanceBaselineApprovalControl({
  baseline,
  csrfToken,
  disabled,
  onReasonChange,
  onRetry,
  onSubmit,
  reason,
  submission,
}: GovernanceBaselineApprovalControlProps) {
  if (!csrfToken) {
    return (
      <div className="governance-baseline-auth">
        <KeyRound aria-hidden="true" />
        <span>Administrator session required for baseline approval</span>
      </div>
    );
  }
  const admittedReason = governanceBaselineReasonIsAdmitted(reason);
  const submitting = submission.kind === "submitting";
  const result = submission.kind === "settled" ? submission.result : undefined;
  return (
    <form
      className="governance-baseline-approval"
      onSubmit={(event) => {
        event.preventDefault();
        if (admittedReason && !submitting) onSubmit();
      }}
    >
      <label htmlFor="governance-baseline-reason">Approval reason</label>
      <div className="governance-baseline-command">
        <input
          id="governance-baseline-reason"
          aria-describedby="governance-baseline-reason-help"
          autoComplete="off"
          disabled={disabled}
          maxLength={1_024}
          onChange={(event) => onReasonChange(event.currentTarget.value)}
          placeholder="Repository governance intent"
          value={reason}
        />
        <button
          type="submit"
          className="button button--primary"
          disabled={disabled || !admittedReason || submitting}
        >
          {submitting ? (
            <LoaderCircle className="button-icon spin" aria-hidden="true" />
          ) : (
            <Save className="button-icon" aria-hidden="true" />
          )}
          {submitting ? "Approving" : baseline ? "Replace baseline" : "Approve baseline"}
        </button>
      </div>
      <p id="governance-baseline-reason-help" className="governance-reason-help">
        {admittedReason
          ? "The reason will be retained with this approval."
          : "Enter a reason of up to 1,024 UTF-8 bytes, without surrounding whitespace or control characters."}
      </p>
      {result?.kind === "complete" ? (
        <div className="governance-baseline-outcome" role="status">
          <CheckCircle2 aria-hidden="true" />
          <span>{successLabel(result)}</span>
        </div>
      ) : result ? (
        <div
          className="governance-baseline-outcome governance-baseline-outcome--failure"
          role="alert"
        >
          <AlertTriangle aria-hidden="true" />
          <span>{failureLabel(result.kind)}</span>
          {result.kind === "overloaded" ||
          result.kind === "unavailable" ||
          result.kind === "network-failure" ? (
            <button
              type="button"
              className="icon-button"
              aria-label="Retry baseline approval"
              title="Retry baseline approval"
              disabled={disabled}
              onClick={onRetry}
            >
              <RefreshCw aria-hidden="true" />
            </button>
          ) : null}
        </div>
      ) : null}
    </form>
  );
}

function successLabel(
  result: Extract<GovernanceBaselineApprovalResult, { readonly kind: "complete" }>,
): string {
  switch (result.approval.state) {
    case "accepted":
      return "Expected governance state approved";
    case "duplicate":
      return "Approval already retained";
    case "unchanged":
      return "Expected governance state unchanged";
  }
}

function failureLabel(
  kind: Exclude<GovernanceBaselineApprovalResult, { kind: "complete" }>["kind"],
) {
  switch (kind) {
    case "unauthenticated":
      return "Session expired";
    case "forbidden":
      return "Repository manager access required";
    case "stale":
      return "Observed governance changed; refresh evidence";
    case "baseline_conflict":
      return "Approved baseline changed; refresh baseline";
    case "operation_conflict":
      return "Approval operation conflicts with retained evidence";
    case "overloaded":
      return "Baseline service is busy; retry shortly";
    case "unavailable":
      return "Baseline service unavailable";
    case "invalid-response":
      return "Baseline response rejected";
    case "network-failure":
      return "Baseline endpoint could not be reached";
  }
}
