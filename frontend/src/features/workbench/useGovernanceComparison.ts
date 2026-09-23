import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchGovernanceComparison,
  type GovernanceComparisonResult,
} from "../../api/governanceComparison/client";
import type { WorkbenchScope } from "../../api/workbench/client";

type QueryState =
  | { readonly kind: "loading"; readonly requestKey: string; readonly scopeKey: string }
  | {
      readonly kind: "settled";
      readonly requestKey: string;
      readonly refreshing: boolean;
      readonly result: GovernanceComparisonResult;
      readonly scopeKey: string;
    };

export function useGovernanceComparison(scope: WorkbenchScope) {
  const [refreshRevision, setRefreshRevision] = useState(0);
  const scopeKey = `${scope.installationId}:${scope.repositoryId}`;
  const request = useMemo(
    () => ({
      key: `${scopeKey}:${refreshRevision}`,
      scope: {
        installationId: scope.installationId,
        repositoryId: scope.repositoryId,
      },
    }),
    [refreshRevision, scope.installationId, scope.repositoryId, scopeKey],
  );
  const [state, setState] = useState<QueryState>({
    kind: "loading",
    requestKey: request.key,
    scopeKey,
  });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState((current) =>
      current.kind === "settled" && current.scopeKey === scopeKey
        ? { ...current, refreshing: true, requestKey: request.key }
        : { kind: "loading", requestKey: request.key, scopeKey },
    );
    void fetchGovernanceComparison(request.scope, controller.signal).then(
      (result) => {
        if (active) {
          setState({
            kind: "settled",
            refreshing: false,
            requestKey: request.key,
            result,
            scopeKey,
          });
        }
      },
      () => {
        if (active) {
          setState({
            kind: "settled",
            refreshing: false,
            requestKey: request.key,
            result: { kind: "network-failure" },
            scopeKey,
          });
        }
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [request, scopeKey]);

  const refresh = useCallback(() => {
    setState((current) =>
      current.kind === "settled" ? { ...current, refreshing: true } : current,
    );
    setRefreshRevision((value) => value + 1);
  }, []);
  const current =
    state.scopeKey === scopeKey
      ? state
      : { kind: "loading" as const, requestKey: request.key, scopeKey };
  return { generation: request.key, refresh, state: current } as const;
}
