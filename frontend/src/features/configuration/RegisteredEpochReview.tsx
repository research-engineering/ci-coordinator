import { ExternalLink, RefreshCw, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ConfigEpoch, ConfigScope, ConfigStatus } from "../../api/configLifecycle/schema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import {
  type RegisteredEpochReviewCommand,
  type RepositoryAttestationResult,
  startRegisteredEpochReview,
} from "../../api/repositoryAttestation/client";
import type { RegisteredEpochReviewHint } from "../workbench/navigation";
import { EpochPreview, useEpochPreview } from "./EpochPreview";
import type { ActivationCommand } from "./useConfigurationCommand";

type ReviewState =
  | { readonly kind: "idle" }
  | { readonly kind: "starting"; readonly command: RegisteredEpochReviewCommand }
  | {
      readonly kind: "settled";
      readonly command: RegisteredEpochReviewCommand;
      readonly result: Exclude<RepositoryAttestationResult, { readonly kind: "ready" }>;
      readonly uncertain: boolean;
    };

const UNCERTAIN = ["network-failure", "invalid-response", "unavailable", "overloaded"];
const REVIEW_FAILURES: Readonly<Partial<Record<string, string>>> = {
  forbidden: "Configure role and repository maintain permission are required.",
  target_missing: "The registered epoch was not found in this repository.",
  target_ineligible:
    "This epoch has no standalone registration; activate it through its workflow proposal.",
  source_mismatch: "The registered source differs from the selected epoch.",
  baseline_conflict: "The active configuration changed. Refresh and review again.",
  stale: "The repository head or workflow inventory changed. Refresh and review again.",
  blocked: "The repository workflow inventory is incomplete or not locally closed.",
  operation_conflict: "The review operation conflicts with retained evidence.",
  diff_limit: "The semantic diff exceeds its admitted bound.",
  rate_limited: "GitHub rate limited the review request.",
  unauthenticated: "Administrator session expired.",
};

// Starts the CI-018 GitHub step-up; GitHub returns control to the Configuration view.
export function RegisteredEpochReviewRequest({
  scope,
  status,
  epoch,
  session,
  onReviewed,
}: {
  readonly scope: ConfigScope;
  readonly status: ConfigStatus;
  readonly epoch: ConfigEpoch;
  readonly session: ControlPlaneSession;
  readonly onReviewed: (hint: RegisteredEpochReviewHint) => void;
}) {
  const [state, setState] = useState<ReviewState>({ kind: "idle" });
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => controller.current?.abort(), []);
  async function send(command: RegisteredEpochReviewCommand, wasUncertain: boolean) {
    if (controller.current || Date.parse(session.expiresAt) <= Date.now()) return;
    const request = new AbortController();
    controller.current = request;
    setState({ kind: "starting", command });
    const result = await startRegisteredEpochReview(command, session.csrfToken, request.signal);
    if (request.signal.aborted) return;
    controller.current = undefined;
    const expired = Date.parse(session.expiresAt) <= Date.now();
    if (result.kind === "ready" && !expired) {
      globalThis.location.assign(result.authorizationUrl);
      return;
    }
    const settled = result.kind === "ready" ? { kind: "unauthenticated" as const } : result;
    if (settled.kind === "already_reviewed" && !expired) {
      onReviewed({
        targetEpochId: command.targetEpochId,
        expectedSourceHash: command.expectedSourceHash,
        operationId: command.operationId,
        expectedActive: command.expectedActive,
      });
      setState({ kind: "idle" });
      return;
    }
    setState({
      kind: "settled",
      command,
      result: settled,
      uncertain: wasUncertain || expired || UNCERTAIN.includes(settled.kind),
    });
  }
  const uncertain = state.kind === "settled" && state.uncertain;
  return (
    <section className="configuration-review" aria-label="GitHub review">
      <h4>GitHub review</h4>
      <p>
        GitHub confirms maintain or admin permission for this exact registered source against the
        current active revision. Review does not change the active configuration.
      </p>
      {uncertain ? (
        <button
          type="button"
          className="button button--secondary"
          disabled={state.kind !== "settled"}
          onClick={() => {
            if (state.kind === "settled") void send(state.command, true);
          }}
        >
          <RefreshCw className="button-icon" aria-hidden="true" />
          Retry same review request
        </button>
      ) : (
        <button
          type="button"
          className="button button--secondary"
          disabled={state.kind === "starting"}
          onClick={() =>
            void send(
              {
                scope: { installationId: scope.installationId, repositoryId: scope.repositoryId },
                operationId: crypto.randomUUID(),
                targetEpochId: epoch.epochId,
                expectedSourceHash: epoch.sourceHash,
                expectedActive: status.active ? { ...status.active } : null,
              },
              false,
            )
          }
        >
          <ExternalLink className="button-icon" aria-hidden="true" />
          {state.kind === "starting" ? "Opening GitHub" : "Request GitHub review"}
        </button>
      )}
      {state.kind === "settled" ? (
        <p role="alert">
          {uncertain
            ? "The review request outcome is unconfirmed. The same request is retained."
            : (REVIEW_FAILURES[state.result.kind] ?? "The review request was rejected.")}
        </p>
      ) : null}
    </section>
  );
}

// Activation through ci-config-epoch-activation/v3 for one returned review. With read access and
// a reviewed baseline, activation waits until a terminal preview result is displayed; the
// preview reads only while the view is visible.
export function ReviewedEpochActivation({
  scope,
  hint,
  session,
  visible,
  locked,
  confirmed,
  onActivate,
}: {
  readonly scope: ConfigScope;
  readonly hint: RegisteredEpochReviewHint;
  readonly session: ControlPlaneSession;
  readonly visible: boolean;
  readonly locked: boolean;
  readonly confirmed: boolean;
  readonly onActivate: (command: ActivationCommand) => void;
}) {
  const base = hint.expectedActive;
  const comparable = session.roles.includes("read") && base !== null;
  const preview = useEpochPreview(
    scope,
    comparable ? base.epochId : undefined,
    hint.targetEpochId,
    visible,
  );
  const previewPending = comparable && preview === undefined;
  return (
    <section className="configuration-detail" aria-label="Reviewed registered epoch">
      <h3>Reviewed registered epoch</h3>
      <dl>
        <dt>Target epoch</dt>
        <dd>
          <code>{hint.targetEpochId}</code>
        </dd>
        <dt>Source hash</dt>
        <dd>
          <code>{hint.expectedSourceHash}</code>
        </dd>
        <dt>Reviewed active revision</dt>
        <dd>{base ? base.revision : "No active configuration"}</dd>
        <dt>Review operation</dt>
        <dd>
          <code>{hint.operationId}</code>
        </dd>
      </dl>
      {!session.roles.includes("read") ? (
        <p>Read role required to compare epochs.</p>
      ) : base ? (
        <EpochPreview label="Reviewed change against the active epoch" result={preview} />
      ) : (
        <p>No active configuration was reviewed; the whole policy becomes active.</p>
      )}
      {confirmed ? null : session.roles.includes("activate") ? (
        <button
          type="button"
          className="button"
          disabled={locked || !visible || previewPending}
          onClick={() =>
            onActivate({
              kind: "activation",
              currentEpochId: base?.epochId ?? null,
              body: {
                scope: { installationId: scope.installationId, repositoryId: scope.repositoryId },
                targetEpochId: hint.targetEpochId,
                expectedSourceHash: hint.expectedSourceHash,
                registeredEpochReviewOperationId: hint.operationId,
                expectedRevision: base?.revision ?? null,
                operationId: crypto.randomUUID(),
              },
            })
          }
        >
          <Zap className="button-icon" aria-hidden="true" />
          Activate reviewed epoch
        </button>
      ) : (
        <p>Activate role required for activation.</p>
      )}
    </section>
  );
}
