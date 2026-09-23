import { useEffect, useState } from "react";
import type { EconomicsResult } from "../../api/ciEconomics/transport";

export type EconomicsReadState<T> =
  | { readonly kind: "loading" }
  | { readonly kind: "settled"; readonly result: EconomicsResult<T> };

export function useEconomicsRead<T>(
  read: (signal: AbortSignal) => Promise<EconomicsResult<T>>,
  refreshEpoch = 0,
) {
  const [revision, setRevision] = useState(0);
  const [settled, setSettled] = useState<{
    readonly read: typeof read;
    readonly revision: number;
    readonly refreshEpoch: number;
    readonly result: EconomicsResult<T>;
  }>();
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void read(controller.signal).then(
      (result) => {
        if (active) setSettled({ read, revision, refreshEpoch, result });
      },
      () => {
        if (active)
          setSettled({ read, revision, refreshEpoch, result: { kind: "network-failure" } });
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [read, revision, refreshEpoch]);
  const state: EconomicsReadState<T> =
    settled?.read === read && settled.revision === revision && settled.refreshEpoch === refreshEpoch
      ? { kind: "settled", result: settled.result }
      : { kind: "loading" };
  return { state, refresh: () => setRevision((value) => value + 1) };
}
