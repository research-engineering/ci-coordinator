import {
  CheckCircle2,
  ExternalLink,
  KeyRound,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Zap,
} from "lucide-react";
import type { ConfigActivationFailure } from "../../api/configActivation/client";
import type { ControlPlaneRole, ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type {
  ExpectedActiveEpoch,
  RepositoryAttestationFailure,
} from "../../api/repositoryAttestation/client";
import { operationIdIsAdmitted } from "../../api/shared/operationId";
import type { WorkbenchScope } from "../../api/workbench/client";
import type { WorkflowDiscoveryReport } from "../../api/workflowDiscovery/schema";
import { StatusBadge } from "../../components/StatusBadge";
import { useConfigActivation } from "./useConfigActivation";
import { useRepositoryAttestation } from "./useRepositoryAttestation";

interface RepositoryActivationControlProps {
  readonly scope: WorkbenchScope;
  readonly proposal: WorkflowDiscoveryReport["proposal"];
  readonly expectedActive: ExpectedActiveEpoch | null | undefined;
  readonly session: ControlPlaneSession | undefined;
}

export function RepositoryActivationControl({
  scope,
  proposal,
  expectedActive,
  session,
}: RepositoryActivationControlProps) {
  if (proposal.state !== "reviewable") return null;
  if (!session) {
    return (
      <div className="proposal-review-control">
        <KeyRound aria-hidden="true" />
        <span>Administrator session required to verify and activate this proposal</span>
      </div>
    );
  }
  if (expectedActive === undefined) {
    return (
      <div className="proposal-review-control">
        <RefreshCw aria-hidden="true" />
        <span>Load the active configuration revision before continuing</span>
      </div>
    );
  }
  if (proposal.admittedEpochId === null) {
    return (
      <div className="proposal-review-control">
        <RefreshCw aria-hidden="true" />
        <span>The admitted proposal has no activation target</span>
      </div>
    );
  }
  return (
    <AdmittedRepositoryActivationControl
      expectedActive={expectedActive}
      manifestId={proposal.manifestId}
      scope={scope}
      session={session}
      targetEpochId={proposal.admittedEpochId}
    />
  );
}

interface AdmittedRepositoryActivationControlProps {
  readonly scope: WorkbenchScope;
  readonly expectedActive: ExpectedActiveEpoch | null;
  readonly manifestId: string;
  readonly session: ControlPlaneSession;
  readonly targetEpochId: string;
}

function AdmittedRepositoryActivationControl({
  scope,
  expectedActive,
  manifestId,
  session,
  targetEpochId,
}: AdmittedRepositoryActivationControlProps) {
  const attestation = useRepositoryAttestation({
    csrfToken: session.csrfToken,
    expectedActive,
    manifestId,
    scope,
  });
  const activation = useConfigActivation({
    csrfToken: session.csrfToken,
    expectedRevision: expectedActive?.revision ?? null,
    manifestId,
    scope,
    targetEpochId,
  });
  const callbackOperationId = reviewedOperationId(scope, manifestId);
  const retainedReview =
    attestation.state.kind === "settled" && attestation.state.result.kind === "already_reviewed";
  const canConfigure = hasRole(session, "configure");
  const canActivate = hasRole(session, "activate");
  const canAttemptActivation = retainedReview;

  if (activation.state.kind === "settled" && activation.state.result.kind === "complete") {
    const result = activation.state.result.activation;
    return (
      <div className="proposal-review-result" role="status">
        <div className="proposal-review-summary">
          <CheckCircle2 aria-hidden="true" />
          <div>
            <strong>
              {result.duplicate ? "Configuration already active" : "Configuration activated"}
            </strong>
            <span>Revision {result.revision} is now authoritative</span>
          </div>
          <StatusBadge tone="positive">active</StatusBadge>
        </div>
      </div>
    );
  }

  const attestationFailure =
    attestation.state.kind === "settled" && attestation.state.result.kind !== "already_reviewed"
      ? attestation.state.result.kind
      : undefined;
  const activationFailure =
    activation.state.kind === "settled" && activation.state.result.kind !== "complete"
      ? activation.state.result.kind
      : undefined;

  return (
    <div className="proposal-activation-flow">
      <div className="proposal-activation-step">
        <span className="proposal-activation-index">1</span>
        <div>
          <strong>Verify repository authority</strong>
          <span>GitHub confirms current maintain or admin permission for this exact proposal.</span>
        </div>
        {canAttemptActivation ? (
          <StatusBadge tone="positive">reviewed</StatusBadge>
        ) : canConfigure ? (
          <button
            type="button"
            className="button button--secondary"
            disabled={
              attestation.state.kind === "starting" || attestation.state.kind === "redirecting"
            }
            onClick={() => attestation.start(callbackOperationId)}
          >
            {attestation.state.kind === "starting" || attestation.state.kind === "redirecting" ? (
              <LoaderCircle className="button-icon spin" aria-hidden="true" />
            ) : (
              <ExternalLink className="button-icon" aria-hidden="true" />
            )}
            {attestation.state.kind === "redirecting"
              ? "Opening GitHub"
              : callbackOperationId
                ? "Confirm review"
                : "Verify authority"}
          </button>
        ) : (
          <StatusBadge tone="warning">configure role required</StatusBadge>
        )}
      </div>

      <div className="proposal-activation-step">
        <span className="proposal-activation-index">2</span>
        <div>
          <strong>Activate configuration</strong>
          <span>The coordinator rechecks the retained review and current GitHub App evidence.</span>
        </div>
        {canAttemptActivation && canActivate ? (
          <button
            type="button"
            className="button button--primary"
            disabled={activation.state.kind === "submitting"}
            onClick={() => activation.submit()}
          >
            {activation.state.kind === "submitting" ? (
              <LoaderCircle className="button-icon spin" aria-hidden="true" />
            ) : (
              <Zap className="button-icon" aria-hidden="true" />
            )}
            {activation.state.kind === "submitting" ? "Activating" : "Activate proposal"}
          </button>
        ) : (
          <StatusBadge tone={canAttemptActivation ? "warning" : "neutral"}>
            {canAttemptActivation ? "activate role required" : "awaiting review"}
          </StatusBadge>
        )}
      </div>

      {callbackOperationId ? (
        <div className="proposal-activation-notice" role="status">
          <ShieldCheck aria-hidden="true" />
          <span>GitHub returned control. Confirm the retained review before activation.</span>
        </div>
      ) : null}
      {attestationFailure ? (
        <FailureNotice
          label={attestationFailureLabel(attestationFailure)}
          retry={isRetryableAttestation(attestationFailure) ? attestation.retry : undefined}
          retryLabel="Retry repository verification"
        />
      ) : null}
      {activationFailure ? (
        <FailureNotice
          label={activationFailureLabel(activationFailure)}
          retry={isRetryableActivation(activationFailure) ? activation.retry : undefined}
          retryLabel="Retry configuration activation"
        />
      ) : null}
    </div>
  );
}

function FailureNotice({
  label,
  retry,
  retryLabel,
}: {
  readonly label: string;
  readonly retry: (() => void) | undefined;
  readonly retryLabel: string;
}) {
  return (
    <div className="proposal-review-failure" role="alert">
      <span>{label}</span>
      {retry ? (
        <button
          type="button"
          className="icon-button"
          aria-label={retryLabel}
          title={retryLabel}
          onClick={retry}
        >
          <RefreshCw aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

function hasRole(session: ControlPlaneSession, role: ControlPlaneRole): boolean {
  return session.roles.includes(role);
}

function reviewedOperationId(scope: WorkbenchScope, manifestId: string): string | undefined {
  const query = new URLSearchParams(globalThis.location.search);
  const operationId = query.get("reviewOperationId");
  return operationId !== null &&
    query.get("repositoryAttestation") === "reviewed" &&
    query.get("installationId") === String(scope.installationId) &&
    query.get("repositoryId") === String(scope.repositoryId) &&
    query.get("proposalManifestId") === manifestId &&
    operationIdIsAdmitted(operationId)
    ? operationId
    : undefined;
}

function isRetryableAttestation(kind: RepositoryAttestationFailure): boolean {
  return kind === "overloaded" || kind === "unavailable" || kind === "network-failure";
}

function isRetryableActivation(kind: ConfigActivationFailure): boolean {
  return kind === "overloaded" || kind === "unavailable" || kind === "network-failure";
}

function attestationFailureLabel(kind: RepositoryAttestationFailure): string {
  switch (kind) {
    case "already_reviewed":
      return "Repository review is already retained";
    case "unauthenticated":
      return "Administrator session expired";
    case "forbidden":
      return "Configure role and repository maintain permission are required";
    case "blocked":
      return "Proposal is no longer reviewable";
    case "stale":
      return "Proposal changed; refresh discovery";
    case "diff_limit":
      return "Semantic diff exceeds its admitted bound";
    case "baseline_conflict":
      return "Active configuration changed; refresh the workbench";
    case "operation_conflict":
      return "Verification operation conflicts with retained evidence";
    case "epoch_conflict":
      return "Draft epoch conflicts with retained state";
    case "overloaded":
      return "Verification service is busy; retry shortly";
    case "unavailable":
      return "Verification service is unavailable";
    case "rate_limited":
      return "GitHub rate limited the verification request";
    case "invalid_callback":
    case "replayed":
      return "GitHub verification callback was rejected";
    case "invalid-response":
      return "Verification response was rejected";
    case "network-failure":
      return "Verification endpoint could not be reached";
  }
}

function activationFailureLabel(kind: ConfigActivationFailure): string {
  switch (kind) {
    case "unauthenticated":
      return "Administrator session expired";
    case "forbidden":
      return "Activate role is required";
    case "attestation_invalid":
      return "Repository review is missing, expired, or no longer matches this proposal";
    case "revision_conflict":
      return "Active configuration changed; refresh the workbench";
    case "target_unavailable":
      return "The proposed configuration epoch is unavailable";
    case "conflict":
      return "Activation operation conflicts with retained evidence";
    case "coverage_reducing":
      return "Activation would reduce required validation coverage";
    case "coverage_unproven":
      return "Required validation coverage is not proved";
    case "overloaded":
      return "Activation service is busy; retry shortly";
    case "unavailable":
      return "Activation service is unavailable";
    case "invalid_config":
      return "Activation request violates the configuration contract";
    case "invalid-response":
      return "Activation response was rejected";
    case "network-failure":
      return "Activation endpoint could not be reached";
  }
}
