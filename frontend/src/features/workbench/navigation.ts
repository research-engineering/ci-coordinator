import { operationIdIsAdmitted } from "../../api/shared/operationId";
import { admitWorkbenchScope, type WorkbenchScope } from "../../api/workbench/client";
import {
  ACTIVITY_SOURCES,
  type ActivitySource,
  ECONOMICS_TABS,
  type EconomicsTab,
} from "../../domain/consoleSelection";

export const REPOSITORY_VIEWS = [
  "overview",
  "workflows",
  "configuration",
  "economics",
  "governance",
  "audit",
] as const;
export type RepositoryView = (typeof REPOSITORY_VIEWS)[number];
export type ConsoleView = "repositories" | "activity" | RepositoryView;
export const EVIDENCE_TABS = ["plans", "runs", "configEpochs", "overrides"] as const;
export type EvidenceTab = (typeof EVIDENCE_TABS)[number];

export interface ConsoleRoute {
  readonly scope: WorkbenchScope | undefined;
  readonly view: ConsoleView;
  readonly tab: EvidenceTab;
  readonly economicsTab?: EconomicsTab;
  readonly activitySource?: ActivitySource;
}

export function readConsoleRoute(search: string): ConsoleRoute {
  const query = new URLSearchParams(search);
  const scopeKeys = ["installationId", "repositoryId", "limit"] as const;
  const scope = scopeKeys.every((key) => query.getAll(key).length === 1)
    ? admitWorkbenchScope({
        installationId: Number(query.get("installationId")),
        repositoryId: Number(query.get("repositoryId")),
        limit: Number(query.get("limit")),
      })
    : undefined;
  const selected = query.getAll("view").length === 1 ? query.get("view") : null;
  const view =
    selected === "activity"
      ? "activity"
      : !scope || selected === "repositories"
        ? "repositories"
        : (REPOSITORY_VIEWS.find((candidate) => candidate === selected) ??
          (reviewHint(query, scope) ? "workflows" : "overview"));
  const tab =
    query.getAll("tab").length === 1
      ? (EVIDENCE_TABS.find((candidate) => candidate === query.get("tab")) ?? "plans")
      : "plans";
  const economicsTab =
    scope && view === "economics" && query.getAll("economicsTab").length === 1
      ? ECONOMICS_TABS.find((value) => value === query.get("economicsTab"))
      : undefined;
  const activitySource =
    scope && view === "activity" && query.getAll("activitySource").length === 1
      ? ACTIVITY_SOURCES.find((value) => value === query.get("activitySource"))
      : undefined;
  return {
    scope,
    tab,
    view,
    ...(economicsTab && economicsTab !== "registered" ? { economicsTab } : {}),
    ...(activitySource === "business" ? { activitySource } : {}),
  };
}

export function consoleHref(route: ConsoleRoute, currentSearch = ""): string {
  const query = new URLSearchParams();
  const scope = route.scope && admitWorkbenchScope(route.scope);
  if (scope) {
    query.set("installationId", String(scope.installationId));
    query.set("repositoryId", String(scope.repositoryId));
    query.set("limit", String(scope.limit));
    const hint = reviewHint(new URLSearchParams(currentSearch), scope);
    if (hint) {
      query.set("repositoryAttestation", "reviewed");
      query.set("proposalManifestId", hint.manifestId);
      query.set("reviewOperationId", hint.operationId);
    }
  }
  const view = scope || route.view === "activity" ? route.view : "repositories";
  if (scope || view !== "repositories") query.set("view", view);
  if (view === "overview" && route.tab !== "plans") query.set("tab", route.tab);
  if (
    scope &&
    view === "economics" &&
    route.economicsTab &&
    route.economicsTab !== "registered" &&
    ECONOMICS_TABS.includes(route.economicsTab)
  )
    query.set("economicsTab", route.economicsTab);
  if (scope && view === "activity" && route.activitySource === "business")
    query.set("activitySource", route.activitySource);
  const suffix = query.toString();
  return suffix ? `/workbench?${suffix}` : "/workbench";
}

export function sameScope(left: WorkbenchScope | undefined, right: WorkbenchScope | undefined) {
  return (
    left?.installationId === right?.installationId &&
    left?.repositoryId === right?.repositoryId &&
    left?.limit === right?.limit
  );
}

function reviewHint(query: URLSearchParams, scope: WorkbenchScope) {
  const keys = [
    "repositoryAttestation",
    "installationId",
    "repositoryId",
    "proposalManifestId",
    "reviewOperationId",
  ];
  const manifestId = query.get("proposalManifestId");
  const operationId = query.get("reviewOperationId");
  if (
    !keys.every((key) => query.getAll(key).length === 1) ||
    query.get("repositoryAttestation") !== "reviewed" ||
    query.get("installationId") !== String(scope.installationId) ||
    query.get("repositoryId") !== String(scope.repositoryId) ||
    manifestId === null ||
    operationId === null ||
    !operationIdIsAdmitted(operationId)
  )
    return undefined;
  return { manifestId, operationId };
}
