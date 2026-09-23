import { CircleUserRound, LoaderCircle, LogIn, LogOut, RefreshCw } from "lucide-react";
import { beginSessionLogin, clearSessionReturn, LOGIN_PATH } from "./sessionNavigation";
import type { useControlPlaneSession } from "./useControlPlaneSession";

interface ControlPlaneIdentityControlProps {
  readonly identity: ReturnType<typeof useControlPlaneSession>;
  readonly returnTo: string;
}

export function ControlPlaneIdentityControl({
  identity,
  returnTo,
}: ControlPlaneIdentityControlProps) {
  if (identity.state.kind === "loading" || identity.loggingOut) {
    return (
      <div className="identity-control" aria-live="polite">
        <LoaderCircle className="spin" aria-hidden="true" />
        <span>{identity.loggingOut ? "Signing out" : "Checking session"}</span>
      </div>
    );
  }
  if (identity.state.kind === "challenged") {
    return (
      <div className="identity-stack" role="status">
        <span className="identity-error">Session access must be checked again</span>
        <button type="button" className="button button--secondary" onClick={identity.refresh}>
          <RefreshCw className="button-icon" aria-hidden="true" />
          Check session
        </button>
      </div>
    );
  }
  const { result } = identity.state;
  if (result.kind === "authenticated") {
    const user = result.session.user;
    const name = user.displayName ?? user.preferredUsername ?? "Administrator";
    const detail = user.preferredUsername ?? `${result.session.roles.length} assigned roles`;
    return (
      <div className="identity-stack">
        <div className="identity-control" title={name}>
          <CircleUserRound aria-hidden="true" />
          <span className="identity-copy">
            <strong>{name}</strong>
            <span>{detail}</span>
          </span>
          <button
            type="button"
            className="icon-button"
            aria-label="Sign out"
            title="Sign out"
            disabled={identity.loggingOut}
            onClick={() => {
              clearSessionReturn();
              identity.logout();
            }}
          >
            {identity.loggingOut ? (
              <LoaderCircle className="spin" aria-hidden="true" />
            ) : (
              <LogOut aria-hidden="true" />
            )}
          </button>
        </div>
      </div>
    );
  }
  if (result.kind === "anonymous") {
    return (
      <a
        className="button button--primary"
        href={LOGIN_PATH}
        onClick={(event) => {
          if (
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey
          )
            return;
          event.preventDefault();
          beginSessionLogin(returnTo, false);
        }}
      >
        <LogIn className="button-icon" aria-hidden="true" />
        Sign in
      </a>
    );
  }
  const logoutFailure = identity.logoutResult;
  const failure =
    logoutFailure && logoutFailure.kind !== "complete" && logoutFailure.kind !== "anonymous"
      ? {
          overloaded: "Sign-out service is busy",
          unavailable: "Sign-out service unavailable",
          forbidden: "Sign-out request was rejected",
          "invalid-response": "Sign-out response rejected",
          "network-failure": "Sign-out request failed",
        }[logoutFailure.kind]
      : {
          overloaded: "Session service is busy; retry shortly",
          unavailable: "Session service unavailable",
          "invalid-response": "Session response rejected",
          "network-failure": "Session request failed",
        }[result.kind];
  return (
    <div className="identity-stack" role="status">
      <span className="identity-error">{failure}</span>
      {logoutFailure ? <span>Remote sign-out is not confirmed.</span> : null}
      <button type="button" className="button button--secondary" onClick={identity.refresh}>
        <RefreshCw className="button-icon" aria-hidden="true" />
        {logoutFailure ? "Check current session" : "Retry session"}
      </button>
    </div>
  );
}
