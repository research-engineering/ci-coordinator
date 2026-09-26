import { useLayoutEffect, useRef, useState } from "react";
import {
  type ExpectedActiveEpoch,
  type RepositoryAttestationCommand,
  type RepositoryAttestationResult,
  startRepositoryAttestation,
} from "../../api/repositoryAttestation/client";
import type { WorkbenchScope } from "../../api/workbench/client";

type AttestationState =
  | { readonly kind: "idle" }
  | {
      readonly kind: "starting" | "redirecting";
      readonly owner: string;
      readonly command: RepositoryAttestationCommand;
    }
  | {
      readonly kind: "settled";
      readonly owner: string;
      readonly command: RepositoryAttestationCommand;
      readonly result: Exclude<RepositoryAttestationResult, { readonly kind: "ready" }>;
      readonly uncertain: boolean;
      readonly expired: boolean;
    };

interface RepositoryAttestationBinding {
  readonly scope: Pick<WorkbenchScope, "installationId" | "repositoryId">;
  readonly manifestId: string;
  readonly expectedActive: ExpectedActiveEpoch | null | undefined;
  readonly csrfToken: string;
  readonly authorityRevision: number;
  readonly expiresAt: string;
  readonly allowed: boolean;
  readonly onConflict?: (() => void) | undefined;
}

export function useRepositoryAttestation(binding: RepositoryAttestationBinding) {
  const owner = [
    binding.scope.installationId,
    binding.scope.repositoryId,
    binding.authorityRevision,
  ].join(":");
  const live = useRef({ owner, binding });
  live.current = { owner, binding };
  const [state, setState] = useState<AttestationState>({ kind: "idle" });
  const current = useRef<AttestationState>(state);
  const generation = useRef(0);
  const controller = useRef<AbortController | undefined>(undefined);

  function publish(value: AttestationState) {
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

  async function send(command: RepositoryAttestationCommand, wasUncertain: boolean) {
    if (controller.current || !authorized()) return;
    const active = new AbortController();
    const ticket = ++generation.current;
    controller.current = active;
    publish({ kind: "starting", owner, command });
    let result: RepositoryAttestationResult;
    try {
      result = await startRepositoryAttestation(
        command,
        live.current.binding.csrfToken,
        active.signal,
      );
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
    if (result.kind === "ready" && !expired && latest.allowed) {
      publish({ kind: "redirecting", owner, command });
      globalThis.location.assign(result.authorizationUrl);
      return;
    }
    const settled = result.kind === "ready" ? { kind: "unauthenticated" as const } : result;
    const reviewed = settled.kind === "already_reviewed" && !expired && latest.allowed;
    publish({
      kind: "settled",
      owner,
      command,
      result: settled,
      expired,
      uncertain:
        !reviewed &&
        (wasUncertain ||
          expired ||
          !latest.allowed ||
          ["network-failure", "invalid-response", "unavailable", "overloaded"].includes(
            settled.kind,
          )),
    });
    if (!wasUncertain && !expired && latest.allowed && settled.kind === "baseline_conflict")
      latest.onConflict?.();
  }

  function start(operationId?: string) {
    const latest = live.current.binding;
    const prior = current.current;
    if (
      !authorized() ||
      latest.expectedActive === undefined ||
      controller.current ||
      (prior.kind !== "idle" &&
        prior.owner === owner &&
        (prior.kind !== "settled" || prior.uncertain))
    )
      return;
    const command: RepositoryAttestationCommand = {
      expectedActive: latest.expectedActive === null ? null : { ...latest.expectedActive },
      expectedManifestId: latest.manifestId,
      operationId: operationId ?? crypto.randomUUID(),
      scope: {
        installationId: latest.scope.installationId,
        repositoryId: latest.scope.repositoryId,
      },
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
  const reviewed =
    currentState.kind === "settled" &&
    !uncertain &&
    currentState.result.kind === "already_reviewed" &&
    binding.expectedActive !== undefined &&
    currentState.command.expectedManifestId === binding.manifestId &&
    currentState.command.expectedActive?.epochId === binding.expectedActive?.epochId &&
    currentState.command.expectedActive?.revision === binding.expectedActive?.revision;
  return {
    retry,
    start,
    state: currentState,
    uncertain,
    reviewed,
    expired: currentState.kind === "settled" && currentState.expired,
    locked: currentState.kind === "starting" || currentState.kind === "redirecting" || uncertain,
  } as const;
}
