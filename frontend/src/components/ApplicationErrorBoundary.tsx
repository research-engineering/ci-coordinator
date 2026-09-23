import { AlertTriangle, RotateCw } from "lucide-react";
import { Component, type ReactNode } from "react";

export function reportRenderFailure(): void {
  console.error("Operator console rendering failed.");
}

export class ApplicationErrorBoundary extends Component<
  { readonly children: ReactNode },
  { readonly failed: boolean }
> {
  override state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  override render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="application-recovery">
        <AlertTriangle className="recovery-icon" aria-hidden="true" />
        <h1>Console unavailable</h1>
        <p>The console could not be displayed. Refresh to reconnect.</p>
        <p>Check the current state before repeating an interrupted action.</p>
        <button
          className="button button--primary"
          onClick={() => globalThis.location.reload()}
          type="button"
        >
          <RotateCw className="button-icon" aria-hidden="true" />
          Refresh console
        </button>
      </main>
    );
  }
}
