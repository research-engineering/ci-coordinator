import { useEffect, useRef, useState } from "react";
import {
  activateRegisteredEpoch,
  type RegisteredEpochActivationCommand,
} from "../../api/configActivation/client";
import {
  type ConfigurationCommand,
  registerSource,
  rollbackConfig,
} from "../../api/configLifecycle/client";
import type { ConfigRegistration, ConfigRollback } from "../../api/configLifecycle/schema";
import type { LifecycleFailure, LifecycleResult } from "../../api/configLifecycle/transport";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { ExpectedActiveEpoch } from "../../api/repositoryAttestation/client";

export interface ActivationCommand {
  readonly kind: "activation";
  readonly currentEpochId: string | null;
  readonly body: RegisteredEpochActivationCommand;
}
export type WorkspaceCommand = ConfigurationCommand | ActivationCommand;

// Each command needs exactly the role its API route admits; the UI adds no stricter policy.
export function commandRole(command: WorkspaceCommand) {
  return command.kind === "registration" ? "configure" : "activate";
}

async function activate(
  command: ActivationCommand,
  csrfToken: string,
  signal: AbortSignal,
): Promise<LifecycleResult<ConfigRollback>> {
  const result = await activateRegisteredEpoch(command.body, csrfToken, signal);
  return result.kind === "complete" ? { kind: "ready", value: result.activation } : result;
}

type State =
  | { readonly kind: "idle" }
  | { readonly kind: "pending" | "uncertain"; readonly command: WorkspaceCommand }
  | {
      readonly kind: "complete";
      readonly command: WorkspaceCommand;
      readonly value: ConfigRegistration | ConfigRollback;
    }
  | { readonly kind: "rejected"; readonly failure: LifecycleFailure };

export function useConfigurationCommand(
  session: ControlPlaneSession,
  onConfirmed?: (active?: ExpectedActiveEpoch) => void,
) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [readRevision, setReadRevision] = useState(0);
  const current = useRef<State>(state);
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => controller.current?.abort(), []);
  function publish(value: State) {
    current.current = value;
    setState(value);
    if (value.kind === "complete" || value.kind === "rejected")
      setReadRevision((revision) => revision + 1);
  }
  async function submit(command: WorkspaceCommand) {
    const prior = current.current;
    if (
      controller.current ||
      prior.kind === "pending" ||
      Date.parse(session.expiresAt) <= Date.now() ||
      !session.roles.includes(commandRole(command)) ||
      (prior.kind === "uncertain" && prior.command !== command)
    )
      return;
    const active = new AbortController();
    controller.current = active;
    publish({ kind: "pending", command });
    try {
      const result =
        command.kind === "registration"
          ? await registerSource(command, session.csrfToken, active.signal)
          : command.kind === "rollback"
            ? await rollbackConfig(command, session.csrfToken, active.signal)
            : await activate(command, session.csrfToken, active.signal);
      if (active.signal.aborted) return;
      if (Date.parse(session.expiresAt) <= Date.now()) {
        publish({ kind: "uncertain", command });
        return;
      }
      if (result.kind === "ready") {
        publish({ kind: "complete", command, value: result.value });
        onConfirmed?.(
          command.kind !== "registration" && "revision" in result.value
            ? { epochId: result.value.epochId, revision: result.value.revision }
            : undefined,
        );
      } else if (
        prior.kind !== "uncertain" &&
        [
          "invalid-request",
          "unauthenticated",
          "forbidden",
          "invalid_config",
          "conflict",
          "revision_conflict",
          "target_unavailable",
          "coverage_reducing",
          "coverage_unproven",
          "target_never_activated",
          "attestation_invalid",
        ].includes(result.kind)
      )
        publish({ kind: "rejected", failure: result });
      else publish({ kind: "uncertain", command });
    } catch {
      if (!active.signal.aborted) publish({ kind: "uncertain", command });
    } finally {
      if (controller.current === active) controller.current = undefined;
    }
  }
  return {
    state,
    submit,
    readRevision,
    locked: state.kind === "pending" || state.kind === "uncertain",
  };
}
