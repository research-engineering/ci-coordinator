import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { EconomicsFailure, EconomicsResult } from "../../api/ciEconomics/transport";

type StatusRead<T> = {
  readonly key: string;
  readonly value?: T;
  readonly failure?: EconomicsFailure;
  readonly refreshing: boolean;
};

export function useEconomicsStatus<T>(
  key: string,
  readStatus: (signal: AbortSignal) => Promise<EconomicsResult<T>>,
  shouldPoll: (value: T) => boolean,
  viewActive = true,
) {
  const [state, setState] = useState<StatusRead<T>>({ key, refreshing: true });
  const refreshAction = useRef<() => void>(() => {});
  const refresh = useCallback(() => refreshAction.current(), []);
  useLayoutEffect(() => {
    if (!viewActive) return;
    let active = true;
    let inFlight: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let queued = false;
    let automatic = false;
    function stopTimer() {
      clearTimeout(timer);
      timer = undefined;
    }
    async function read() {
      stopTimer();
      if (!active) return;
      if (document.visibilityState !== "visible") {
        queued = true;
        return;
      }
      if (inFlight) {
        queued = true;
        return;
      }
      queued = false;
      const controller = new AbortController();
      inFlight = controller;
      setState((previous) => ({
        ...(previous.key === key ? previous : { key }),
        refreshing: true,
      }));
      try {
        const result = await readStatus(controller.signal);
        if (!active) return;
        automatic = result.kind === "ready" && shouldPoll(result.value);
        setState((previous) =>
          result.kind === "ready"
            ? { key, value: result.value, refreshing: false }
            : {
                key,
                ...(previous.key === key && previous.value ? { value: previous.value } : {}),
                failure: result,
                refreshing: false,
              },
        );
      } catch {
        if (!active) return;
        automatic = false;
        setState((previous) => ({
          key,
          ...(previous.key === key && previous.value ? { value: previous.value } : {}),
          failure: { kind: "network-failure" },
          refreshing: false,
        }));
      } finally {
        inFlight = undefined;
        if (active && queued && document.visibilityState === "visible") {
          void read();
        } else if (active && automatic && document.visibilityState === "visible") {
          timer = setTimeout(() => void read(), 15_000);
        }
      }
    }
    function visibilityChanged() {
      stopTimer();
      if ((automatic || queued) && document.visibilityState === "visible" && !inFlight) void read();
    }
    refreshAction.current = () => void read();
    document.addEventListener("visibilitychange", visibilityChanged);
    void read();
    return () => {
      active = false;
      stopTimer();
      inFlight?.abort();
      refreshAction.current = () => {};
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [key, readStatus, shouldPoll, viewActive]);
  return { state: state.key === key ? state : { key, refreshing: true }, refresh };
}
