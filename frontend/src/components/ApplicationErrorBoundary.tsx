import { AlertTriangle, RotateCw } from "lucide-react";
import { Component, type ReactNode } from "react";

export function reportRenderFailure(): void {
  console.error("Operator console rendering failed.");
}

export class ApplicationErrorBoundary extends Component<
  { readonly children: ReactNode; readonly panelName?: string },
  { readonly failed: boolean }
> {
  override state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  override render() {
    if (!this.state.failed) return this.props.children;
    const panel = this.props.panelName !== undefined;
    const Container = panel ? "section" : "main";
    const Heading = panel ? "h2" : "h1";
    return (
      <Container className={panel ? "state-panel" : "application-recovery"} aria-live="polite">
        <AlertTriangle className={panel ? "state-icon" : "recovery-icon"} aria-hidden="true" />
        <Heading>{panel ? `${this.props.panelName} unavailable` : "Console unavailable"}</Heading>
        <p>
          {panel ? "This panel could not be displayed." : "The console could not be displayed."}
        </p>
        <p>Refreshing discards unsaved work in this tab.</p>
        <p>Check the current state before repeating an interrupted action.</p>
        <button
          className="button button--primary"
          onClick={() => globalThis.location.reload()}
          type="button"
        >
          <RotateCw className="button-icon" aria-hidden="true" />
          Refresh console
        </button>
      </Container>
    );
  }
}
