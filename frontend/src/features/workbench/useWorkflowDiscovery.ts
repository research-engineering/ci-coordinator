import { useCallback, useEffect, useMemo, useState } from "react";
import type { WorkbenchScope } from "../../api/workbench/client";
import {
  fetchWorkflowDiscovery,
  type WorkflowDiscoveryResult,
} from "../../api/workflowDiscovery/client";

type QueryState =
  | { readonly kind: "idle" }
  | { readonly kind: "loading"; readonly requestKey: string }
  | {
      readonly kind: "settled";
      readonly requestKey: string;
      readonly result: WorkflowDiscoveryResult;
    };

export function useWorkflowDiscovery(
  scope: WorkbenchScope | undefined,
  revision: string | undefined,
) {
  const [refreshRevision, setRefreshRevision] = useState(0);
  const installationId = scope?.installationId;
  const repositoryId = scope?.repositoryId;
  const request = useMemo(() => {
    const repository =
      installationId === undefined || repositoryId === undefined
        ? undefined
        : { installationId, repositoryId };
    return { key: requestKey(repository, revision, refreshRevision), revision, scope: repository };
  }, [refreshRevision, revision, installationId, repositoryId]);
  const [state, setState] = useState<QueryState>({ kind: "idle" });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    if (!request.scope) {
      setState({ kind: "idle" });
      return () => controller.abort();
    }
    setState({ kind: "loading", requestKey: request.key });
    void fetchWorkflowDiscovery(request.scope, request.revision, controller.signal).then(
      (result) => {
        if (active) setState({ kind: "settled", requestKey: request.key, result });
      },
      () => {
        if (active) {
          setState({
            kind: "settled",
            requestKey: request.key,
            result: { kind: "network-failure" },
          });
        }
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [request]);

  const refresh = useCallback(() => setRefreshRevision((value) => value + 1), []);
  const currentState: QueryState =
    state.kind === "idle" || state.requestKey === request.key
      ? state
      : { kind: "loading", requestKey: request.key };
  return { refresh, state: currentState } as const;
}

function requestKey(
  scope: Pick<WorkbenchScope, "installationId" | "repositoryId"> | undefined,
  revision: string | undefined,
  refreshRevision: number,
): string {
  if (!scope) return "idle";
  return `${scope.installationId}:${scope.repositoryId}:${revision ?? "head"}:${refreshRevision}`;
}
