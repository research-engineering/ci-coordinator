import {
  AlertTriangle,
  Ban,
  KeyRound,
  RefreshCw,
  Search,
  ServerOff,
  type ShieldCheck,
} from "lucide-react";
import type { GovernanceComparisonResult } from "../../api/governanceComparison/client";
import type { WorkbenchScope } from "../../api/workbench/client";
import { LoadingState } from "../../components/LoadingState";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateTime } from "../../domain/format";
import { GovernanceBaselineControl } from "./GovernanceBaselineControl";
import { GovernanceComparisonEvidence } from "./GovernanceComparisonEvidence";
import { GovernanceRuleEvidence } from "./GovernanceRuleEvidence";
import { useGovernanceComparison } from "./useGovernanceComparison";

interface GovernanceObservationPanelProps {
  readonly authorityRevision: number;
  readonly csrfToken: string | undefined;
  readonly scope: WorkbenchScope;
}

export function GovernanceObservationPanel({
  authorityRevision,
  csrfToken,
  scope,
}: GovernanceObservationPanelProps) {
  const query = useGovernanceComparison(scope);
  const result = query.state.kind === "settled" ? query.state.result : undefined;
  const refreshing = query.state.kind === "settled" && query.state.refreshing;

  return (
    <section
      className="governance-observation"
      aria-busy={refreshing}
      aria-labelledby="governance-observation-heading"
    >
      <header className="governance-header">
        <div>
          <p className="eyebrow">Provider evidence</p>
          <h2 id="governance-observation-heading">Effective governance</h2>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="Refresh effective governance"
          title="Refresh effective governance"
          onClick={query.refresh}
        >
          <RefreshCw
            className={
              query.state.kind === "settled" && query.state.refreshing ? "spin" : undefined
            }
            aria-hidden="true"
          />
        </button>
      </header>
      {refreshing ? (
        <LoadingState
          compact
          label={
            result?.kind === "ready"
              ? "Refreshing governance evidence; previous observation shown"
              : "Refreshing governance evidence"
          }
        />
      ) : null}
      {query.state.kind === "loading" ? (
        <LoadingState label="Reading governance evidence" />
      ) : result?.kind === "ready" ? (
        <GovernanceResult
          authorityRevision={authorityRevision}
          csrfToken={csrfToken}
          generation={query.generation}
          onRefresh={query.refresh}
          refreshing={refreshing}
          result={result}
          scope={scope}
        />
      ) : result ? (
        <GovernanceFailure result={result} onRetry={query.refresh} />
      ) : null}
    </section>
  );
}

function GovernanceResult({
  authorityRevision,
  csrfToken,
  generation,
  onRefresh,
  refreshing,
  result,
  scope,
}: {
  readonly authorityRevision: number;
  readonly csrfToken: string | undefined;
  readonly generation: string;
  readonly onRefresh: () => void;
  readonly refreshing: boolean;
  readonly result: Extract<GovernanceComparisonResult, { readonly kind: "ready" }>;
  readonly scope: WorkbenchScope;
}) {
  const observation = result.evidence.observation;
  return (
    <>
      <div className="governance-coordinate">
        <div>
          <strong>{observation.repository.fullName}</strong>
          <span>
            Default branch <code>{observation.repository.defaultBranch}</code>
          </span>
        </div>
        <div className="governance-statuses">
          <StatusBadge tone="warning">Observation only</StatusBadge>
          <StatusBadge tone="info">Best effort</StatusBadge>
        </div>
      </div>
      <dl className="governance-summary">
        <SummaryItem label="Effective rules" value={String(observation.rules.length)} />
        <SummaryItem label="GitHub API" value={observation.apiVersion} />
        <SummaryItem label="Observed" value={formatDateTime(observation.observedAt)} />
        <SummaryItem label="State digest" value={observation.stateDigest} code />
      </dl>
      <div className="governance-boundary" role="status">
        <AlertTriangle aria-hidden="true" />
        <span>
          GitHub rules can change during collection. This observation is not an atomic snapshot, an
          approved policy, or a compliance verdict.
        </span>
      </div>
      <GovernanceComparisonEvidence evidence={result.evidence} />
      <GovernanceBaselineControl
        authorityRevision={authorityRevision}
        baseline={result.evidence.baseline}
        csrfToken={csrfToken}
        disabled={refreshing}
        generation={generation}
        onComplete={onRefresh}
        observation={observation}
        scope={scope}
      />
      {observation.rules.length === 0 ? (
        <GovernanceMessage
          icon={Search}
          title="No active rules reported"
          description="No active rules were reported during this best-effort traversal."
        />
      ) : (
        <section aria-labelledby="effective-rules-title">
          <h3 id="effective-rules-title" className="governance-rules-heading">
            Effective rules
          </h3>
          <GovernanceRuleEvidence rules={observation.rules} />
        </section>
      )}
    </>
  );
}

function SummaryItem({
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

function GovernanceFailure({
  result,
  onRetry,
}: {
  readonly result: Exclude<GovernanceComparisonResult, { readonly kind: "ready" }>;
  readonly onRetry: () => void;
}) {
  const state = failureState(result);
  return (
    <GovernanceMessage
      action={
        <button type="button" className="button button--secondary" onClick={onRetry}>
          <RefreshCw className="button-icon" aria-hidden="true" />
          Retry
        </button>
      }
      description={state.description}
      icon={state.icon}
      title={state.title}
    />
  );
}

function GovernanceMessage({
  action,
  description,
  icon: Icon,
  spin = false,
  title,
}: {
  readonly action?: React.ReactNode;
  readonly description: string;
  readonly icon: typeof ShieldCheck;
  readonly spin?: boolean;
  readonly title: string;
}) {
  return (
    <div className="governance-message" aria-live="polite">
      <Icon className={spin ? "spin" : undefined} aria-hidden="true" />
      <strong>{title}</strong>
      <span>{description}</span>
      {action}
    </div>
  );
}

function failureState(result: Exclude<GovernanceComparisonResult, { readonly kind: "ready" }>) {
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
    case "stale":
      return {
        description: "The approved baseline changed during observation. Read fresh evidence.",
        icon: AlertTriangle,
        title: "Governance evidence changed",
      };
    case "not-found":
      return {
        description: "The repository is not visible to the GitHub App installation.",
        icon: Search,
        title: "Repository not found",
      };
    case "rate-limited":
      return {
        description:
          result.retryAfterSeconds === undefined
            ? "GitHub temporarily refused the bounded read operation."
            : `GitHub temporarily refused the read. Retry after ${result.retryAfterSeconds} seconds.`,
        icon: ServerOff,
        title: "Provider rate limit reached",
      };
    case "unavailable":
      return {
        description: unavailableDescription(result.reason),
        icon: ServerOff,
        title: "Governance observation unavailable",
      };
    case "invalid-response":
      return {
        description:
          "The response could not be verified for this repository. Refresh the evidence; if this persists, contact an administrator.",
        icon: AlertTriangle,
        title: "Governance response rejected",
      };
    case "network-failure":
      return {
        description: "The governance endpoint could not be reached before its deadline.",
        icon: ServerOff,
        title: "Connection failed",
      };
  }
}

function unavailableDescription(
  reason: Extract<GovernanceComparisonResult, { readonly kind: "unavailable" }>["reason"],
): string {
  switch (reason) {
    case "malformed_provider_response":
      return "GitHub returned data outside the admitted governance contract.";
    case "provider_binding_mismatch":
      return "Repository identity changed or contradicted the requested provider scope.";
    case "observation_limit_exceeded":
      return "The provider traversal exceeded the admitted observation bounds.";
    case "unavailable":
      return "The provider observation could not be completed.";
  }
}
