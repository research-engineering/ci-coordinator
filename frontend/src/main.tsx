import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { reportRenderFailure } from "./components/ApplicationErrorBoundary";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/workbench.css";
import "./styles/ciEconomics.css";
import "./styles/analytics.css";
import "./styles/activity.css";
import "./styles/configuration.css";
import "./styles/governanceObservation.css";
import "./styles/workflowDiscovery.css";

const root = document.getElementById("root");
if (!root) throw new Error("application root is missing");

createRoot(root, { onCaughtError: reportRenderFailure }).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
