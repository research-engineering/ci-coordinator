import { RefreshCw } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { LoadingState } from "../../components/LoadingState";
import { EconomicsError, EconomicsRefresh } from "./EconomicsControls";
import { HistoryEditor } from "./HistoryEditor";
import { HistoryProgress } from "./HistoryProgress";
import { useHistoryStatus } from "./useHistoryStatus";

const ArchiveBrowser = lazy(() =>
  import("./ArchiveBrowser").then((module) => ({ default: module.ArchiveBrowser })),
);

export function HistoryPanel({
  scope,
  repositoryCreatedAt,
  session,
  active = true,
}: {
  readonly scope: WorkbenchScope;
  readonly repositoryCreatedAt?: string | undefined;
  readonly session: ControlPlaneSession | undefined;
  readonly active?: boolean;
}) {
  const { state, refresh } = useHistoryStatus(scope, active);
  const [selection, setView] = useState<string>();
  const view = selection ?? (state.value?.snapshot === null ? "settings" : "records");
  useEffect(() => {
    if (selection === undefined && state.value)
      setView(state.value.snapshot === null ? "settings" : "records");
  }, [selection, state.value]);
  return (
    <section className="economics-subsection" aria-label="Historical Actions archive">
      <div className="economics-subheading">
        <h2>Actions history</h2>
        <div className="economics-actions">
          <span role="status" className="history-refresh-status">
            {state.refreshing ? (
              <RefreshCw aria-hidden="true" className="history-refreshing" />
            ) : null}
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
            <p role="status">
              Showing the last validated status. Current scan activity is unknown.
            </p>
          ) : null}
          <HistoryProgress status={state.value} />
          <fieldset className="archive-views">
            <legend>Archive view</legend>
            {[
              ["records", "Retained runs"],
              ["gaps", "Collection gaps"],
              ["settings", "Collection settings"],
            ].map(([id, label]) => (
              <label key={id}>
                <input
                  type="radio"
                  name="archive-view"
                  value={id}
                  checked={view === id}
                  onChange={() => setView(id ?? "records")}
                />
                {label}
              </label>
            ))}
          </fieldset>
          <div hidden={view !== "records"}>
            <Suspense fallback={<LoadingState label="Loading archive" />}>
              <ArchiveBrowser
                scope={scope}
                status={state.value}
                session={session}
                kind="records"
                active={active && view === "records"}
                onChanged={refresh}
              />
            </Suspense>
          </div>
          <div hidden={view !== "gaps"}>
            <Suspense fallback={<LoadingState label="Loading archive gaps" />}>
              <ArchiveBrowser
                scope={scope}
                status={state.value}
                session={session}
                kind="gaps"
                active={active && view === "gaps"}
                onChanged={refresh}
              />
            </Suspense>
          </div>
          <div hidden={state.value.snapshot !== null && view !== "settings"}>
            <HistoryEditor
              scope={scope}
              repositoryCreatedAt={repositoryCreatedAt}
              status={state.value}
              session={session}
              stale={state.failure !== undefined}
              onSaved={() => {
                setView("settings");
                refresh();
              }}
            />
          </div>
        </>
      ) : state.refreshing ? (
        <LoadingState label="Loading history status" />
      ) : null}
    </section>
  );
}
