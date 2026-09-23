import { useCallback, useEffect, useRef, useState } from "react";
import { activateConfig, type ConfigActivationResult } from "../../api/configActivation/client";
import type { WorkbenchScope } from "../../api/workbench/client";

type ActivationState =
  | { readonly kind: "idle" }
  | { readonly kind: "submitting"; readonly binding: string; readonly operationId: string }
  | {
      readonly kind: "settled";
      readonly binding: string;
      readonly operationId: string;
      readonly result: ConfigActivationResult;
    };

interface ConfigActivationBinding {
  readonly scope: Pick<WorkbenchScope, "installationId" | "repositoryId">;
  readonly expectedRevision: number | null;
  readonly manifestId: string;
  readonly targetEpochId: string;
  readonly csrfToken: string;
}

export function useConfigActivation(binding: ConfigActivationBinding) {
  const installationId = binding.scope.installationId;
  const repositoryId = binding.scope.repositoryId;
  const bindingKey = [
    installationId,
    repositoryId,
    binding.manifestId,
    binding.targetEpochId,
    binding.expectedRevision ?? "none",
  ].join(":");
  const [state, setState] = useState<ActivationState>({ kind: "idle" });
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

  const submit = useCallback(
    (operationId: string = crypto.randomUUID()) => {
      const generation = ++requestGeneration.current;
      controller.current?.abort();
      const nextController = new AbortController();
      controller.current = nextController;
      setState({ kind: "submitting", binding: bindingKey, operationId });
      void activateConfig(
        {
          expectedRevision: binding.expectedRevision,
          operationId,
          proposalManifestId: binding.manifestId,
          scope: { installationId, repositoryId },
          targetEpochId: binding.targetEpochId,
        },
        binding.csrfToken,
        nextController.signal,
      ).then(
        (result) => {
          if (requestGeneration.current === generation) {
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
    [binding, bindingKey, installationId, repositoryId],
  );

  const currentState =
    state.kind === "idle" || state.binding === bindingKey ? state : { kind: "idle" as const };
  const retry = useCallback(() => {
    if (currentState.kind === "settled") submit(currentState.operationId);
  }, [currentState, submit]);
  return { retry, state: currentState, submit } as const;
}
