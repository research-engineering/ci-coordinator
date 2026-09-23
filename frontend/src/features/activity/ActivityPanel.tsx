import { ArrowLeft, ArrowRight, Download, RefreshCw, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { type ActivityResult, fetchActivity } from "../../api/activity/client";
import {
  type ActivityQuery,
  activityQuerySchema,
  BUSINESS_ACTIONS,
  SECURITY_ACTIONS,
} from "../../api/activity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { LoadingState } from "../../components/LoadingState";
import type { ActivitySource } from "../../domain/consoleSelection";
import { ActivityTable, actionLabel } from "./ActivityTable";

export function ActivityPanel({
  scope,
  source,
  onSource,
}: {
  readonly scope: WorkbenchScope | undefined;
  readonly source: ActivitySource;
  readonly onSource: (source: ActivitySource) => void;
}) {
  const selected = scope ? source : "security";
  const buttons = useRef<Partial<Record<"security" | "business", HTMLButtonElement | null>>>({});
  return (
    <section aria-label="Administrator activity">
      <div className="activity-tabs" role="tablist" aria-label="Activity source">
        {(["security", ...(scope ? ["business"] : [])] as const).map((value) => (
          <button
            type="button"
            key={value}
            role="tab"
            aria-selected={selected === value}
            id={`activity-${value}`}
            aria-controls="activity-content"
            tabIndex={selected === value ? 0 : -1}
            ref={(element) => {
              buttons.current[value === "security" ? "security" : "business"] = element;
            }}
            onClick={() => onSource(value === "security" ? "security" : "business")}
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const next =
                !scope || event.key === "Home"
                  ? "security"
                  : event.key === "End"
                    ? "business"
                    : selected === "security"
                      ? "business"
                      : "security";
              onSource(next);
              buttons.current[next]?.focus();
            }}
          >
            {value === "security" ? "Access and sessions" : "Repository actions"}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="activity-content" aria-labelledby={`activity-${selected}`}>
        <ActivityQueryPanel
          key={`${selected}:${scope?.installationId}:${scope?.repositoryId}`}
          source={selected}
          scope={scope}
        />
      </div>
    </section>
  );
}

function initialQuery(source: "security" | "business", scope: WorkbenchScope | undefined) {
  const until = new Date();
  until.setUTCHours(24, 0, 0, 0);
  return activityQuerySchema.parse({
    source,
    until: until.toISOString(),
    since: new Date(until.getTime() - 7 * 86400000).toISOString(),
    ...(source === "business" && scope
      ? { installationId: scope.installationId, repositoryId: scope.repositoryId }
      : {}),
  });
}

function ActivityQueryPanel({
  source,
  scope,
}: {
  readonly source: "security" | "business";
  readonly scope: WorkbenchScope | undefined;
}) {
  const [query, setQuery] = useState<ActivityQuery>(() => initialQuery(source, scope));
  const [invalid, setInvalid] = useState(false);
  return (
    <>
      <form
        className="analytics-filters"
        aria-label="Activity filters"
        onSubmit={(event) => {
          event.preventDefault();
          const fields = new FormData(event.currentTarget);
          const through = String(fields.get("through") ?? "");
          const throughTime = Date.parse(`${through}T00:00:00Z`);
          const candidate = activityQuerySchema.safeParse({
            ...query,
            since: `${fields.get("from")}T00:00:00Z`,
            until: Number.isFinite(throughTime)
              ? new Date(throughTime + 86400000).toISOString()
              : "",
            action: fields.get("action") || null,
            actor: fields.get("actor") || null,
          });
          setInvalid(!candidate.success);
          if (candidate.success) setQuery(candidate.data);
        }}
      >
        <label>
          From (UTC)
          <input type="date" name="from" required defaultValue={query.since.slice(0, 10)} />
        </label>
        <label>
          Through (UTC)
          <input
            type="date"
            name="through"
            required
            defaultValue={new Date(Date.parse(query.until) - 86400000).toISOString().slice(0, 10)}
          />
        </label>
        <label>
          Action
          <select name="action" defaultValue="">
            <option value="">All actions</option>
            {(source === "security" ? SECURITY_ACTIONS : BUSINESS_ACTIONS).map((value) => (
              <option key={value} value={value}>
                {actionLabel(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Exact actor ID
          <input type="text" name="actor" maxLength={128} autoComplete="off" />
        </label>
        <button type="submit" className="button button--primary">
          <Search aria-hidden="true" className="button-icon" />
          Apply
        </button>
      </form>
      {invalid ? (
        <p role="alert">Choose a valid UTC interval of at most 31 days and an admitted actor.</p>
      ) : null}
      <ActivityResults key={JSON.stringify(query)} query={query} />
    </>
  );
}

function ActivityResults({ query }: { readonly query: ActivityQuery }) {
  const [request, setRequest] = useState<{ cursor: string | null }>({ cursor: null });
  const [pageNumber, setPageNumber] = useState(1);
  const [result, setResult] = useState<ActivityResult>();
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(false);
  const exportRequest = useRef<AbortController | undefined>(undefined);
  useEffect(() => {
    const controller = new AbortController();
    setResult(undefined);
    void fetchActivity(query, request.cursor, controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setResult(value);
      },
      () => {
        if (!controller.signal.aborted) setResult({ kind: "network-failure" });
      },
    );
    return () => controller.abort();
  }, [query, request]);
  useEffect(() => () => exportRequest.current?.abort(), []);
  const page = result?.kind === "ready" ? result.page : undefined;
  function restart() {
    setRequest({ cursor: null });
    setPageNumber(1);
  }
  async function exportPage() {
    if (exportRequest.current) return;
    const controller = new AbortController();
    exportRequest.current = controller;
    setExporting(true);
    setExportError(false);
    try {
      const received = await fetchActivity(query, request.cursor, controller.signal, true);
      if (controller.signal.aborted) return;
      if (received.kind !== "ready") {
        setExportError(true);
        return;
      }
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(received.page, null, 2)], { type: "application/json" }),
      );
      try {
        const link = document.createElement("a");
        link.href = url;
        link.download = `activity-${query.source}-${query.since.slice(0, 10)}.json`;
        document.body.append(link);
        try {
          link.click();
        } finally {
          link.remove();
        }
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch {
      if (!controller.signal.aborted) setExportError(true);
    } finally {
      exportRequest.current = undefined;
      if (!controller.signal.aborted) setExporting(false);
    }
  }
  return (
    <>
      <div className="activity-toolbar">
        <p>
          {query.source === "security"
            ? "Security events are retained for 30 days."
            : "Selected committed business actions; original audit evidence remains authoritative."}
        </p>
        <button
          type="button"
          className="icon-button"
          aria-label="Refresh activity"
          title="Refresh activity"
          onClick={restart}
          disabled={exporting}
        >
          <RefreshCw aria-hidden="true" />
        </button>
        <button
          type="button"
          className="button button--secondary"
          disabled={!page || exporting}
          onClick={() => void exportPage()}
        >
          <Download aria-hidden="true" className="button-icon" />
          {exporting ? "Preparing export" : "Export page"}
        </button>
      </div>
      {exportError ? (
        <p role="alert">The export could not be confirmed. No automatic retry was made.</p>
      ) : null}
      {result === undefined ? (
        <LoadingState label="Loading activity" />
      ) : page ? (
        <>
          <p className="activity-context">
            {page.context.source === "security"
              ? page.context.issuer
              : `Repository ${page.context.repositoryId}, installation ${page.context.installationId}`}
          </p>
          <ActivityTable page={page} />
          <nav className="economics-pagination" aria-label="Activity pages">
            <button
              type="button"
              className="icon-button"
              title="First activity page"
              aria-label="First activity page"
              onClick={restart}
              disabled={pageNumber === 1 || exporting}
            >
              <ArrowLeft aria-hidden="true" />
            </button>
            <span>Page {pageNumber}</span>
            <button
              type="button"
              className="icon-button"
              title="Next activity page"
              aria-label="Next activity page"
              disabled={page.nextCursor === null || exporting}
              onClick={() => {
                if (page.nextCursor) {
                  setRequest({ cursor: page.nextCursor });
                  setPageNumber((value) => value + 1);
                }
              }}
            >
              <ArrowRight aria-hidden="true" />
            </button>
          </nav>
        </>
      ) : (
        <div role="alert" className="activity-error">
          <strong>
            {result.kind === "unauthenticated"
              ? "Authentication required"
              : result.kind === "forbidden"
                ? "Activity access denied"
                : "Activity could not be loaded"}
          </strong>
          <p>
            {result.kind === "invalid-response"
              ? "The response or continuation could not be validated. Refresh the first page."
              : "Refresh the page after checking your session and activity access."}
          </p>
          <button type="button" className="button button--secondary" onClick={restart}>
            <RefreshCw aria-hidden="true" className="button-icon" />
            Retry from first page
          </button>
        </div>
      )}
    </>
  );
}
