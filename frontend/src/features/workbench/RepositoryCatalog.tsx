import {
  AlertTriangle,
  Building2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  FolderGit2,
  RotateCw,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { components } from "../../api/generated";
import type { WorkbenchScope } from "../../api/workbench/client";
import { type DataColumn, DataTable } from "../../components/DataTable";
import { LoadingState } from "../../components/LoadingState";
import { StatusBadge } from "../../components/StatusBadge";
import { useInstallationCatalog, useRepositoryCatalog } from "./useProviderInventory";

type Installation = components["schemas"]["InstallationResponse"];
type Repository = components["schemas"]["RepositoryResponse"];

const EMPTY_INSTALLATIONS: readonly Installation[] = [];

interface RepositoryCatalogProps {
  readonly itemLimit: number;
  readonly onSelect: (scope: WorkbenchScope, repositoryName?: string) => void;
}

export function RepositoryCatalog({ itemLimit, onSelect }: RepositoryCatalogProps) {
  const [organizationPage, setOrganizationPage] = useState(1);
  const installations = useInstallationCatalog(organizationPage);
  const catalog = installations.state.kind === "settled" ? installations.state.result : undefined;
  const available = catalog?.kind === "ready" ? catalog.catalog.installations : EMPTY_INSTALLATIONS;
  const [installationId, setInstallationId] = useState<number>();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (catalog?.kind !== "ready") return;
    if (
      installationId !== undefined &&
      available.some((item) => item.installationId === installationId && item.state === "active")
    ) {
      return;
    }
    setInstallationId(available.find((item) => item.state === "active")?.installationId);
    setPage(1);
    setSearch("");
  }, [available, installationId, catalog]);

  const activeInstallationId = available.some(
    (item) => item.installationId === installationId && item.state === "active",
  )
    ? installationId
    : undefined;
  const repositories = useRepositoryCatalog(activeInstallationId, page);
  const repositoryResult =
    repositories.state.kind === "settled" ? repositories.state.result : undefined;
  const repositoryPage = repositoryResult?.kind === "ready" ? repositoryResult.page : undefined;
  const filteredRepositories = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!repositoryPage || !normalized) return repositoryPage?.repositories ?? [];
    return repositoryPage.repositories.filter((item) =>
      item.fullName.toLowerCase().includes(normalized),
    );
  }, [repositoryPage, search]);

  const columns: readonly DataColumn<Repository>[] = [
    {
      header: "Repository",
      render: (repository) => (
        <div className="repository-name">
          <strong>{repository.name}</strong>
          <span>{repository.ownerLogin}</span>
        </div>
      ),
    },
    { header: "Default branch", render: (repository) => <code>{repository.defaultBranch}</code> },
    {
      header: "Visibility",
      render: (repository) => <StatusBadge tone="neutral">{repository.visibility}</StatusBadge>,
    },
    {
      header: "Repository state",
      render: (repository) => (
        <StatusBadge tone={repository.archived || repository.disabled ? "warning" : "positive"}>
          {repository.archived ? "Archived" : repository.disabled ? "Disabled" : "Active"}
        </StatusBadge>
      ),
    },
    {
      header: "Evidence access",
      render: (repository) => (
        <div className="repository-access">
          <StatusBadge tone={repository.workbenchAuthorized ? "info" : "neutral"}>
            {repository.workbenchAuthorized ? "Available" : "Catalog only"}
          </StatusBadge>
          {!repository.workbenchAuthorized ? (
            repository.disabled ? (
              <span>Disabled by GitHub.</span>
            ) : (
              <details>
                <summary>Restricted by deployment policy</summary>
                <p>
                  Scope:{" "}
                  <code>
                    {repository.scope.installationId}:{repository.scope.repositoryId}
                  </code>
                </p>
                <a href="https://github.com/research-engineering/ci-coordinator/blob/master/docs/how-to/discover-installed-organizations.md">
                  Repository access configuration
                </a>
              </details>
            )
          ) : null}
        </div>
      ),
    },
    {
      header: "Action",
      render: (repository) => (
        <button
          type="button"
          className="button button--compact"
          disabled={!repository.workbenchAuthorized}
          title={
            repository.workbenchAuthorized
              ? `Open ${repository.fullName}`
              : repository.disabled
                ? "Repository disabled by GitHub"
                : "Repository restricted by deployment policy"
          }
          onClick={() =>
            onSelect(
              {
                installationId: repository.scope.installationId,
                repositoryId: repository.scope.repositoryId,
                limit: itemLimit,
              },
              repository.fullName,
            )
          }
        >
          Open
        </button>
      ),
    },
  ];

  return (
    <section className="repository-catalog" aria-labelledby="repository-catalog-title">
      <header className="section-heading">
        <div>
          <p className="eyebrow">GitHub App access</p>
          <h2 id="repository-catalog-title">Repositories</h2>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="Refresh repositories"
          title="Refresh repositories"
          onClick={() => {
            installations.refresh();
            repositories.refresh();
          }}
        >
          <RotateCw aria-hidden="true" />
        </button>
      </header>

      {installations.state.kind === "loading" ? (
        <LoadingState label="Loading organizations" />
      ) : catalog?.kind !== "ready" ? (
        <CatalogFailure
          kind={catalog?.kind ?? "network-failure"}
          onRetry={installations.refresh}
          retryAfterSeconds={
            catalog?.kind === "rate-limited" ? catalog.retryAfterSeconds : undefined
          }
        />
      ) : (
        <>
          {!catalog.catalog.complete ? (
            <CatalogPartialFailures failures={catalog.catalog.failures} />
          ) : null}
          <div className="catalog-toolbar">
            <div className="catalog-organization">
              <label>
                Organization
                <span className="catalog-organization-select">
                  <select
                    value={installationId ?? ""}
                    disabled={available.length === 0}
                    onChange={(event) => {
                      const value = event.currentTarget.value;
                      setInstallationId(value ? Number(value) : undefined);
                      setPage(1);
                      setSearch("");
                    }}
                  >
                    <option value="" disabled>
                      {available.length === 0
                        ? catalog.catalog.failures.length > 0
                          ? "No organizations available"
                          : "No authorized organizations"
                        : "Select an active organization"}
                    </option>
                    {available.map((item) => (
                      <option
                        key={item.installationId}
                        value={item.installationId}
                        disabled={item.state !== "active"}
                      >
                        {item.accountLogin}
                        {item.state === "active" ? "" : ` (${item.state})`}
                      </option>
                    ))}
                  </select>
                  <ChevronDown aria-hidden="true" focusable="false" />
                </span>
              </label>
              {organizationPage > 1 || catalog.catalog.hasNextPage ? (
                <nav
                  className="catalog-pagination catalog-pagination--organizations"
                  aria-label="Organization pages"
                >
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Previous organization page"
                    title="Previous organization page"
                    disabled={organizationPage === 1}
                    onClick={() => setOrganizationPage((value) => value - 1)}
                  >
                    <ChevronLeft aria-hidden="true" />
                  </button>
                  <span>Page {organizationPage}</span>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Next organization page"
                    title="Next organization page"
                    disabled={!catalog.catalog.hasNextPage}
                    onClick={() => setOrganizationPage((value) => value + 1)}
                  >
                    <ChevronRight aria-hidden="true" />
                  </button>
                </nav>
              ) : null}
            </div>
            <label>
              Search loaded page
              <span className="input-with-icon">
                <Search aria-hidden="true" />
                <input
                  type="search"
                  value={search}
                  disabled={installationId === undefined}
                  onChange={(event) => setSearch(event.currentTarget.value)}
                  placeholder="Repository name"
                />
              </span>
            </label>
          </div>
          {available.length === 0 ? (
            <CatalogMessage
              icon={catalog.catalog.failures.length > 0 ? AlertTriangle : Building2}
              title={
                catalog.catalog.failures.length > 0
                  ? "Authorized organizations unavailable"
                  : "No authorized organizations"
              }
            />
          ) : installationId === undefined ? (
            <CatalogMessage icon={AlertTriangle} title="No active organizations" />
          ) : repositories.state.kind === "loading" ? (
            <LoadingState label="Loading repositories" />
          ) : repositoryPage === undefined ? (
            <CatalogFailure
              kind={repositoryResult?.kind ?? "network-failure"}
              onRetry={repositories.refresh}
              retryAfterSeconds={
                repositoryResult?.kind === "rate-limited"
                  ? repositoryResult.retryAfterSeconds
                  : undefined
              }
            />
          ) : (
            <>
              <div className="catalog-count" aria-live="polite">
                <FolderGit2 aria-hidden="true" />
                <span>
                  {filteredRepositories.length} loaded of {repositoryPage.totalCount}
                </span>
              </div>
              <DataTable
                columns={columns}
                emptyLabel={search ? "No matching repositories on this page." : "No repositories."}
                keyOf={(repository) =>
                  `${repository.scope.installationId}:${repository.scope.repositoryId}`
                }
                rows={filteredRepositories}
              />
              {repositoryPage.page > 1 || repositoryPage.hasNextPage ? (
                <nav className="catalog-pagination" aria-label="Repository pages">
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Previous repository page"
                    title="Previous repository page"
                    disabled={repositoryPage.page === 1}
                    onClick={() => setPage((value) => value - 1)}
                  >
                    <ChevronLeft aria-hidden="true" />
                  </button>
                  <span>Page {repositoryPage.page}</span>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Next repository page"
                    title="Next repository page"
                    disabled={!repositoryPage.hasNextPage}
                    onClick={() => setPage((value) => value + 1)}
                  >
                    <ChevronRight aria-hidden="true" />
                  </button>
                </nav>
              ) : null}
            </>
          )}
        </>
      )}
    </section>
  );
}

interface CatalogMessageProps {
  readonly icon: typeof Building2;
  readonly spin?: boolean;
  readonly title: string;
}

function CatalogMessage({ icon: Icon, spin = false, title }: CatalogMessageProps) {
  return (
    <div className="catalog-message" aria-live="polite">
      <Icon className={spin ? "spin" : undefined} aria-hidden="true" />
      <strong>{title}</strong>
    </div>
  );
}

function CatalogFailure({
  kind,
  onRetry,
  retryAfterSeconds,
}: {
  readonly kind: string;
  readonly onRetry: () => void;
  readonly retryAfterSeconds: number | undefined;
}) {
  const label =
    {
      forbidden: "Repositories access denied",
      "invalid-response": "Repositories response rejected",
      "network-failure": "Repositories connection failed",
      unauthenticated: "Repositories authentication required",
      unavailable: "Repositories unavailable",
      "rate-limited": "GitHub rate limit reached",
      "not-found": "Installation not found",
      suspended: "Installation suspended",
      "unsupported-account": "Personal installations are not supported",
    }[kind] ?? "Repositories unavailable";
  const detail =
    kind === "rate-limited" && retryAfterSeconds !== undefined
      ? ` Retry after ${retryAfterSeconds} seconds.`
      : "";
  return (
    <div className="catalog-message catalog-message--warning" aria-live="polite">
      <AlertTriangle aria-hidden="true" />
      <strong>
        {label}.{detail}
      </strong>
      <button type="button" className="button button--secondary" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

function CatalogPartialFailures({
  failures,
}: {
  readonly failures: readonly components["schemas"]["InstallationFailureResponse"][];
}) {
  return (
    <div className="catalog-notice" role="status">
      <AlertTriangle aria-hidden="true" />
      <div>
        <strong>
          Partial catalog: {failures.length} installation{failures.length === 1 ? "" : "s"}{" "}
          unavailable.
        </strong>
        <ul>
          {failures.map((failure) => (
            <li key={failure.installationId}>
              Installation {failure.installationId}: {installationFailureLabel(failure)}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function installationFailureLabel(
  failure: components["schemas"]["InstallationFailureResponse"],
): string {
  if (failure.reason === "rate_limited") {
    return failure.retryAfterSeconds === null
      ? "GitHub rate limited the request"
      : `GitHub rate limited the request; retry after ${failure.retryAfterSeconds} seconds`;
  }
  return {
    malformed_provider_response: "GitHub returned a malformed response",
    not_found: "installation was not found",
    provider_binding_mismatch: "provider identity binding failed",
    unavailable: "GitHub is unavailable",
  }[failure.reason];
}
