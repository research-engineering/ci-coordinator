import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchInstallationCatalog,
  fetchRepositoryCatalogPage,
  type InstallationCatalogResult,
  type RepositoryCatalogResult,
} from "../../api/providerInventory/client";

type InstallationState =
  | { readonly kind: "loading"; readonly requestKey: string }
  | {
      readonly kind: "settled";
      readonly requestKey: string;
      readonly result: InstallationCatalogResult;
    };

type RepositoryState =
  | { readonly kind: "idle" }
  | { readonly kind: "loading"; readonly requestKey: string }
  | {
      readonly kind: "settled";
      readonly requestKey: string;
      readonly result: RepositoryCatalogResult;
    };

export function useInstallationCatalog(page = 1) {
  const [revision, setRevision] = useState(0);
  const requestKey = `${page}:${revision}`;
  const [state, setState] = useState<InstallationState>({ kind: "loading", requestKey });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState({ kind: "loading", requestKey });
    void fetchInstallationCatalog(controller.signal, undefined, page).then(
      (result) => {
        if (active) setState({ kind: "settled", requestKey, result });
      },
      () => {
        if (active) {
          setState({
            kind: "settled",
            requestKey,
            result: { kind: "network-failure" },
          });
        }
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [requestKey, page]);

  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const current =
    state.requestKey === requestKey ? state : { kind: "loading" as const, requestKey };
  return { refresh, state: current } as const;
}

export function useRepositoryCatalog(installationId: number | undefined, page: number) {
  const [revision, setRevision] = useState(0);
  const request = useMemo(
    () => ({
      installationId,
      key: installationId === undefined ? "idle" : `${installationId}:${page}:${revision}`,
      page,
    }),
    [installationId, page, revision],
  );
  const [state, setState] = useState<RepositoryState>({ kind: "idle" });

  useEffect(() => {
    if (request.installationId === undefined) {
      setState({ kind: "idle" });
      return;
    }
    const controller = new AbortController();
    let active = true;
    setState({ kind: "loading", requestKey: request.key });
    void fetchRepositoryCatalogPage(request.installationId, request.page, controller.signal).then(
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

  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const current =
    state.kind === "idle" || state.requestKey === request.key
      ? state
      : { kind: "loading" as const, requestKey: request.key };
  return { refresh, state: current } as const;
}
