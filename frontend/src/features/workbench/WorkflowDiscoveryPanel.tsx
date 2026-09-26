import {
  AlertTriangle,
  Ban,
  GitCommitHorizontal,
  KeyRound,
  RefreshCw,
  Search,
  ServerOff,
} from "lucide-react";
import { type FormEvent, useState } from "react";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { ExpectedActiveEpoch } from "../../api/repositoryAttestation/client";
import type { WorkbenchScope } from "../../api/workbench/client";
import type { WorkflowDiscoveryResult } from "../../api/workflowDiscovery/client";
import { LoadingState } from "../../components/LoadingState";
import { useWorkflowDiscovery } from "./useWorkflowDiscovery";
import { WorkflowDiscoveryEvidence } from "./WorkflowDiscoveryEvidence";
import { WorkflowDiscoveryInventory } from "./WorkflowDiscoveryInventory";
import { WorkflowDiscoveryProposal } from "./WorkflowDiscoveryProposal";
import { WorkflowDiscoverySummary } from "./WorkflowDiscoverySummary";

interface WorkflowDiscoveryPanelProps {
  readonly scope: WorkbenchScope;
  readonly session: ControlPlaneSession | undefined;
  readonly expectedActive: ExpectedActiveEpoch | null | undefined;
  readonly authorityRevision: number;
  readonly snapshotReadRevision: number;
  readonly snapshotLoading: boolean;
  readonly onConfirmed: (active?: ExpectedActiveEpoch) => void;
  readonly onRefreshSnapshot: () => void;
}

export function WorkflowDiscoveryPanel({
  scope,
  session,
  expectedActive,
  authorityRevision,
  snapshotReadRevision,
  snapshotLoading,
  onConfirmed,
  onRefreshSnapshot,
}: WorkflowDiscoveryPanelProps) {
  const [requestedRevision, setRequestedRevision] = useState<string | undefined>();
  const [revisionInput, setRevisionInput] = useState("");
  const [revisionError, setRevisionError] = useState<string>();
  const [commandLocked, setCommandLocked] = useState(false);
  const query = useWorkflowDiscovery(scope, requestedRevision);
  const result = query.state.kind === "settled" ? query.state.result : undefined;

  function submitRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (commandLocked) return;
    const revision = revisionInput.trim();
    if (revision !== "" && !/^[0-9a-f]{40}$/.test(revision)) {
      setRevisionError("Enter an exact 40-character lowercase commit SHA.");
      return;
    }
    setRevisionError(undefined);
    const nextRevision = revision === "" ? undefined : revision;
    if (nextRevision === requestedRevision) query.refresh();
    else setRequestedRevision(nextRevision);
  }

  function useCurrentHead() {
    if (commandLocked) return;
    setRevisionInput("");
    setRevisionError(undefined);
    if (requestedRevision === undefined) query.refresh();
    else setRequestedRevision(undefined);
  }

  return (
    <section className="workflow-discovery" aria-labelledby="workflow-discovery-heading">
      <header className="discovery-header">
        <div>
          <p className="eyebrow">Exact-commit analysis</p>
          <h2 id="workflow-discovery-heading">Workflow discovery</h2>
        </div>
        <button
          type="button"
          className="icon-button"
          onClick={query.refresh}
          title="Refresh workflow discovery"
          disabled={commandLocked}
        >
          <RefreshCw aria-hidden="true" />
        </button>
      </header>
      <form className="revision-form" onSubmit={submitRevision} noValidate>
        <label htmlFor="workflow-revision">
          Revision
          <span className="input-with-icon">
            <GitCommitHorizontal aria-hidden="true" />
            <input
              id="workflow-revision"
              type="text"
              inputMode="text"
              autoComplete="off"
              maxLength={40}
              pattern="[0-9a-f]{40}"
              placeholder="Current default-branch head"
              spellCheck={false}
              value={revisionInput}
              disabled={commandLocked}
              onChange={(event) => setRevisionInput(event.target.value)}
              aria-invalid={revisionError !== undefined}
              aria-describedby={revisionError ? "workflow-revision-error" : undefined}
            />
          </span>
        </label>
        <button type="submit" className="button button--primary" disabled={commandLocked}>
          <Search className="button-icon" aria-hidden="true" />
          Scan
        </button>
        {requestedRevision !== undefined ? (
          <button
            type="button"
            className="button button--secondary"
            onClick={useCurrentHead}
            disabled={commandLocked}
          >
            Use current head
          </button>
        ) : null}
        {revisionError ? (
          <p id="workflow-revision-error" className="form-error">
            {revisionError}
          </p>
        ) : null}
      </form>
      {query.state.kind === "loading" ? (
        <LoadingState label="Reading the exact workflow snapshot" />
      ) : result?.kind === "ready" ? (
        <>
          <div className="discovery-coordinate">
            <span>{reportName(result.report)}</span>
            <code>{result.report.revision}</code>
          </div>
          {!result.report.complete || !result.report.localGraphClosed ? (
            <div className="discovery-warning" role="status">
              <AlertTriangle aria-hidden="true" />
              <span>
                The report is incomplete or its local call graph is open. Unknowns remain visible,
                and policy authority stays blocked where required.
              </span>
            </div>
          ) : null}
          <WorkflowDiscoverySummary report={result.report} />
          <WorkflowDiscoveryInventory report={result.report} />
          <WorkflowDiscoveryEvidence report={result.report} />
          <WorkflowDiscoveryProposal
            expectedActive={expectedActive}
            report={result.report}
            scope={scope}
            session={session}
            authorityRevision={authorityRevision}
            snapshotReadRevision={snapshotReadRevision}
            snapshotLoading={snapshotLoading}
            onConfirmed={onConfirmed}
            onRefreshSnapshot={onRefreshSnapshot}
            onLockedChange={setCommandLocked}
          />
        </>
      ) : result ? (
        <DiscoveryFailure result={result} onRetry={query.refresh} />
      ) : null}
    </section>
  );
}

function DiscoveryFailure({
  result,
  onRetry,
}: {
  readonly result: Exclude<WorkflowDiscoveryResult, { readonly kind: "ready" }>;
  readonly onRetry: () => void;
}) {
  const state = failureState(result);
  const Icon = state.icon;
  return (
    <div className="discovery-state" aria-live="polite">
      <Icon aria-hidden="true" />
      <strong>{state.title}</strong>
      <span>{state.description}</span>
      <button type="button" className="button button--secondary" onClick={onRetry}>
        <RefreshCw className="button-icon" aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}

function failureState(result: Exclude<WorkflowDiscoveryResult, { readonly kind: "ready" }>) {
  switch (result.kind) {
    case "unauthenticated":
      return {
        description: "The server did not establish an operator identity.",
        icon: KeyRound,
        title: "Authentication required",
      };
    case "forbidden":
      return {
        description: "The operator is not authorized for this repository scope.",
        icon: Ban,
        title: "Repository access denied",
      };
    case "invalid-revision":
      return {
        description: "Enter the full commit SHA, not a branch name or shortened hash.",
        icon: AlertTriangle,
        title: "Revision rejected",
      };
    case "not-found":
      return {
        description: "The repository or exact revision is not visible to the installation.",
        icon: Search,
        title: "Snapshot not found",
      };
    case "rate-limited":
      return {
        description: "GitHub's request limit was reached. Wait before retrying the scan.",
        icon: ServerOff,
        title: "Provider rate limit reached",
      };
    case "overloaded":
      return {
        description: "Another workflow scan is running. Retry shortly.",
        icon: ServerOff,
        title: "Discovery is busy",
      };
    case "unavailable":
      return {
        description: `The trustworthy scan could not be completed (${result.reason}).`,
        icon: ServerOff,
        title: "Discovery unavailable",
      };
    case "invalid-response":
      return {
        description:
          "The workflow scan could not be validated. Retry; if this persists, contact an administrator.",
        icon: AlertTriangle,
        title: "Response rejected",
      };
    case "network-failure":
      return {
        description: "The discovery endpoint could not be reached before its deadline.",
        icon: ServerOff,
        title: "Connection failed",
      };
  }
}

function reportName(
  report: Extract<WorkflowDiscoveryResult, { readonly kind: "ready" }>["report"],
) {
  return `${report.repository.owner}/${report.repository.name}`;
}
