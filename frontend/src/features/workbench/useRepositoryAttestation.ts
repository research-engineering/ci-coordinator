import { useCallback, useEffect, useRef, useState } from "react";
import {
  type ExpectedActiveEpoch,
  type RepositoryAttestationResult,
  startRepositoryAttestation,
} from "../../api/repositoryAttestation/client";
import type { WorkbenchScope } from "../../api/workbench/client";

type AttestationState =
  | { readonly kind: "idle" }
  | { readonly kind: "starting"; readonly binding: string; readonly operationId: string }
  | { readonly kind: "redirecting"; readonly binding: string; readonly operationId: string }
  | {
      readonly kind: "settled";
      readonly binding: string;
      readonly operationId: string;
      readonly result: Exclude<RepositoryAttestationResult, { readonly kind: "ready" }>;
    };

interface RepositoryAttestationBinding {
  readonly scope: Pick<WorkbenchScope, "installationId" | "repositoryId">;
  readonly manifestId: string;
  readonly expectedActive: ExpectedActiveEpoch | null;
  readonly csrfToken: string;
}

export function useRepositoryAttestation(binding: RepositoryAttestationBinding) {
  const installationId = binding.scope.installationId;
  const repositoryId = binding.scope.repositoryId;
  const activeEpochId = binding.expectedActive?.epochId;
  const activeRevision = binding.expectedActive?.revision;
  const bindingKey = [
    installationId,
    repositoryId,
    binding.manifestId,
    activeEpochId ?? "none",
    activeRevision ?? "none",
  ].join(":");
  const [state, setState] = useState<AttestationState>({ kind: "idle" });
  const requestGeneration = useRef(0);
  const controller = useRef<AbortController | undefined>(undefined);

  useEffect(() => {
    void bindingKey;
    requestGeneration.current += 1;
    controller.current?.abort();
    setState({ kind: "idle" });
    return () => {
      requestGeneration.current += 1;
      controller.current?.abort();
    };
  }, [bindingKey]);

  const start = useCallback(
    (operationId: string = crypto.randomUUID()) => {
      const generation = ++requestGeneration.current;
      controller.current?.abort();
      const nextController = new AbortController();
      controller.current = nextController;
      setState({ kind: "starting", binding: bindingKey, operationId });
      void startRepositoryAttestation(
        {
          expectedActive:
            activeEpochId === undefined || activeRevision === undefined
              ? null
              : { epochId: activeEpochId, revision: activeRevision },
          expectedManifestId: binding.manifestId,
          operationId,
          scope: { installationId, repositoryId },
        },
        binding.csrfToken,
        nextController.signal,
      ).then(
        (result) => {
          if (requestGeneration.current !== generation) return;
          if (result.kind === "ready") {
            setState({ kind: "redirecting", binding: bindingKey, operationId });
            globalThis.location.assign(result.authorizationUrl);
          } else {
            setState({ kind: "settled", binding: bindingKey, operationId, result });
          }
        },
        () => {
          if (requestGeneration.current === generation) {
            setState({
              kind: "settled",
              binding: bindingKey,
              operationId,
              result: { kind: "network-failure" },
            });
          }
        },
      );
    },
    [
      activeEpochId,
      activeRevision,
      binding.csrfToken,
      binding.manifestId,
      bindingKey,
      installationId,
      repositoryId,
    ],
  );

  const currentState =
    state.kind === "idle" || state.binding === bindingKey ? state : { kind: "idle" as const };
  const retry = useCallback(() => {
    if (currentState.kind === "settled") start(currentState.operationId);
  }, [currentState, start]);
  return { retry, start, state: currentState } as const;
}
