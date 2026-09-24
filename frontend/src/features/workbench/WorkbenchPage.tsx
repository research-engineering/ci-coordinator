import {
  ChartNoAxesCombined,
  FileSliders,
  FolderGit2,
  LayoutDashboard,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  ShieldCheck,
  UserRoundCheck,
  Workflow,
  X,
} from "lucide-react";
import { lazy, type MouseEvent, Suspense, useEffect, useRef, useState } from "react";
import type { WorkbenchScope } from "../../api/workbench/client";
import { LoadingState } from "../../components/LoadingState";
import { ControlPlaneIdentityControl } from "../auth/ControlPlaneIdentityControl";
import { beginSessionLogin, consumeSessionReturn } from "../auth/sessionNavigation";
import { useControlPlaneSession } from "../auth/useControlPlaneSession";
import { consoleHref, type RepositoryView } from "./navigation";
import { RepositoryCatalog } from "./RepositoryCatalog";
import { RepositoryWorkspace } from "./RepositoryWorkspace";
import { ScopeForm } from "./ScopeForm";
import { useConsoleNavigation } from "./useConsoleNavigation";

const DEFAULT_SCOPE: WorkbenchScope = { installationId: 1, limit: 10, repositoryId: 1 };
const ActivityPanel = lazy(() =>
  import("../activity/ActivityPanel").then((module) => ({ default: module.ActivityPanel })),
);
const VIEWS = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "workflows", label: "Workflows", icon: Workflow },
  { id: "configuration", label: "Configuration", icon: FileSliders },
  { id: "economics", label: "CI economics", icon: ChartNoAxesCombined },
  { id: "governance", label: "Governance", icon: ShieldCheck },
  { id: "audit", label: "Audit log", icon: ScrollText },
] as const satisfies readonly {
  readonly id: RepositoryView;
  readonly label: string;
  readonly icon: typeof Menu;
}[];

export function WorkbenchPage() {
  const navigation = useConsoleNavigation();
  const { scope, view, tab } = navigation.route;
  const identity = useControlPlaneSession();
  const returnTo = consoleHref(navigation.route);
  const [selection, setSelection] = useState<{
    readonly scope: WorkbenchScope;
    readonly name: string | undefined;
    readonly createdAt: string | undefined;
    readonly authorityRevision: number;
  }>();
  const [menuOpen, setMenuOpen] = useState(false);
  const [compactPreference, setCompactPreference] = useState<boolean>();
  const compact = compactPreference ?? view === "repositories";
  const sidebarAction = compact ? "Expand sidebar" : "Collapse sidebar";
  const heading = useRef<HTMLHeadingElement>(null);
  const menu = useRef<HTMLButtonElement>(null);
  const previousView = useRef(view);
  const selected = VIEWS.find((candidate) => candidate.id === view);
  const title = view === "activity" ? "Activity" : (selected?.label ?? "Repositories");
  const repositoryName =
    selection?.authorityRevision === identity.authorityRevision &&
    selection?.scope.installationId === scope?.installationId &&
    selection?.scope.repositoryId === scope?.repositoryId
      ? selection?.name
      : undefined;
  const repositoryCreatedAt =
    selection?.authorityRevision === identity.authorityRevision &&
    selection?.scope.installationId === scope?.installationId &&
    selection?.scope.repositoryId === scope?.repositoryId
      ? selection?.createdAt
      : undefined;
  const session =
    identity.state.kind === "settled" && identity.state.result.kind === "authenticated"
      ? identity.state.result.session
      : undefined;

  useEffect(() => {
    if (!identity.recoveryRequired) return;
    const recover = () => {
      if (document.visibilityState === "visible") beginSessionLogin(returnTo, true);
    };
    recover();
    document.addEventListener("visibilitychange", recover);
    return () => document.removeEventListener("visibilitychange", recover);
  }, [identity.recoveryRequired, returnTo]);

  useEffect(() => {
    if (!session) return;
    const destination = consumeSessionReturn();
    if (destination && location.pathname === "/workbench" && !location.search && !location.hash) {
      history.replaceState(null, "", destination);
      dispatchEvent(new PopStateEvent("popstate"));
    }
  }, [session]);

  useEffect(() => {
    document.title = `${title} | CI Coordinator`;
    if (previousView.current !== view) {
      heading.current?.focus();
      setMenuOpen(false);
      previousView.current = view;
    }
  }, [title, view]);

  function selectScope(nextScope: WorkbenchScope, name?: string, createdAt?: string) {
    setSelection({
      scope: nextScope,
      name,
      createdAt,
      authorityRevision: identity.authorityRevision,
    });
    navigation.navigate({ scope: nextScope, tab: "plans", view: "overview" });
  }

  function followLink(event: MouseEvent<HTMLAnchorElement>) {
    navigation.followLink(event);
    if (event.defaultPrevented) {
      setMenuOpen(false);
      heading.current?.focus();
    }
  }

  return (
    <div className={`app-shell${compact ? " sidebar-compact" : ""}`}>
      <aside className="sidebar">
        <div className="brand-mark" aria-hidden="true">
          CI
        </div>
        <div className="brand-copy">
          <strong>CI Coordinator</strong>
          <span>Operator console</span>
        </div>
        <button
          ref={menu}
          type="button"
          className="navigation-toggle icon-button"
          aria-label="Toggle navigation"
          aria-expanded={menuOpen}
          aria-controls="console-navigation"
          onClick={() => setMenuOpen(!menuOpen)}
        >
          {menuOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </button>
        <button
          type="button"
          className="sidebar-collapse icon-button"
          aria-label={sidebarAction}
          title={sidebarAction}
          aria-expanded={!compact}
          aria-controls="console-navigation"
          onClick={() => setCompactPreference(!compact)}
        >
          {compact ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
        </button>
        <nav
          id="console-navigation"
          aria-label="Primary navigation"
          className={menuOpen ? "navigation-open" : undefined}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setMenuOpen(false);
              menu.current?.focus();
            }
          }}
        >
          <a
            href={consoleHref({ ...navigation.route, view: "repositories" }, location.search)}
            onClick={followLink}
            aria-current={view === "repositories" ? "page" : undefined}
            title="Repositories"
          >
            <FolderGit2 className="nav-icon" aria-hidden="true" />
            <span className="navigation-label">Repositories</span>
          </a>
          <a
            href={consoleHref({ ...navigation.route, view: "activity" }, location.search)}
            onClick={followLink}
            aria-current={view === "activity" ? "page" : undefined}
            title="Activity"
          >
            <UserRoundCheck className="nav-icon" aria-hidden="true" />
            <span className="navigation-label">Activity</span>
          </a>
          {scope ? (
            <div className="repository-navigation">
              <p className="navigation-group-label">Selected repository</p>
              <p className="navigation-scope">
                {repositoryName ?? `Repository ${scope.repositoryId}`}
                <span>Installation {scope.installationId}</span>
              </p>
              {VIEWS.map(({ id, label, icon: Icon }) => (
                <a
                  key={id}
                  href={consoleHref({ ...navigation.route, view: id }, location.search)}
                  onClick={followLink}
                  aria-current={view === id ? "page" : undefined}
                  title={label}
                >
                  <Icon className="nav-icon" aria-hidden="true" />
                  <span className="navigation-label">{label}</span>
                </a>
              ))}
            </div>
          ) : null}
        </nav>
        <section className="sidebar-account" aria-label="Administrator account">
          <ControlPlaneIdentityControl identity={identity} returnTo={returnTo} />
        </section>
      </aside>
      <main>
        <header className="page-header">
          <div>
            <p className="eyebrow">
              {view === "repositories" || view === "activity"
                ? "CI Coordinator"
                : "Repository workspace"}
            </p>
            <h1 ref={heading} tabIndex={-1}>
              {title}
            </h1>
          </div>
        </header>
        {identity.state.kind === "settled" && !identity.loggingOut ? (
          <>
            <div hidden={view !== "repositories"}>
              <RepositoryCatalog
                itemLimit={scope?.limit ?? DEFAULT_SCOPE.limit}
                key={`catalog:${identity.authorityRevision}`}
                onSelect={selectScope}
              />
              <details className="manual-scope">
                <summary>Advanced degraded access</summary>
                <ScopeForm
                  key={`scope:${identity.authorityRevision}:${scope?.installationId}:${scope?.repositoryId}:${scope?.limit}`}
                  initialScope={scope ?? DEFAULT_SCOPE}
                  onSubmit={selectScope}
                />
              </details>
            </div>
            {view === "activity" ? (
              <Suspense fallback={<LoadingState label="Loading activity" />}>
                <ActivityPanel
                  key={`activity:${identity.authorityRevision}:${scope?.installationId}:${scope?.repositoryId}`}
                  scope={scope}
                  source={navigation.route.activitySource ?? "security"}
                  onSource={(activitySource) =>
                    navigation.navigate({ ...navigation.route, activitySource })
                  }
                />
              </Suspense>
            ) : null}
            {scope ? (
              <RepositoryWorkspace
                key={`workspace:${identity.authorityRevision}:${scope.installationId}:${scope.repositoryId}`}
                authorityRevision={identity.authorityRevision}
                scope={scope}
                repositoryCreatedAt={repositoryCreatedAt}
                session={session}
                view={view}
                tab={tab}
                economicsTab={navigation.route.economicsTab ?? "registered"}
                onEconomicsTab={(economicsTab) =>
                  navigation.navigate({ ...navigation.route, view: "economics", economicsTab })
                }
                onTab={(nextTab) => navigation.navigate({ scope, view: "overview", tab: nextTab })}
                onWorkflows={followLink}
              />
            ) : null}
          </>
        ) : null}
      </main>
    </div>
  );
}
