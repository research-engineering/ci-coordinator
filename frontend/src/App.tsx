import { WorkbenchPage } from "./features/workbench/WorkbenchPage";

export function App() {
  return (
    <ApplicationErrorBoundary>
      <WorkbenchPage />
    </ApplicationErrorBoundary>
  );
}

import { ApplicationErrorBoundary } from "./components/ApplicationErrorBoundary";
