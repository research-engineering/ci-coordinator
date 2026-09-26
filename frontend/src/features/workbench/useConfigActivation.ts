import { useLayoutEffect, useRef, useState } from "react";
import {
  activateConfig,
  type ConfigActivationCommand,
  type ConfigActivationResult,
} from "../../api/configActivation/client";
import type { ConfigActivation } from "../../api/configActivation/schema";
import type { WorkbenchScope } from "../../api/workbench/client";

type ActivationState =
  | { readonly kind: "idle" }
  | {
      readonly kind: "submitting";
      readonly owner: string;
      readonly command: ConfigActivationCommand;
    }
  | {
      readonly kind: "settled";
      readonly owner: string;
      readonly command: ConfigActivationCommand;
      readonly result: ConfigActivationResult;
      readonly uncertain: boolean;
      readonly expired: boolean;
    };

interface ConfigActivationBinding {
  readonly scope: Pick<WorkbenchScope, "installationId" | "repositoryId">;
  readonly expectedRevision: number | null | undefined;
  readonly manifestId: string;
  readonly targetEpochId: string;
  readonly csrfToken: string;
  readonly authorityRevision: number;
  readonly expiresAt: string;
  readonly allowed: boolean;
  readonly newCommandAllowed: boolean;
  readonly onConfirmed?: ((receipt: ConfigActivation) => void) | undefined;
  readonly onConflict?: (() => void) | undefined;
}

export function useConfigActivation(binding: ConfigActivationBinding) {
  const owner = [
    binding.scope.installationId,
    binding.scope.repositoryId,
    binding.authorityRevision,
  ].join(":");
  const live = useRef({ owner, binding });
  live.current = { owner, binding };
  const [state, setState] = useState<ActivationState>({ kind: "idle" });
  const current = useRef<ActivationState>(state);
  const generation = useRef(0);
  const controller = useRef<AbortController | undefined>(undefined);

  function publish(value: ActivationState) {
    current.current = value;
    setState(value);
  }

  useLayoutEffect(() => {
    void owner;
    generation.current += 1;
    controller.current?.abort();
    controller.current = undefined;
    current.current = { kind: "idle" };
    setState({ kind: "idle" });
    return () => {
      generation.current += 1;
      controller.current?.abort();
      controller.current = undefined;
    };
  }, [owner]);

  function authorized() {
    const latest = live.current.binding;
    return (
      live.current.owner === owner &&
      latest.allowed &&
      Number.isFinite(Date.parse(latest.expiresAt)) &&
      Date.parse(latest.expiresAt) > Date.now()
    );
  }

  async function send(command: ConfigActivationCommand, wasUncertain: boolean) {
    if (controller.current || !authorized()) return;
    const active = new AbortController();
    const ticket = ++generation.current;
    controller.current = active;
    publish({ kind: "submitting", owner, command });
    let result: ConfigActivationResult;
    try {
      result = await activateConfig(command, live.current.binding.csrfToken, active.signal);
    } catch {
      result = { kind: "network-failure" };
    } finally {
      if (controller.current === active) controller.current = undefined;
    }
    if (active.signal.aborted || generation.current !== ticket || live.current.owner !== owner)
      return;
    const latest = live.current.binding;
    const expired =
      !Number.isFinite(Date.parse(latest.expiresAt)) || Date.parse(latest.expiresAt) <= Date.now();
    const complete = result.kind === "complete" && !expired && latest.allowed;
    const uncertain =
      !complete &&
      (wasUncertain ||
        expired ||
        !latest.allowed ||
        ["network-failure", "invalid-response", "unavailable", "overloaded"].includes(result.kind));
    publish({ kind: "settled", owner, command, result, uncertain, expired });
    if (complete && result.kind === "complete") latest.onConfirmed?.(result.activation);
    else if (!uncertain && ["revision_conflict", "attestation_invalid"].includes(result.kind))
      latest.onConflict?.();
  }

  function submit() {
    const latest = live.current.binding;
    const prior = current.current;
    if (
      !authorized() ||
      !latest.newCommandAllowed ||
      latest.expectedRevision === undefined ||
      controller.current ||
      (prior.kind !== "idle" &&
        prior.owner === owner &&
        (prior.kind === "submitting" || prior.uncertain))
    )
      return;
    const command: ConfigActivationCommand = {
      expectedRevision: latest.expectedRevision,
      operationId: crypto.randomUUID(),
      proposalManifestId: latest.manifestId,
      scope: {
        installationId: latest.scope.installationId,
        repositoryId: latest.scope.repositoryId,
      },
      targetEpochId: latest.targetEpochId,
    };
    void send(command, false);
  }

  function retry() {
    const prior = current.current;
    if (prior.kind === "settled" && prior.owner === owner && prior.uncertain)
      void send(prior.command, true);
  }

  const currentState =
    state.kind === "idle" || state.owner === owner ? state : { kind: "idle" as const };
  const uncertain = currentState.kind === "settled" && currentState.uncertain;
  return {
    retry,
    submit,
    state: currentState,
    uncertain,
    expired: currentState.kind === "settled" && currentState.expired,
    locked: currentState.kind === "submitting" || uncertain,
  } as const;
}
