import { type KeyboardEvent, lazy, Suspense, useEffect, useRef, useState } from "react";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { LoadingState } from "../../components/LoadingState";
import { ECONOMICS_TABS as TABS, type EconomicsTab as Tab } from "../../domain/consoleSelection";
import { CiEconomicsPanel } from "../workbench/CiEconomicsPanel";
import { BudgetPoliciesPanel } from "./BudgetPoliciesPanel";
import { BudgetSignalsPanel } from "./BudgetSignalsPanel";
import { HistoryPanel } from "./HistoryPanel";
import { ObservationPanel } from "./ObservationPanel";
import { RegisteredRuns } from "./RegisteredRuns";
import { SourceDiscoveryPanel } from "./SourceDiscoveryPanel";

const AnalyticsPanel = lazy(() =>
  import("./AnalyticsPanel").then((module) => ({ default: module.AnalyticsPanel })),
);

const LABELS = {
  registered: "Registered runs",
  discover: "Discover runs",
  reconciled: "Reconciled attempts",
  observation: "Observation",
  history: "History",
  analytics: "Analytics",
  budgets: "Budgets",
  signals: "Signals",
};

type ConsoleProps = {
  readonly scope: WorkbenchScope;
  readonly repositoryCreatedAt?: string | undefined;
  readonly authorityRevision: number;
  readonly session: ControlPlaneSession | undefined;
  readonly active?: boolean;
  readonly selectedTab: Tab;
  readonly onSelectTab: (tab: Tab) => void;
};

export function EconomicsConsole(props: ConsoleProps) {
  const { scope, authorityRevision, session } = props;
  const key = JSON.stringify([
    scope.installationId,
    scope.repositoryId,
    authorityRevision,
    session?.user.actorId,
    session?.roles,
    session?.expiresAt,
  ]);
  return <ScopedEconomicsConsole key={key} {...props} />;
}

function ScopedEconomicsConsole({
  scope,
  repositoryCreatedAt,
  authorityRevision,
  session,
  active = true,
  selectedTab: tab,
  onSelectTab,
}: ConsoleProps) {
  const [retained, setRetained] = useState({
    observation: tab === "observation",
    history: tab === "history",
    analytics: tab === "analytics",
  });
  useEffect(() => {
    if (tab === "observation" || tab === "history" || tab === "analytics")
      setRetained((previous) => (previous[tab] ? previous : { ...previous, [tab]: true }));
  }, [tab]);
  const buttons = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  function selectTab(next: Tab) {
    onSelectTab(next);
  }
  function navigate(event: KeyboardEvent<HTMLButtonElement>, current: Tab) {
    const index = TABS.indexOf(current);
    const target =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? TABS.length - 1
          : event.key === "ArrowRight"
            ? (index + 1) % TABS.length
            : event.key === "ArrowLeft"
              ? (index + TABS.length - 1) % TABS.length
              : undefined;
    const next = target === undefined ? undefined : TABS[target];
    if (!next) return;
    event.preventDefault();
    selectTab(next);
    buttons.current[next]?.focus();
  }
  return (
    <section className="ci-economics" aria-label="Repository economics">
      <div className="evidence-tabs" role="tablist" aria-label="Economics views">
        {TABS.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            id={`economics-tab-${item}`}
            aria-controls={`economics-panel-${item}`}
            aria-selected={tab === item}
            tabIndex={tab === item ? 0 : -1}
            ref={(element) => {
              buttons.current[item] = element;
            }}
            onClick={() => selectTab(item)}
            onKeyDown={(event) => navigate(event, item)}
          >
            {LABELS[item]}
          </button>
        ))}
      </div>
      {TABS.map((item) => (
        <div
          key={item}
          role="tabpanel"
          id={`economics-panel-${item}`}
          aria-labelledby={`economics-tab-${item}`}
          hidden={tab !== item}
        >
          {(!active || tab !== item) &&
          !(
            (item === "observation" || item === "history" || item === "analytics") &&
            retained[item]
          ) ? null : item === "registered" ? (
            <RegisteredRuns scope={scope} />
          ) : item === "discover" ? (
            <SourceDiscoveryPanel scope={scope} session={session} />
          ) : item === "observation" ? (
            <ObservationPanel scope={scope} session={session} active={active && tab === item} />
          ) : item === "history" ? (
            <HistoryPanel
              scope={scope}
              repositoryCreatedAt={repositoryCreatedAt}
              session={session}
              active={active && tab === item}
            />
          ) : item === "analytics" ? (
            <Suspense fallback={<LoadingState label="Loading analytics" />}>
              <AnalyticsPanel
                scope={scope}
                session={session}
                active={active && tab === item}
                onHistory={() => selectTab("history")}
              />
            </Suspense>
          ) : item === "budgets" ? (
            <BudgetPoliciesPanel scope={scope} session={session} />
          ) : item === "signals" ? (
            <BudgetSignalsPanel scope={scope} />
          ) : (
            <CiEconomicsPanel scope={scope} authorityRevision={authorityRevision} />
          )}
        </div>
      ))}
    </section>
  );
}
