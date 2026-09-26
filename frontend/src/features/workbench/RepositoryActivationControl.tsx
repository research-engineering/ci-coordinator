import {
  CheckCircle2,
  ExternalLink,
  KeyRound,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Zap,
} from "lucide-react";
import { useLayoutEffect, useState } from "react";
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
  readonly authorityRevision: number;
  readonly snapshotReadRevision: number;
  readonly snapshotLoading: boolean;
  readonly onConfirmed: (active?: ExpectedActiveEpoch) => void;
  readonly onRefreshSnapshot: () => void;
  readonly onLockedChange: (locked: boolean) => void;
}

export function RepositoryActivationControl({
  scope,
  proposal,
  expectedActive,
  session,
  authorityRevision,
  snapshotReadRevision,
  snapshotLoading,
  onConfirmed,
  onRefreshSnapshot,
  onLockedChange,
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
      key={`${scope.installationId}:${scope.repositoryId}:${authorityRevision}`}
      expectedActive={expectedActive}
      manifestId={proposal.manifestId}
      scope={scope}
      session={session}
      targetEpochId={proposal.admittedEpochId}
      authorityRevision={authorityRevision}
      snapshotReadRevision={snapshotReadRevision}
      snapshotLoading={snapshotLoading}
      onConfirmed={onConfirmed}
      onRefreshSnapshot={onRefreshSnapshot}
      onLockedChange={onLockedChange}
    />
  );
}

interface AdmittedRepositoryActivationControlProps
  extends Pick<
    RepositoryActivationControlProps,
    | "authorityRevision"
    | "snapshotReadRevision"
    | "snapshotLoading"
    | "onConfirmed"
    | "onRefreshSnapshot"
    | "onLockedChange"
  > {
  readonly scope: WorkbenchScope;
  readonly expectedActive: ExpectedActiveEpoch | null | undefined;
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
  authorityRevision,
  snapshotReadRevision,
  snapshotLoading,
  onConfirmed,
  onRefreshSnapshot,
  onLockedChange,
}: AdmittedRepositoryActivationControlProps) {
  const [conflictRevision, setConflictRevision] = useState<number>();
  const [reviewInvalidated, setReviewInvalidated] = useState(false);
  const [consumedHint, setConsumedHint] = useState<string>();
  const baselineReady =
    expectedActive !== undefined &&
    (conflictRevision === undefined || snapshotReadRevision > conflictRevision);
  const canConfigure = hasRole(session, "configure");
  const canActivate = hasRole(session, "activate");
  const attestation = useRepositoryAttestation({
    csrfToken: session.csrfToken,
    expectedActive: baselineReady ? expectedActive : undefined,
    manifestId,
    scope,
    authorityRevision,
    expiresAt: session.expiresAt,
    allowed: canConfigure,
    onConflict: () => setConflictRevision(snapshotReadRevision),
  });
  const canAttemptActivation = attestation.reviewed && !reviewInvalidated && baselineReady;
  const activation = useConfigActivation({
    csrfToken: session.csrfToken,
    expectedRevision: baselineReady ? (expectedActive?.revision ?? null) : undefined,
    manifestId,
    scope,
    targetEpochId,
    authorityRevision,
    expiresAt: session.expiresAt,
    allowed: canActivate,
    newCommandAllowed: canAttemptActivation,
    onConfirmed: (receipt) => onConfirmed({ epochId: receipt.epochId, revision: receipt.revision }),
    onConflict: () => {
      setConflictRevision(snapshotReadRevision);
      setReviewInvalidated(true);
    },
  });
  const locked = attestation.locked || activation.locked;
  useLayoutEffect(() => {
    onLockedChange(locked);
    return () => onLockedChange(false);
  }, [locked, onLockedChange]);
  const callbackOperationId = reviewedOperationId(scope, manifestId);
  const freshVerification =
    reviewInvalidated ||
    (attestation.state.kind === "settled" && !attestation.uncertain && !canAttemptActivation);
  function verify() {
    if (!baselineReady || locked || !canConfigure) return;
    setReviewInvalidated(false);
    if (freshVerification) {
      setConsumedHint(callbackOperationId);
      attestation.start();
    } else
      attestation.start(callbackOperationId === consumedHint ? undefined : callbackOperationId);
  }

  if (
    activation.state.kind === "settled" &&
    !activation.uncertain &&
    activation.state.result.kind === "complete"
  ) {
    const result = activation.state.result.activation;
    return (
      <div className="proposal-review-result" role="status">
        <div className="proposal-review-summary">
          <CheckCircle2 aria-hidden="true" />
          <div>
            <strong>
              {result.duplicate ? "Activation already recorded" : "Configuration activated"}
            </strong>
            <span>Activation recorded at revision {result.revision}</span>
          </div>
          <StatusBadge tone="positive">recorded</StatusBadge>
        </div>
        <CurrentConfiguration
          active={expectedActive}
          loading={snapshotLoading}
          onRefresh={onRefreshSnapshot}
        />
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
      {!baselineReady ? (
        <div className="proposal-activation-notice" role="status">
          <span>A fresh active configuration read is required before a new command.</span>
          <button
            type="button"
            className="icon-button"
            onClick={onRefreshSnapshot}
            title="Refresh active configuration"
            aria-label="Refresh active configuration"
            disabled={snapshotLoading}
          >
            <RefreshCw aria-hidden="true" />
          </button>
        </div>
      ) : null}
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
            disabled={!baselineReady || locked}
            onClick={verify}
          >
            {attestation.state.kind === "starting" || attestation.state.kind === "redirecting" ? (
              <LoaderCircle className="button-icon spin" aria-hidden="true" />
            ) : (
              <ExternalLink className="button-icon" aria-hidden="true" />
            )}
            {attestation.state.kind === "redirecting"
              ? "Opening GitHub"
              : freshVerification
                ? "Verify authority again"
                : callbackOperationId && callbackOperationId !== consumedHint
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
            disabled={locked}
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

      {callbackOperationId && callbackOperationId !== consumedHint ? (
        <div className="proposal-activation-notice" role="status">
          <ShieldCheck aria-hidden="true" />
          <span>GitHub returned control. Confirm the retained review before activation.</span>
        </div>
      ) : null}
      {attestationFailure ? (
        <FailureNotice
          label={attestationFailureLabel(attestationFailure)}
          retry={
            attestation.uncertain && canConfigure && !attestation.expired
              ? attestation.retry
              : undefined
          }
          retryLabel="Retry repository verification"
        />
      ) : null}
      {activationFailure ? (
        <FailureNotice
          label={activationFailureLabel(activationFailure)}
          retry={
            activation.uncertain && canActivate && !activation.expired
              ? activation.retry
              : undefined
          }
          retryLabel="Retry configuration activation"
        />
      ) : null}
      {activation.uncertain || attestation.uncertain ? (
        <p role="status">
          The outcome is unconfirmed. The original command is retained; a new read does not replace
          it.
          {activation.expired || attestation.expired ? " The administrator session expired." : ""}
        </p>
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
  return (
    session.roles.includes(role) &&
    Number.isFinite(Date.parse(session.expiresAt)) &&
    Date.parse(session.expiresAt) > Date.now()
  );
}

function CurrentConfiguration({
  active,
  loading,
  onRefresh,
}: {
  readonly active: ExpectedActiveEpoch | null | undefined;
  readonly loading: boolean;
  readonly onRefresh: () => void;
}) {
  return (
    <div className="proposal-activation-notice" role="status">
      <span>
        {active === undefined ? (
          "Current configuration is unproved."
        ) : active === null ? (
          "No active configuration was observed."
        ) : (
          <>
            Current observed revision {active.revision}, epoch{" "}
            <code title={active.epochId}>{active.epochId.slice(0, 12)}</code>.
          </>
        )}
      </span>
      <button
        type="button"
        className="icon-button"
        title="Refresh active configuration"
        aria-label="Refresh active configuration"
        disabled={loading}
        onClick={onRefresh}
      >
        <RefreshCw aria-hidden="true" />
      </button>
    </div>
  );
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
