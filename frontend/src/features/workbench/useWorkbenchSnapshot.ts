import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchWorkbenchSnapshot,
  type WorkbenchResult,
  type WorkbenchScope,
} from "../../api/workbench/client";

type QueryState =
  | { readonly kind: "idle" }
  | { readonly kind: "loading"; readonly requestKey: string }
  | { readonly kind: "settled"; readonly requestKey: string; readonly result: WorkbenchResult };

export function useWorkbenchSnapshot(scope: WorkbenchScope | undefined, authorityRevision: number) {
  const [refreshRevision, setRefreshRevision] = useState(0);
  const request = useMemo(
    () => ({ key: scope ? requestKey(scope, refreshRevision, authorityRevision) : "idle", scope }),
    [authorityRevision, refreshRevision, scope],
  );
  const [state, setState] = useState<QueryState>({ kind: "idle" });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    if (!request.scope) {
      setState({ kind: "idle" });
      return () => controller.abort();
    }
    setState({ kind: "loading", requestKey: request.key });
    void fetchWorkbenchSnapshot(request.scope, controller.signal).then(
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

  const refresh = useCallback(() => setRefreshRevision((revision) => revision + 1), []);
  const currentState: QueryState =
    state.kind === "idle" || state.requestKey === request.key
      ? state
      : { kind: "loading", requestKey: request.key };
  return { refresh, state: currentState } as const;
}

function requestKey(
  scope: WorkbenchScope,
  refreshRevision: number,
  authorityRevision: number,
): string {
  return `${scope.installationId}:${scope.repositoryId}:${scope.limit}:${refreshRevision}:${authorityRevision}`;
}
