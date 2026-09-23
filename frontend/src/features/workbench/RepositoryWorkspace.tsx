import { type MouseEvent, type ReactNode, useEffect, useState } from "react";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { ExpectedActiveEpoch } from "../../api/repositoryAttestation/client";
import type { WorkbenchScope } from "../../api/workbench/client";
import type { WorkbenchSnapshot } from "../../api/workbench/schema";
import { LoadingState } from "../../components/LoadingState";
import { StatePanel } from "../../components/StatePanel";
import type { EconomicsTab } from "../../domain/consoleSelection";
import { EconomicsConsole } from "../ciEconomics/EconomicsConsole";
import { ConfigurationPage } from "../configuration/ConfigurationPage";
import { EvidenceTabs } from "./EvidenceTabs";
import { GovernanceObservationPanel } from "./GovernanceObservationPanel";
import { type ConsoleView, EVIDENCE_TABS, type EvidenceTab } from "./navigation";
import { useWorkbenchSnapshot } from "./useWorkbenchSnapshot";
import { WorkbenchEvidence } from "./WorkbenchEvidence";
import { WorkbenchSummary } from "./WorkbenchSummary";
import { WorkflowDiscoveryPanel } from "./WorkflowDiscoveryPanel";

export function RepositoryWorkspace({
  authorityRevision,
  scope,
  session,
  view,
  tab,
  onTab,
  onWorkflows,
  economicsTab,
  onEconomicsTab,
}: {
  readonly authorityRevision: number;
  readonly scope: WorkbenchScope;
  readonly session: ControlPlaneSession | undefined;
  readonly view: ConsoleView;
  readonly tab: EvidenceTab;
  readonly onTab: (tab: EvidenceTab) => void;
  readonly onWorkflows?: (event: MouseEvent<HTMLAnchorElement>) => void;
  readonly economicsTab: EconomicsTab;
  readonly onEconomicsTab: (tab: EconomicsTab) => void;
}) {
  const query = useWorkbenchSnapshot(scope, authorityRevision);
  const result = query.state.kind === "settled" ? query.state.result : undefined;
  const snapshot = result?.kind === "ready" ? result.snapshot : undefined;

  return (
    <>
      {view === "overview" || view === "audit" ? (
        query.state.kind === "loading" ? (
          <LoadingState label="Loading snapshot" />
        ) : snapshot ? (
          <>
            <WorkbenchSummary snapshot={snapshot} />
            {view === "overview" ? (
              <>
                <EvidenceTabs selected={tab} onSelect={onTab} />
                {EVIDENCE_TABS.map((panel) => (
                  <div
                    key={panel}
                    id={`repository-evidence-panel-${panel}`}
                    role="tabpanel"
                    aria-labelledby={`evidence-tab-${panel}`}
                    // biome-ignore lint/a11y/noNoninteractiveTabindex: WAI-ARIA tabs require a focusable panel before non-focusable content.
                    tabIndex={0}
                    hidden={tab !== panel}
                  >
                    {tab === panel ? (
                      <WorkbenchEvidence snapshot={snapshot} section={panel} />
                    ) : null}
                  </div>
                ))}
              </>
            ) : (
              <WorkbenchEvidence snapshot={snapshot} section="auditEvents" />
            )}
          </>
        ) : result && result.kind !== "ready" ? (
          <StatePanel kind={result.kind} onRetry={query.refresh} />
        ) : null
      ) : null}
      <RetainedTask active={view === "configuration"}>
        <ConfigurationPage
          authorityRevision={authorityRevision}
          scope={scope}
          session={session}
          active={view === "configuration"}
          onWorkflows={onWorkflows}
        />
      </RetainedTask>
      <RetainedTask active={view === "economics"}>
        <EconomicsConsole
          selectedTab={economicsTab}
          onSelectTab={onEconomicsTab}
          authorityRevision={authorityRevision}
          scope={scope}
          session={session}
          active={view === "economics"}
        />
      </RetainedTask>
      <RetainedTask active={view === "governance"}>
        <GovernanceObservationPanel
          authorityRevision={authorityRevision}
          csrfToken={session?.csrfToken}
          scope={scope}
        />
      </RetainedTask>
      <RetainedTask active={view === "workflows"}>
        <WorkflowDiscoveryPanel
          expectedActive={snapshot ? activeEpoch(snapshot) : undefined}
          scope={scope}
          session={session}
        />
      </RetainedTask>
    </>
  );
}

function RetainedTask({
  active,
  children,
}: {
  readonly active: boolean;
  readonly children: ReactNode;
}) {
  const [visited, setVisited] = useState(active);
  useEffect(() => {
    if (active) setVisited(true);
  }, [active]);
  return active || visited ? <div hidden={!active}>{children}</div> : null;
}

function activeEpoch(snapshot: WorkbenchSnapshot): ExpectedActiveEpoch | null | undefined {
  const epoch = snapshot.configEpochs.find((candidate) => candidate.active);
  if (!epoch) return null;
  return epoch.activeRevision === null
    ? undefined
    : { epochId: epoch.epochId, revision: epoch.activeRevision };
}
