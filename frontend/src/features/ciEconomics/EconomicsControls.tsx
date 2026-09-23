import { AlertTriangle, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import type { EconomicsFailure } from "../../api/ciEconomics/transport";
import { LoadingState } from "../../components/LoadingState";
import type { EconomicsReadState } from "./useEconomicsRead";

export function EconomicsRead<T>({
  state,
  onRetry,
  children,
}: {
  readonly state: EconomicsReadState<T>;
  readonly onRetry: () => void;
  readonly children: (value: T) => ReactNode;
}) {
  if (state.kind === "loading") return <LoadingState label="Loading evidence" />;
  return state.result.kind === "ready" ? (
    children(state.result.value)
  ) : (
    <EconomicsError failure={state.result} onRetry={onRetry} />
  );
}

export function EconomicsError({
  failure,
  onRetry,
  operation = "read",
}: {
  readonly failure: EconomicsFailure;
  readonly onRetry: () => void;
  readonly operation?: "read" | "register";
}) {
  const labels = {
    unauthenticated: "Authentication required",
    forbidden: "Access denied",
    "not-found": "Evidence is not retained",
    unavailable: "Evidence temporarily unavailable",
    "invalid-response": "Evidence could not be validated",
    "network-failure": "Connection failed",
  };
  const registrationLabels = {
    unauthenticated: "Authentication required for registration",
    forbidden: "Registration access denied",
    "not-found": "Run unavailable for registration",
    unavailable: "Registration temporarily unavailable",
    "invalid-response": "Registration response could not be validated",
    "network-failure": "Registration could not be confirmed",
  };
  return (
    <div className="economics-message" role="alert">
      <AlertTriangle className="state-icon" aria-hidden="true" />
      <strong>{(operation === "register" ? registrationLabels : labels)[failure.kind]}</strong>
      <span>
        {failure.kind === "unauthenticated"
          ? "Sign in again before retrying."
          : failure.kind === "forbidden"
            ? "Ask a repository administrator to check your access."
            : operation === "register"
              ? "Retry the same source to confirm registration. If it remains unavailable, search for the run again."
              : "Refresh the evidence. If this continues, ask an administrator to check collection and retention."}
      </span>
      {failure.kind === "unavailable" ? <span>{failure.reason.replaceAll("_", " ")}</span> : null}
      <button className="button button--secondary" type="button" onClick={onRetry}>
        <RefreshCw className="button-icon" aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}

export function EconomicsPagination({
  page,
  next,
  onNext,
  onFirst,
}: {
  readonly page: number;
  readonly next: string | null;
  readonly onNext: (cursor: string) => void;
  readonly onFirst: () => void;
}) {
  return (
    <nav className="economics-pagination" aria-label="Evidence pages">
      <button
        className="icon-button"
        type="button"
        title="First page"
        aria-label="First page"
        disabled={page === 1}
        onClick={onFirst}
      >
        <ChevronLeft aria-hidden="true" />
      </button>
      <span aria-current="page">Page {page}</span>
      <button
        className="icon-button"
        type="button"
        title="Next page"
        aria-label="Next page"
        disabled={next === null}
        onClick={() => {
          if (next !== null) onNext(next);
        }}
      >
        <ChevronRight aria-hidden="true" />
      </button>
    </nav>
  );
}

export function EconomicsRefresh({
  onClick,
  disabled = false,
}: {
  readonly onClick: () => void;
  readonly disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="icon-button"
      aria-label="Refresh evidence"
      title="Refresh evidence"
      onClick={onClick}
      disabled={disabled}
    >
      <RefreshCw aria-hidden="true" />
    </button>
  );
}
