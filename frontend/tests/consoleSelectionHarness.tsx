import { type ComponentProps, useState } from "react";
import type { ActivitySource, EconomicsTab } from "../src/domain/consoleSelection";
import { ActivityPanel as ControlledActivityPanel } from "../src/features/activity/ActivityPanel";
import { EconomicsConsole as ControlledEconomicsConsole } from "../src/features/ciEconomics/EconomicsConsole";
import { RepositoryWorkspace as ControlledRepositoryWorkspace } from "../src/features/workbench/RepositoryWorkspace";

type EconomicsProps = Omit<
  ComponentProps<typeof ControlledEconomicsConsole>,
  "selectedTab" | "onSelectTab"
>;

export function EconomicsConsole(props: EconomicsProps) {
  return <EconomicsSelection key={JSON.stringify(props.scope)} {...props} />;
}

function EconomicsSelection(props: EconomicsProps) {
  const [selectedTab, onSelectTab] = useState<EconomicsTab>("registered");
  return (
    <ControlledEconomicsConsole {...props} selectedTab={selectedTab} onSelectTab={onSelectTab} />
  );
}

export function ActivityPanel({
  scope,
}: Pick<ComponentProps<typeof ControlledActivityPanel>, "scope">) {
  const [source, onSource] = useState<ActivitySource>("security");
  return <ControlledActivityPanel scope={scope} source={source} onSource={onSource} />;
}

type WorkspaceProps = Omit<
  ComponentProps<typeof ControlledRepositoryWorkspace>,
  "economicsTab" | "onEconomicsTab"
>;

export function RepositoryWorkspace(props: WorkspaceProps) {
  return <WorkspaceSelection key={JSON.stringify(props.scope)} {...props} />;
}

function WorkspaceSelection(props: WorkspaceProps) {
  const [economicsTab, onEconomicsTab] = useState<EconomicsTab>("registered");
  return (
    <ControlledRepositoryWorkspace
      {...props}
      economicsTab={economicsTab}
      onEconomicsTab={onEconomicsTab}
    />
  );
}
