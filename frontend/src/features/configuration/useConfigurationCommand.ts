import { useEffect, useRef, useState } from "react";
import {
  type ConfigurationCommand,
  registerSource,
  rollbackConfig,
} from "../../api/configLifecycle/client";
import type { ConfigRegistration, ConfigRollback } from "../../api/configLifecycle/schema";
import type { LifecycleFailure } from "../../api/configLifecycle/transport";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";

type State =
  | { readonly kind: "idle" }
  | { readonly kind: "pending" | "uncertain"; readonly command: ConfigurationCommand }
  | {
      readonly kind: "complete";
      readonly command: ConfigurationCommand;
      readonly value: ConfigRegistration | ConfigRollback;
    }
  | { readonly kind: "rejected"; readonly failure: LifecycleFailure };

export function useConfigurationCommand(session: ControlPlaneSession) {
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
  async function submit(command: ConfigurationCommand) {
    const prior = current.current;
    if (
      controller.current ||
      prior.kind === "pending" ||
      Date.parse(session.expiresAt) <= Date.now() ||
      !session.roles.includes("configure") ||
      (command.kind === "rollback" && !session.roles.includes("activate")) ||
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
          : await rollbackConfig(command, session.csrfToken, active.signal);
      if (active.signal.aborted || Date.parse(session.expiresAt) <= Date.now()) return;
      if (result.kind === "ready") publish({ kind: "complete", command, value: result.value });
      else if (
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
