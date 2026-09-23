import { useState } from "react";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { LoadingState } from "../../components/LoadingState";
import { EconomicsError, EconomicsRefresh } from "./EconomicsControls";
import { ObservationEditor } from "./ObservationEditor";
import { ObservationGaps } from "./ObservationGaps";
import { ObservationProgress } from "./ObservationProgress";
import { useObservationStatus } from "./useObservationStatus";

export function ObservationPanel({
  scope,
  session,
  active = true,
}: {
  readonly scope: WorkbenchScope;
  readonly session: ControlPlaneSession | undefined;
  readonly active?: boolean;
}) {
  const { state, refresh } = useObservationStatus(scope, active);
  const [showGaps, setShowGaps] = useState(false);
  return (
    <section className="economics-subsection" aria-label="Repository observation">
      <div className="economics-subheading">
        <h2>Repository observation</h2>
        <div className="economics-actions">
          <span role="status">
            {state.refreshing
              ? "Refreshing"
              : state.failure
                ? "Refresh failed"
                : "Latest recorded status"}
          </span>
          <EconomicsRefresh onClick={refresh} disabled={state.refreshing} />
        </div>
      </div>
      {state.failure ? <EconomicsError failure={state.failure} onRetry={refresh} /> : null}
      {state.value ? (
        <>
          {state.failure ? (
            <p className="economics-prompt" role="status">
              Showing the last validated status. Current scan activity is unknown.
            </p>
          ) : null}
          <ObservationProgress status={state.value} />
          <ObservationEditor
            scope={scope}
            snapshot={state.value.snapshot}
            session={session}
            stale={state.failure !== undefined}
            onSaved={refresh}
          />
          <details
            className="observation-gap-disclosure"
            onToggle={(event) => setShowGaps(event.currentTarget.open)}
          >
            <summary>Coverage gaps</summary>
            {showGaps ? <ObservationGaps scope={scope} /> : null}
          </details>
        </>
      ) : state.refreshing ? (
        <LoadingState label="Loading observation" />
      ) : null}
    </section>
  );
}
