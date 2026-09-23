import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  type ControlPlaneLogoutResult,
  type ControlPlaneSessionResult,
  fetchControlPlaneSession,
  isSessionChallenge,
  logoutControlPlaneSession,
} from "../../api/controlPlaneIdentity/client";
import { observeResponses } from "../../api/shared/responseObservation";

type SessionState =
  | { readonly kind: "loading" }
  | { readonly kind: "challenged" }
  | { readonly kind: "settled"; readonly result: ControlPlaneSessionResult };

export function useControlPlaneSession() {
  const [authorityRevision, setAuthorityRevision] = useState(0);
  const [state, setState] = useState<SessionState>({ kind: "loading" });
  const [logoutResult, setLogoutResult] = useState<ControlPlaneLogoutResult>();
  const [loggingOut, setLoggingOut] = useState(false);
  const [recoveryRequired, setRecoveryRequired] = useState(false);
  const generation = useRef(0);
  const logoutIntent = useRef(false);
  const challengedSession = useRef<string | undefined>(undefined);
  const mounted = useRef(false);
  const sessionController = useRef<AbortController | undefined>(undefined);
  const logoutController = useRef<AbortController | undefined>(undefined);

  const check = useCallback((recover: boolean, invalidate = true) => {
    if (logoutIntent.current || sessionController.current) return;
    const controller = new AbortController();
    sessionController.current = controller;
    const current = ++generation.current;
    setRecoveryRequired(false);
    setState({ kind: "loading" });
    if (invalidate) setAuthorityRevision((value) => value + 1);
    void fetchControlPlaneSession(controller.signal).then(
      (received) => {
        if (
          !mounted.current ||
          current !== generation.current ||
          controller.signal.aborted ||
          logoutIntent.current
        )
          return;
        sessionController.current = undefined;
        const result: ControlPlaneSessionResult =
          received.kind === "authenticated" && Date.parse(received.session.expiresAt) <= Date.now()
            ? { kind: "invalid-response" }
            : received;
        setState({ kind: "settled", result });
        setRecoveryRequired(recover && result.kind === "anonymous");
      },
      () => undefined,
    );
  }, []);

  useEffect(() => {
    mounted.current = true;
    check(false, false);
    return () => {
      mounted.current = false;
      generation.current += 1;
      sessionController.current?.abort();
      sessionController.current = undefined;
      logoutController.current?.abort();
    };
  }, [check]);

  // Subscribe before descendant passive effects capture their first requests.
  useLayoutEffect(() => {
    if (state.kind !== "settled" || state.result.kind !== "authenticated") return;
    const expiresAt = Date.parse(state.result.session.expiresAt);
    const sessionIdentity = state.result.session.csrfToken;
    const expire = () => check(true);
    const resume = () => {
      if (document.visibilityState === "visible" && Date.now() >= expiresAt) expire();
    };
    const timer = globalThis.setTimeout(
      expire,
      Math.min(2_147_483_647, Math.max(0, expiresAt - Date.now())),
    );
    const stop = observeResponses((request, status) => {
      if (!isSessionChallenge(request, status) || logoutIntent.current || sessionController.current)
        return;
      if (challengedSession.current === sessionIdentity) {
        generation.current += 1;
        setAuthorityRevision((value) => value + 1);
        setRecoveryRequired(false);
        setState({ kind: "challenged" });
        return;
      }
      challengedSession.current = sessionIdentity;
      check(true);
    });
    document.addEventListener("visibilitychange", resume);
    globalThis.addEventListener("focus", resume);
    return () => {
      globalThis.clearTimeout(timer);
      document.removeEventListener("visibilitychange", resume);
      globalThis.removeEventListener("focus", resume);
      stop();
    };
  }, [check, state]);

  const refresh = useCallback(() => {
    if (logoutController.current) return;
    logoutIntent.current = false;
    challengedSession.current = undefined;
    setLogoutResult(undefined);
    check(false);
  }, [check]);

  const logout = useCallback(() => {
    if (
      state.kind !== "settled" ||
      state.result.kind !== "authenticated" ||
      logoutController.current
    )
      return;
    logoutIntent.current = true;
    generation.current += 1;
    sessionController.current?.abort();
    sessionController.current = undefined;
    setRecoveryRequired(false);
    setState({ kind: "settled", result: { kind: "anonymous" } });
    setAuthorityRevision((value) => value + 1);
    const controller = new AbortController();
    logoutController.current = controller;
    setLoggingOut(true);
    void logoutControlPlaneSession(state.result.session.csrfToken, controller.signal).then(
      (result) => {
        if (
          !mounted.current ||
          logoutController.current !== controller ||
          controller.signal.aborted
        )
          return;
        logoutController.current = undefined;
        setLoggingOut(false);
        setLogoutResult(result);
        if (result.kind !== "complete" && result.kind !== "anonymous") {
          setState({
            kind: "settled",
            result: { kind: result.kind === "forbidden" ? "invalid-response" : result.kind },
          });
        }
        if (result.kind === "complete") globalThis.location.assign(result.redirectUrl);
      },
      () => undefined,
    );
  }, [state]);

  return {
    authorityRevision,
    loggingOut,
    logout,
    logoutResult,
    refresh,
    recoveryRequired,
    state,
  } as const;
}
