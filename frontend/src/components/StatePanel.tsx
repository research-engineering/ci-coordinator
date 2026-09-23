import { AlertTriangle, Ban, KeyRound, RotateCw, ServerOff } from "lucide-react";
import type { WorkbenchResult } from "../api/workbench/client";

interface StatePanelProps {
  readonly kind: Exclude<WorkbenchResult["kind"], "ready">;
  readonly onRetry: () => void;
}

const STATES = {
  forbidden: {
    description: "Ask an administrator to grant you access to this repository.",
    icon: Ban,
    title: "Repository access denied",
  },
  "invalid-response": {
    description:
      "The repository data could not be validated. Retry; if this persists, contact an administrator.",
    icon: AlertTriangle,
    title: "Response rejected",
  },
  "network-failure": {
    description: "The server could not be reached. Check your connection and retry.",
    icon: ServerOff,
    title: "Connection failed",
  },
  unauthenticated: {
    description: "Your session could not be verified. Sign in again.",
    icon: KeyRound,
    title: "Authentication required",
  },
  unavailable: {
    description: "Repository data is temporarily unavailable. Retry shortly.",
    icon: ServerOff,
    title: "Service unavailable",
  },
} as const;

export function StatePanel({ kind, onRetry }: StatePanelProps) {
  const state = STATES[kind];
  const Icon = state.icon;
  return (
    <section className="state-panel" aria-live="polite">
      <Icon className="state-icon" aria-hidden="true" />
      <h2>{state.title}</h2>
      <p>{state.description}</p>
      <button type="button" className="button button--secondary" onClick={onRetry}>
        <RotateCw className="button-icon" aria-hidden="true" />
        Retry
      </button>
    </section>
  );
}
