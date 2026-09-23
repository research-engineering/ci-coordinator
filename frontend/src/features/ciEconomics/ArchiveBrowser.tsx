import { ArrowLeft, List, Search } from "lucide-react";
import { useCallback, useState } from "react";
import { selectedRepairIntervals } from "../../api/ciEconomics/archiveGapRepairSchema";
import { fetchArchiveDetail, fetchArchivePage } from "../../api/ciEconomics/archiveReadClient";
import {
  type ArchiveQuery,
  type ArchiveRead,
  archiveDetailQuerySchema,
  archiveQuerySchema,
} from "../../api/ciEconomics/archiveReadSchema";
import type {
  ArchiveAttemptDetail,
  ArchiveRecord,
} from "../../api/ciEconomics/archiveRecordSchema";
import type { HistoryStatus } from "../../api/ciEconomics/historyStatusSchema";
import { sameScope } from "../../api/ciEconomics/sourceSchema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { ScrollableRegion } from "../../components/ScrollableRegion";
import { formatDateTime } from "../../domain/format";
import { ArchiveGapRepair, type GapRepairSelection } from "./ArchiveGapRepair";
import { ArchiveGaps } from "./ArchiveGaps";
import { ArchiveRetention } from "./ArchiveRetention";
import { EconomicsPagination, EconomicsRead, EconomicsRefresh } from "./EconomicsControls";
import { useEconomicsRead } from "./useEconomicsRead";

export interface ArchiveInspection {
  readonly query: ArchiveQuery;
  readonly configurationRevision: number;
  readonly dataRevision: number;
}

export function ArchiveBrowser({
  scope,
  status,
  session,
  kind = "records",
  onChanged,
  active = true,
  inspection,
}: {
  readonly scope: WorkbenchScope;
  readonly status: HistoryStatus;
  readonly session: ControlPlaneSession | undefined;
  readonly kind?: "records" | "gaps";
  readonly onChanged: () => void;
  readonly active?: boolean;
  readonly inspection?: ArchiveInspection | undefined;
}) {
  const generation = status.snapshot?.generation;
  if (generation === undefined)
    return <p role="status">Configure historical collection to browse retained runs.</p>;
  if (
    inspection &&
    (inspection.query.generation !== generation ||
      !sameScope(inspection.query, scope) ||
      inspection.query.kind !== "records")
  )
    return (
      <p role="alert">The archive scope changed. Return to analytics and refresh the selection.</p>
    );
  return (
    <ArchiveSelection
      key={`${scope.installationId}:${scope.repositoryId}:${generation}:${kind}:${session?.user.actorId ?? "anonymous"}:${inspection ? JSON.stringify(inspection) : ""}`}
      query={
        inspection?.query ??
        archiveQuerySchema.parse({
          installationId: scope.installationId,
          repositoryId: scope.repositoryId,
          generation,
          kind,
        })
      }
      status={status}
      session={session}
      onChanged={onChanged}
      active={active}
      inspection={inspection}
    />
  );
}

function ArchiveSelection({
  query: initial,
  status,
  session,
  onChanged,
  active,
  inspection,
}: {
  readonly query: ArchiveQuery;
  readonly status: HistoryStatus;
  readonly session: ControlPlaneSession | undefined;
  readonly onChanged: () => void;
  readonly active: boolean;
  readonly inspection: ArchiveInspection | undefined;
}) {
  const [query, setQuery] = useState(initial);
  const [invalid, setInvalid] = useState(false);
  const [selected, setSelected] = useState<ArchiveRecord | null>(null);
  const [retentionPage, setRetentionPage] = useState<ArchiveRead | null>(null);
  const [gapRepair, setGapRepair] = useState<GapRepairSelection | null>(null);
  const [gapRefresh, setGapRefresh] = useState(0);
  return (
    <>
      {active && selected !== null ? (
        <>
          <button
            type="button"
            className="button button--secondary"
            onClick={() => setSelected(null)}
          >
            <ArrowLeft aria-hidden="true" className="button-icon" />
            All retained runs
          </button>
          <ArchiveAttempt
            query={query}
            record={selected}
            onRetention={setRetentionPage}
            retentionOpen={retentionPage !== null}
            inspection={inspection}
          />
        </>
      ) : active ? (
        <>
          {initial.kind === "records" && inspection === undefined ? (
            <form
              className="analytics-filters"
              aria-label="Archive filters"
              onSubmit={(event) => {
                event.preventDefault();
                const fields = new FormData(event.currentTarget);
                const workflow = String(fields.get("workflow") ?? "");
                const from = String(fields.get("from") ?? "");
                const through = String(fields.get("through") ?? "");
                const result = archiveQuerySchema.safeParse({
                  ...initial,
                  workflowId: workflow ? Number(workflow) : null,
                  createdFrom: from ? `${from}T00:00:00Z` : null,
                  createdThrough: through ? `${through}T23:59:59.999999Z` : null,
                });
                setInvalid(!result.success);
                if (result.success) setQuery(result.data);
              }}
            >
              <label>
                From (UTC)
                <input type="date" name="from" />
              </label>
              <label>
                Through (UTC)
                <input type="date" name="through" />
              </label>
              <label>
                Workflow ID
                <input type="number" name="workflow" min="1" step="1" placeholder="All workflows" />
              </label>
              <button type="submit" className="button button--primary">
                <Search aria-hidden="true" className="button-icon" />
                Filter
              </button>
            </form>
          ) : null}
          {invalid ? <p role="alert">Choose a valid date interval and workflow ID.</p> : null}
          <ArchivePage
            key={`${JSON.stringify(query)}:${gapRefresh}`}
            query={query}
            inspection={inspection}
          >
            {(page) =>
              query.kind === "gaps" ? (
                <ArchiveGaps
                  key={`${page.configurationRevision}:${page.dataRevision}:${page.gaps[0]?.gapId ?? "empty"}`}
                  page={page}
                  disabled={gapRepair !== null || session?.roles.includes("configure") !== true}
                  onRetry={(ids) => {
                    if (gapRepair !== null) return;
                    const intervals = selectedRepairIntervals(page, ids);
                    if (intervals === null) return;
                    setGapRepair({
                      request: {
                        installationId: page.query.installationId,
                        repositoryId: page.query.repositoryId,
                        generation: page.query.generation,
                        expectedRevision: page.configurationRevision,
                        gapIds: [...ids].sort(),
                        operationId: crypto.randomUUID(),
                      },
                      intervals,
                    });
                  }}
                />
              ) : (
                <ScrollableRegion className="archive-table-scroll" label="Retained run table">
                  <table className="archive-table">
                    <caption>Retained runs, oldest first</caption>
                    <thead>
                      <tr>
                        <th>Run</th>
                        <th>Workflow</th>
                        <th>Created</th>
                        <th>Result</th>
                        <th>Jobs</th>
                        <th>Evidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {page.records.map((record) => (
                        <tr
                          key={`${record.header.attempt.workflowRunId}:${record.header.attempt.runAttempt}`}
                        >
                          <td>
                            <button
                              type="button"
                              className="button button--secondary"
                              onClick={() => setSelected(record)}
                            >
                              <List aria-hidden="true" className="button-icon" />#
                              {record.header.attempt.workflowRunId} /{" "}
                              {record.header.attempt.runAttempt}
                            </button>
                          </td>
                          <td>
                            <span>
                              {record.header.workflowPath ??
                                `Workflow #${record.header.workflowId}`}
                            </span>
                            <small>{record.header.event}</small>
                          </td>
                          <td>
                            <time dateTime={record.header.runCreatedAt}>
                              {formatDateTime(record.header.runCreatedAt)}
                            </time>
                          </td>
                          <td>{record.header.conclusion ?? "Unknown"}</td>
                          <td>{record.jobCount}</td>
                          <td>
                            {record.hasConflict ? "Conflicting evidence" : record.header.population}
                          </td>
                        </tr>
                      ))}
                      {page.records.length === 0 ? (
                        <tr>
                          <td colSpan={6}>No retained runs match this selection.</td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </ScrollableRegion>
              )
            }
          </ArchivePage>
        </>
      ) : null}
      {retentionPage !== null ? (
        <ArchiveRetention
          page={retentionPage}
          defaultRevision={status.defaults.revision}
          session={session}
          onChanged={onChanged}
          onClose={() => setRetentionPage(null)}
        />
      ) : null}
      {gapRepair !== null ? (
        <ArchiveGapRepair
          selection={gapRepair}
          session={session}
          onChanged={() => {
            setGapRefresh((value) => value + 1);
            onChanged();
          }}
          onClose={() => setGapRepair(null)}
        />
      ) : null}
    </>
  );
}

function ArchivePage({
  query,
  children,
  inspection,
}: {
  readonly query: ArchiveQuery;
  readonly children: (page: ArchiveRead) => React.ReactNode;
  readonly inspection?: ArchiveInspection | undefined;
}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const read = useCallback(
    async (signal: AbortSignal) => {
      const result = await fetchArchivePage(query, cursor, signal);
      if (
        inspection &&
        result.kind === "ready" &&
        (result.value.dataRevision !== inspection.dataRevision ||
          result.value.configurationRevision !== inspection.configurationRevision)
      )
        return {
          kind: "unavailable" as const,
          reason: "archive_changed_since_analytics_refresh_the_analysis",
        };
      return result;
    },
    [query, cursor, inspection],
  );
  const { state, refresh } = useEconomicsRead(read);
  function restart() {
    setCursor(null);
    setPageNumber(1);
    refresh();
  }
  return (
    <>
      <div className="economics-subheading">
        <p>Retained local records. Completeness of GitHub history is not established.</p>
        <EconomicsRefresh onClick={restart} />
      </div>
      <EconomicsRead state={state} onRetry={restart}>
        {(page) => (
          <>
            {children(page)}
            <EconomicsPagination
              page={pageNumber}
              next={page.nextCursor}
              onNext={(next) => {
                setCursor(next);
                setPageNumber((value) => value + 1);
              }}
              onFirst={restart}
            />
          </>
        )}
      </EconomicsRead>
    </>
  );
}

function ArchiveAttempt({
  query,
  record,
  onRetention,
  retentionOpen,
  inspection,
}: {
  readonly query: ArchiveQuery;
  readonly record: ArchiveRecord;
  readonly onRetention: (page: ArchiveRead) => void;
  readonly retentionOpen: boolean;
  readonly inspection: ArchiveInspection | undefined;
}) {
  const attempt = record.header.attempt;
  const [jobsQuery] = useState(() =>
    archiveQuerySchema.parse({
      installationId: query.installationId,
      repositoryId: query.repositoryId,
      generation: query.generation,
      kind: "jobs",
      workflowRunId: attempt.workflowRunId,
      runAttempt: attempt.runAttempt,
      jobName: query.jobName,
    }),
  );
  return (
    <section aria-label="Retained attempt">
      <h3>
        Run #{attempt.workflowRunId}, attempt {attempt.runAttempt}
      </h3>
      <p>
        {record.header.workflowPath ?? `Workflow #${record.header.workflowId}`} /{" "}
        {record.header.event}
      </p>
      <p>
        Workflow version: <code>{record.header.workflowBlobSha ?? "Unknown"}</code>. Current
        availability in GitHub has not been checked.
      </p>
      <ArchivePage query={jobsQuery} inspection={inspection}>
        {(page) => (
          <>
            <ScrollableRegion className="archive-table-scroll" label="Retained job table">
              <table className="archive-table">
                <caption>Retained job observations</caption>
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Result</th>
                    <th>Started</th>
                    <th>Completed</th>
                    <th>Runner</th>
                  </tr>
                </thead>
                <tbody>
                  {page.jobs.map((job) => (
                    <tr key={job.providerJobId}>
                      <td>
                        {job.name}
                        <small>#{job.providerJobId}</small>
                      </td>
                      <td>{job.conclusion}</td>
                      <td>{job.startedAt === null ? "Unknown" : formatDateTime(job.startedAt)}</td>
                      <td>
                        {job.completedAt === null ? "Unknown" : formatDateTime(job.completedAt)}
                      </td>
                      <td>
                        {job.runnerId === null ? "Unknown" : `#${job.runnerId}`}
                        <small>{job.labels.join(", ")}</small>
                      </td>
                    </tr>
                  ))}
                  {page.jobs.length === 0 ? (
                    <tr>
                      <td colSpan={5}>No retained jobs match this attempt.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </ScrollableRegion>
            {inspection === undefined ? (
              <button
                type="button"
                className="button button--secondary"
                disabled={retentionOpen}
                onClick={() => onRetention(page)}
              >
                Manage detail retention
              </button>
            ) : null}
          </>
        )}
      </ArchivePage>
      <ArchiveDetail query={query} record={record} inspection={inspection} />
    </section>
  );
}

function ArchiveDetail({
  query,
  record,
  inspection,
}: {
  readonly query: ArchiveQuery;
  readonly record: ArchiveRecord;
  readonly inspection: ArchiveInspection | undefined;
}) {
  const [detailQuery] = useState(() =>
    archiveDetailQuerySchema.parse({
      installationId: query.installationId,
      repositoryId: query.repositoryId,
      generation: query.generation,
      kind: "detail",
      limit: 1,
      createdFrom: null,
      createdThrough: null,
      workflowId: null,
      workflowRunId: record.header.attempt.workflowRunId,
      runAttempt: record.header.attempt.runAttempt,
      jobName: null,
    }),
  );
  const read = useCallback(
    async (signal: AbortSignal) => {
      const result = await fetchArchiveDetail(detailQuery, signal);
      if (
        inspection &&
        result.kind === "ready" &&
        (result.value.dataRevision !== inspection.dataRevision ||
          result.value.configurationRevision !== inspection.configurationRevision)
      )
        return {
          kind: "unavailable" as const,
          reason: "archive_changed_since_analytics_refresh_the_analysis",
        };
      return result;
    },
    [detailQuery, inspection],
  );
  const { state, refresh } = useEconomicsRead(read);
  return (
    <section className="archive-detail" aria-label="Numbered step details">
      <h4>Numbered step details</h4>
      <EconomicsRead state={state} onRetry={refresh}>
        {(page) => <ArchiveDetailContent page={page.detailPayload} retention={page.detail} />}
      </EconomicsRead>
    </section>
  );
}

function ArchiveDetailContent({
  page,
  retention,
}: {
  readonly page: ArchiveAttemptDetail | null;
  readonly retention: ArchiveRecord["detail"];
}) {
  return (
    <>
      <p>
        Retention: {retentionLabel(retention)}
        {retention.expiresAt === null ? "" : ` through ${formatDateTime(retention.expiresAt)}`}
      </p>
      {page === null ? (
        <p>
          {retention.state === "not_imported"
            ? "Numbered steps were not imported for this attempt."
            : retention.state === "expired"
              ? "Numbered steps expired; retained statistics remain available."
              : "Numbered steps are retained but unavailable in a recognized format."}
        </p>
      ) : (
        <div>
          {page.jobs.map((job) => (
            <section key={job.providerJobId} aria-label={`Job #${job.providerJobId} steps`}>
              <h5>Job #{job.providerJobId}</h5>
              <ol>
                {job.steps.map((step) => (
                  <li key={step.number}>
                    Step {step.number}: {step.conclusion ?? "No conclusion"}; started{" "}
                    {step.startedAt === null ? "Unknown" : formatDateTime(step.startedAt)}
                    {"; completed "}
                    {step.completedAt === null ? "Unknown" : formatDateTime(step.completedAt)}
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </div>
      )}
    </>
  );
}

function retentionLabel(detail: ArchiveRecord["detail"]): string {
  if (detail.state === "not_imported") return "not imported";
  if (detail.state === "expired") return "expired";
  return "retained";
}
