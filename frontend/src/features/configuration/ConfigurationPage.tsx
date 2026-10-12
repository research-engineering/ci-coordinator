import { ArrowRight, RefreshCw } from "lucide-react";
import { type KeyboardEvent, type MouseEvent, useEffect, useMemo, useState } from "react";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { ExpectedActiveEpoch } from "../../api/repositoryAttestation/client";
import type { WorkbenchScope } from "../../api/workbench/client";
import {
  consoleHref,
  type RegisteredEpochReviewHint,
  registeredEpochReviewHint,
} from "../workbench/navigation";
import { ConfigurationNotice } from "./ConfigurationNotice";
import { ReviewedEpochActivation } from "./RegisteredEpochReview";
import { RetainedEpochs } from "./RetainedEpochs";
import { SourceEditor } from "./SourceEditor";
import { useConfigurationCommand } from "./useConfigurationCommand";

const CONFIGURATION_ROLES = ["read", "configure", "activate"] as const;

export function ConfigurationPage({
  scope,
  session,
  authorityRevision,
  active,
  onWorkflows,
  onConfirmed,
  sharedReadRevision = 0,
  confirmedMinimum,
  minimumConflict = false,
}: {
  readonly scope: WorkbenchScope;
  readonly session: ControlPlaneSession | undefined;
  readonly authorityRevision: number;
  readonly active: boolean;
  readonly onWorkflows: ((event: MouseEvent<HTMLAnchorElement>) => void) | undefined;
  readonly onConfirmed?: ((active?: ExpectedActiveEpoch) => void) | undefined;
  readonly sharedReadRevision?: number;
  readonly confirmedMinimum?: ExpectedActiveEpoch | undefined;
  readonly minimumConflict?: boolean;
}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!session) return;
    const remaining = Date.parse(session.expiresAt) - Math.max(now, Date.now());
    if (!Number.isFinite(remaining) || remaining <= 0) return;
    const timer = setTimeout(
      () => setNow(Date.now()),
      Math.max(0, Math.min(remaining, 2_147_483_647)),
    );
    return () => clearTimeout(timer);
  }, [session, now]);
  // Provisional D3: each control below mirrors the role its route admits.
  if (
    !session ||
    !CONFIGURATION_ROLES.some((role) => session.roles.includes(role)) ||
    !Number.isFinite(Date.parse(session.expiresAt)) ||
    Date.parse(session.expiresAt) <= Math.max(now, Date.now())
  )
    return (
      <p role="status">
        A current administrator session with read, configure or activate access is required.
      </p>
    );
  const key = JSON.stringify([
    scope.installationId,
    scope.repositoryId,
    authorityRevision,
    session.user.actorId,
    session.csrfToken,
    session.roles,
    session.expiresAt,
  ]);
  return (
    <AuthorizedConfiguration
      key={key}
      scope={scope}
      session={session}
      active={active}
      onWorkflows={onWorkflows}
      onConfirmed={onConfirmed}
      sharedReadRevision={sharedReadRevision}
      confirmedMinimum={confirmedMinimum}
      minimumConflict={minimumConflict}
    />
  );
}

function AuthorizedConfiguration({
  scope,
  session,
  active,
  onWorkflows,
  onConfirmed,
  sharedReadRevision,
  confirmedMinimum,
  minimumConflict,
}: {
  readonly scope: WorkbenchScope;
  readonly session: ControlPlaneSession;
  readonly active: boolean;
  readonly onWorkflows: ((event: MouseEvent<HTMLAnchorElement>) => void) | undefined;
  readonly onConfirmed: ((active?: ExpectedActiveEpoch) => void) | undefined;
  readonly sharedReadRevision: number;
  readonly confirmedMinimum: ExpectedActiveEpoch | undefined;
  readonly minimumConflict: boolean;
}) {
  const repository = useMemo(
    () => ({ installationId: scope.installationId, repositoryId: scope.repositoryId }),
    [scope.installationId, scope.repositoryId],
  );
  const [tab, setTab] = useState<"source" | "epochs">("source");
  const [visible, setVisible] = useState(document.visibilityState === "visible");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  const command = useConfigurationCommand(session, onConfirmed);
  const [reviewed, setReviewed] = useState<RegisteredEpochReviewHint | undefined>(() =>
    registeredEpochReviewHint(new URLSearchParams(globalThis.location.search), repository),
  );
  function tabKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next =
      event.key === "Home"
        ? "source"
        : event.key === "End"
          ? "epochs"
          : tab === "source"
            ? "epochs"
            : "source";
    setTab(next);
    document.getElementById(`configuration-tab-${next}`)?.focus();
  }
  const state = command.state;
  const localMinimum = useMemo(
    () =>
      state.kind === "complete" &&
      state.command.kind !== "registration" &&
      "revision" in state.value
        ? { epochId: state.value.epochId, revision: state.value.revision }
        : undefined,
    [state],
  );
  const reviewedActivated =
    state.kind === "complete" &&
    state.command.kind === "activation" &&
    state.command.body.registeredEpochReviewOperationId === reviewed?.operationId;
  const minimumActive =
    confirmedMinimum && (!localMinimum || confirmedMinimum.revision > localMinimum.revision)
      ? confirmedMinimum
      : localMinimum;
  const conflictingMinimum =
    minimumConflict ||
    Boolean(
      localMinimum &&
        confirmedMinimum &&
        localMinimum.revision === confirmedMinimum.revision &&
        localMinimum.epochId !== confirmedMinimum.epochId,
    );
  return (
    <div className="configuration-workspace">
      {reviewed ? (
        <ReviewedEpochActivation
          scope={repository}
          hint={reviewed}
          session={session}
          visible={active && visible}
          locked={command.locked}
          confirmed={reviewedActivated}
          onActivate={(value) => void command.submit(value)}
        />
      ) : null}
      <div role="tablist" aria-label="Configuration views" className="configuration-tabs">
        {(["source", "epochs"] as const).map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`configuration-tab-${id}`}
            aria-controls={`configuration-panel-${id}`}
            aria-selected={tab === id}
            tabIndex={tab === id ? 0 : -1}
            onKeyDown={tabKey}
            onClick={() => setTab(id)}
          >
            {id === "source" ? "Source" : "Retained epochs"}
          </button>
        ))}
      </div>
      {state.kind === "pending" ? (
        <p role="status">
          {state.command.kind === "registration"
            ? "Registering source"
            : state.command.kind === "rollback"
              ? "Requesting rollback"
              : "Requesting activation"}
        </p>
      ) : null}
      {state.kind === "uncertain" ? (
        <section role="alert" className="configuration-notice">
          <p>
            {state.command.kind === "registration"
              ? "Registration"
              : state.command.kind === "rollback"
                ? "Rollback"
                : "Activation"}{" "}
            outcome unknown. The exact command is retained.
          </p>
          <details>
            <summary>Pending command</summary>
            <dl>
              <dt>Operation</dt>
              <dd>
                <code>
                  {state.command.kind === "registration"
                    ? state.command.operationId
                    : state.command.body.operationId}
                </code>
              </dd>
              <dt>Target epoch</dt>
              <dd>
                <code>
                  {state.command.kind === "registration"
                    ? state.command.draft.validation.epochId
                    : state.command.body.targetEpochId}
                </code>
              </dd>
              {state.command.kind === "rollback" ? (
                <>
                  <dt>Expected revision</dt>
                  <dd>{state.command.body.expectedRevision}</dd>
                  <dt>Reason</dt>
                  <dd>{state.command.body.reason}</dd>
                </>
              ) : null}
              {state.command.kind === "activation" ? (
                <>
                  <dt>Expected revision</dt>
                  <dd>{state.command.body.expectedRevision ?? "None"}</dd>
                  <dt>Review operation</dt>
                  <dd>
                    <code>{state.command.body.registeredEpochReviewOperationId}</code>
                  </dd>
                </>
              ) : null}
            </dl>
          </details>
          <button
            type="button"
            className="button button--secondary"
            disabled={!active || !visible}
            onClick={() => void command.submit(state.command)}
          >
            <RefreshCw className="button-icon" aria-hidden="true" />
            Retry same command
          </button>
        </section>
      ) : null}
      {state.kind === "rejected" ? <ConfigurationNotice failure={state.failure} /> : null}
      {state.kind === "complete" ? (
        <p role="status">
          {state.command.kind === "registration"
            ? "Source registration confirmed. Activation requires a GitHub review of the retained epoch or a reviewed workflow proposal."
            : `${state.command.kind === "rollback" ? "Rollback" : "Activation"} confirmed at revision ${"revision" in state.value ? state.value.revision : ""}.`}
        </p>
      ) : null}
      <div
        role="tabpanel"
        id="configuration-panel-source"
        aria-labelledby="configuration-tab-source"
        hidden={tab !== "source"}
      >
        {session.roles.includes("configure") ? (
          <SourceEditor
            scope={repository}
            session={session}
            active={active && visible && tab === "source"}
            locked={command.locked}
            onRegister={(value) => void command.submit(value)}
          />
        ) : (
          <p>Configure role required to validate or register source.</p>
        )}
      </div>
      <div
        role="tabpanel"
        id="configuration-panel-epochs"
        aria-labelledby="configuration-tab-epochs"
        hidden={tab !== "epochs"}
      >
        {session.roles.includes("read") ? (
          <RetainedEpochs
            scope={repository}
            minimumActive={minimumActive}
            readRevision={command.readRevision}
            sharedReadRevision={sharedReadRevision}
            minimumConflict={conflictingMinimum}
            active={active && visible && tab === "epochs" && !command.locked}
            session={session}
            onRollback={(value) => void command.submit(value)}
            onReviewed={setReviewed}
          />
        ) : (
          <p>Read role required to list retained epochs.</p>
        )}
      </div>
      <footer className="configuration-handoff">
        <span>
          Activation requires a GitHub-reviewed registered epoch or an admitted workflow proposal.
        </span>
        <a
          href={consoleHref({ scope, tab: "plans", view: "workflows" }, location.search)}
          onClick={onWorkflows}
        >
          Review workflow proposal <ArrowRight className="button-icon" aria-hidden="true" />
        </a>
      </footer>
    </div>
  );
}
