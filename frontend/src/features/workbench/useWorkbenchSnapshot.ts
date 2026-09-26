import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ExpectedActiveEpoch } from "../../api/repositoryAttestation/client";
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
  const owner = `${scope?.installationId}:${scope?.repositoryId}:${authorityRevision}`;
  const currentOwner = useRef(owner);
  currentOwner.current = owner;
  const [invalidation, setInvalidation] = useState<{
    readonly owner: string;
    readonly revision: number;
    readonly minimum: ExpectedActiveEpoch | undefined;
    readonly conflict: boolean;
  }>({ owner, revision: 0, minimum: undefined, conflict: false });
  const refreshRevision = invalidation.owner === owner ? invalidation.revision : 0;
  const minimum = invalidation.owner === owner ? invalidation.minimum : undefined;
  const conflict = invalidation.owner === owner && invalidation.conflict;
  const request = useMemo(
    () => ({
      key: scope ? requestKey(scope, refreshRevision, authorityRevision) : "idle",
      scope,
      minimum,
      conflict,
    }),
    [authorityRevision, refreshRevision, scope, minimum, conflict],
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
        if (active) {
          const epoch =
            result.kind === "ready"
              ? result.snapshot.configEpochs.find((item) => item.active)
              : undefined;
          const belowMinimum =
            request.minimum !== undefined &&
            (epoch?.activeRevision == null ||
              epoch.activeRevision < request.minimum.revision ||
              (epoch.activeRevision === request.minimum.revision &&
                epoch.epochId !== request.minimum.epochId));
          setState({
            kind: "settled",
            requestKey: request.key,
            result:
              result.kind === "ready" && (request.conflict || belowMinimum)
                ? { kind: "invalid-response" }
                : result,
          });
        }
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

  const invalidate = useCallback(
    (confirmed?: ExpectedActiveEpoch) => {
      if (currentOwner.current !== owner) return;
      setInvalidation((previous) => {
        const prior =
          previous.owner === owner
            ? previous
            : {
                owner,
                revision: 0,
                minimum: undefined,
                conflict: false,
              };
        const minimum = prior.minimum;
        return {
          owner,
          revision: prior.revision + 1,
          minimum:
            confirmed && (!minimum || confirmed.revision > minimum.revision)
              ? { ...confirmed }
              : minimum,
          conflict:
            prior.conflict ||
            Boolean(
              confirmed &&
                minimum &&
                confirmed.revision === minimum.revision &&
                confirmed.epochId !== minimum.epochId,
            ),
        };
      });
    },
    [owner],
  );
  const refresh = useCallback(() => invalidate(), [invalidate]);
  const currentState: QueryState =
    state.kind === "idle" || state.requestKey === request.key
      ? state
      : { kind: "loading", requestKey: request.key };
  return {
    refresh,
    invalidate,
    readRevision: refreshRevision,
    minimumActive: minimum,
    minimumConflict: conflict,
    state: currentState,
  } as const;
}

function requestKey(
  scope: WorkbenchScope,
  refreshRevision: number,
  authorityRevision: number,
): string {
  return `${scope.installationId}:${scope.repositoryId}:${scope.limit}:${refreshRevision}:${authorityRevision}`;
}
