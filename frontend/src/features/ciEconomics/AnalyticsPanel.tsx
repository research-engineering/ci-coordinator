import { List, Search } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { fetchHistoryAnalytics } from "../../api/ciEconomics/analyticsClient";
import {
  type AnalyticsQuery,
  type AnalyticsReport,
  analyticsQuerySchema,
} from "../../api/ciEconomics/analyticsSchema";
import type { HistoryStatus } from "../../api/ciEconomics/historyStatusSchema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { formatDateTime } from "../../domain/format";
import { AnalyticsSources } from "./AnalyticsSources";
import { EconomicsError, EconomicsRead, EconomicsRefresh } from "./EconomicsControls";
import { ForecastChart } from "./ForecastChart";
import { HistoricalMetrics } from "./HistoricalMetrics";
import { PurposeSettingsPanel } from "./PurposeSettingsPanel";
import { useEconomicsRead } from "./useEconomicsRead";
import { useHistoryStatus } from "./useHistoryStatus";

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const dayMs = 86400000;
const reasons = {
  dataset_unavailable: "Enable Actions history for this repository first.",
  generation_changed: "The archive generation changed. Refresh the page.",
  query_budget_exceeded:
    "This selection exceeds the query budget. Choose a shorter period or one workflow.",
  snapshot_changed: "The archive changed during this read. Refresh to use the new snapshot.",
  purpose_mapping_unavailable: "This repository has no configured mapping for that job category.",
  future_window: "The selected period includes an unfinished UTC day.",
};

export function AnalyticsPanel({
  scope,
  onHistory,
  session,
  active = true,
}: {
  readonly scope: WorkbenchScope;
  readonly onHistory: () => void;
  readonly session?: ControlPlaneSession | undefined;
  readonly active?: boolean;
}) {
  const status = useHistoryStatus(scope, active);
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  const value = status.state.value;
  return (
    <section className="economics-subsection" aria-label="Historical CI analytics">
      <div className="economics-subheading">
        <h2>Usage and performance</h2>
        <EconomicsRefresh
          onClick={() => {
            status.refresh();
            setRefreshEpoch((value) => value + 1);
          }}
        />
      </div>
      <div className="analytics-content">
        {status.state.failure ? (
          <EconomicsError failure={status.state.failure} onRetry={status.refresh} />
        ) : null}
        {value ? (
          value.snapshot === null ? (
            <div className="economics-message">
              <strong>No archive configured</strong>
              <button type="button" className="button button--secondary" onClick={onHistory}>
                Configure history
              </button>
            </div>
          ) : (
            <ScopedAnalytics
              key={value.snapshot.generation}
              scope={scope}
              generation={value.snapshot.generation}
              history={value}
              session={session}
              active={active && !status.state.failure}
              refreshEpoch={refreshEpoch}
            />
          )
        ) : (
          <p role="status">Loading history status</p>
        )}
      </div>
    </section>
  );
}

function ScopedAnalytics({
  scope,
  generation,
  history,
  session,
  active,
  refreshEpoch,
}: {
  readonly scope: WorkbenchScope;
  readonly generation: number;
  readonly history: HistoryStatus;
  readonly session: ControlPlaneSession | undefined;
  readonly active: boolean;
  readonly refreshEpoch: number;
}) {
  const settingsQuery = useMemo(
    () => ({ installationId: scope.installationId, repositoryId: scope.repositoryId, generation }),
    [scope.installationId, scope.repositoryId, generation],
  );
  const [until] = useState(() => new Date().toISOString().slice(0, 10));
  const [from] = useState(() =>
    new Date(Date.parse(until) - 90 * dayMs).toISOString().slice(0, 10),
  );
  const [query, setQuery] = useState<AnalyticsQuery>(() =>
    analyticsQuerySchema.parse({
      installationId: scope.installationId,
      repositoryId: scope.repositoryId,
      generation,
      createdFrom: `${from}T00:00:00Z`,
      createdUntil: `${until}T00:00:00Z`,
    }),
  );
  const [invalid, setInvalid] = useState(false);
  const [inspection, setInspection] = useState<{
    readonly report: AnalyticsReport;
    readonly day: string | undefined;
  } | null>(null);
  const read = useCallback((signal: AbortSignal) => fetchHistoryAnalytics(query, signal), [query]);
  const { state, refresh } = useEconomicsRead(read, refreshEpoch);
  const settings = (
    <PurposeSettingsPanel
      query={settingsQuery}
      session={session}
      active={active}
      onSaved={refresh}
    />
  );
  if (inspection !== null)
    return (
      <>
        {settings}
        <AnalyticsSources
          scope={scope}
          history={history}
          report={inspection.report}
          day={inspection.day}
          onBack={() => {
            setInspection(null);
            refresh();
          }}
        />
      </>
    );
  return (
    <>
      {settings}
      <form
        className="analytics-filters"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const workflow = String(data.get("workflow") ?? "").trim();
          const job = String(data.get("job") ?? "");
          const parsed = analyticsQuerySchema.safeParse({
            ...query,
            createdFrom: `${data.get("from")}T00:00:00Z`,
            createdUntil: `${data.get("until")}T00:00:00Z`,
            workflowId: workflow ? Number(workflow) : null,
            jobName: job || null,
            horizonDays: Number(data.get("horizon")),
            purpose: data.get("purpose") || null,
          });
          setInvalid(!parsed.success);
          if (parsed.success) setQuery(parsed.data);
        }}
      >
        <label>
          From (UTC)
          <input type="date" name="from" defaultValue={query.createdFrom.slice(0, 10)} required />
        </label>
        <label>
          Until, exclusive (UTC)
          <input
            type="date"
            name="until"
            defaultValue={query.createdUntil.slice(0, 10)}
            max={until}
            required
          />
        </label>
        <label>
          Workflow ID
          <input
            type="number"
            name="workflow"
            min="1"
            step="1"
            placeholder="All workflows"
            defaultValue={query.workflowId ?? ""}
          />
        </label>
        <label>
          Exact job name
          <input
            type="text"
            name="job"
            maxLength={1024}
            placeholder="All jobs"
            defaultValue={query.jobName ?? ""}
          />
        </label>
        <label>
          Job category
          <select name="purpose" defaultValue={query.purpose ?? ""}>
            <option value="">All categories</option>
            {["lint", "typecheck", "test", "build", "deploy", "mixed", "unknown"].map((purpose) => (
              <option key={purpose} value={purpose}>
                {purpose}
              </option>
            ))}
          </select>
        </label>
        <label>
          Forecast horizon
          <select name="horizon" defaultValue={String(query.horizonDays)}>
            <option value="7">7 days</option>
            <option value="14">14 days</option>
            <option value="30">30 days</option>
          </select>
        </label>
        <button type="submit" className="button button--primary">
          <Search className="button-icon" aria-hidden="true" />
          Apply
        </button>
      </form>
      {invalid ? (
        <p role="alert">
          Choose a valid period of up to 366 complete UTC days and a positive workflow ID.
        </p>
      ) : null}
      <EconomicsRead state={state} onRetry={refresh}>
        {(value) =>
          value.outcome === "available" ? (
            <AnalyticsEvidence
              report={value.report}
              onInspect={(day) => setInspection({ report: value.report, day })}
            />
          ) : (
            <p role="status">{reasons[value.unavailable.reason]}</p>
          )
        }
      </EconomicsRead>
    </>
  );
}

function AnalyticsEvidence({
  report,
  onInspect,
}: {
  readonly report: AnalyticsReport;
  readonly onInspect: (day?: string) => void;
}) {
  const totals = report.buckets.reduce(
    (sum, bucket) => ({
      jobs: sum.jobs + bucket.selected.jobs,
      failures: sum.failures + bucket.selected.failures,
      cancelled: sum.cancelled + bucket.selected.cancellations,
      samples: sum.samples + bucket.selected.durationSamples,
      missing: sum.missing + bucket.selected.missingDuration,
      inconsistent: sum.inconsistent + bucket.selected.inconsistentTimings,
      conflicts: sum.conflicts + bucket.conflictExcludedJobs,
      runnerMs: sum.runnerMs + (bucket.observedRunnerMs ?? 0),
      incomplete:
        sum.incomplete +
        bucket.partialAttempts +
        bucket.unavailableAttempts +
        bucket.conflictAttempts,
    }),
    {
      jobs: 0,
      failures: 0,
      cancelled: 0,
      samples: 0,
      runnerMs: 0,
      incomplete: 0,
      missing: 0,
      inconsistent: 0,
      conflicts: 0,
    },
  );
  const forecast = report.forecast;
  const unavailable = {
    incomplete_daily_coverage: "Daily measurements are incomplete; no forecast is shown.",
    insufficient_backtest: "More historical observations are needed for out-of-sample validation.",
    poor_calibration: "Historical prediction errors are too large for a supported forecast.",
    incompatible_cohort: "The selected workload changed; a comparable forecast is unavailable.",
    conditional_on_unchanged_collection_and_workload:
      "Conditional on unchanged collection and workload.",
  };
  const slowdown = report.degradation;
  return (
    <>
      <button type="button" className="button button--secondary" onClick={() => onInspect()}>
        <List className="button-icon" aria-hidden="true" />
        Inspect source runs
      </button>
      <p className="analytics-provenance">
        Retained attempts by run-creation day. Provider history completeness is unknown. Updated{" "}
        {formatDateTime(report.observedAt)}.
      </p>
      <dl className="analytics-totals">
        {[
          ["Runs", number.format(report.runs)],
          ["Attempts", number.format(report.attempts)],
          [
            "Measured runner-minutes",
            totals.samples ? number.format(totals.runnerMs / 60000) : "Not measured",
          ],
          ["Failed jobs", number.format(totals.failures)],
          ["Cancelled jobs", number.format(totals.cancelled)],
        ].map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <p className="analytics-provenance">
        Duration measured for {number.format(totals.samples)} / {number.format(totals.jobs)}{" "}
        selected jobs. Missing: {number.format(totals.missing)}; inconsistent:{" "}
        {number.format(totals.inconsistent)}; excluded conflicting jobs:{" "}
        {number.format(totals.conflicts)}.
      </p>
      <p className="analytics-provenance">
        Unknown category:{" "}
        {number.format(
          report.buckets.reduce((sum, bucket) => sum + bucket.selected.unknownPurposeJobs, 0),
        )}
        . Mixed category:{" "}
        {number.format(report.buckets.reduce((sum, bucket) => sum + bucket.selected.mixedJobs, 0))}.
        Mapping: {report.mapping?.version ?? "not configured"}.
      </p>
      {totals.incomplete > 0 ? (
        <p role="status">
          {number.format(totals.incomplete)} attempts have incomplete or conflicting job evidence.
          Totals include only admitted observations.
        </p>
      ) : null}
      <HistoricalMetrics report={report} onInspect={onInspect} />
      <ForecastChart report={report} />
      <div className="analytics-comparison">
        <section aria-label="Usage forecast">
          <h3>Next {forecast.horizonDays} days</h3>
          {forecast.status === "available" &&
          forecast.predictedRunnerMs !== null &&
          forecast.lowerRunnerMs !== null &&
          forecast.upperRunnerMs !== null ? (
            <>
              <p className="forecast-value">
                {number.format(forecast.predictedRunnerMs / 60000)} <span>runner-minutes</span>
              </p>
              <p>
                Empirical interval: {number.format(forecast.lowerRunnerMs / 60000)}-
                {number.format(forecast.upperRunnerMs / 60000)} runner-minutes.
              </p>
              <p>{unavailable[forecast.reason]}</p>
            </>
          ) : (
            <p>{unavailable[forecast.reason]}</p>
          )}
          <p>
            No monetary tariff or measured CPU usage is available. Runner occupancy is not realized
            financial savings.
          </p>
          {forecast.folds.length ? (
            <details>
              <summary>Forecast validation</summary>
              <p>
                {forecast.folds.filter((fold) => fold.phase === "evaluation").length} chronological
                evaluation folds. Error:{" "}
                {forecast.backtestMaeMs === null
                  ? "unavailable"
                  : `${number.format(forecast.backtestMaeMs / 60000)} runner-minutes`}
                . Empirical interval coverage:{" "}
                {forecast.empiricalCoverageBps === null
                  ? "unavailable"
                  : `${number.format(forecast.empiricalCoverageBps / 100)}%`}
                .
              </p>
            </details>
          ) : null}
        </section>
        <section aria-label="Job duration signal">
          <h3>Job duration</h3>
          <p>
            <strong>{slowdown.status.replaceAll("_", " ")}</strong>
          </p>
          {slowdown.status === "unavailable" ? (
            <p>
              {report.query.jobName === null
                ? "Select an exact workflow and job to inspect duration changes."
                : slowdown.reason.replaceAll("_", " ")}
            </p>
          ) : (
            <p>Observed duration change only. Its cause is unclassified.</p>
          )}
          {slowdown.events.length ? (
            <ul>
              {slowdown.events.slice(-8).map((event) => (
                <li key={event.key}>
                  {event.day.slice(0, 10)}: {event.state.replaceAll("_", " ")} (
                  {number.format(event.meanDurationMs / 1000)} s)
                  <button
                    type="button"
                    className="icon-button"
                    title={`Inspect source runs for ${event.day.slice(0, 10)}`}
                    aria-label={`Inspect source runs for ${event.day.slice(0, 10)}`}
                    onClick={() => onInspect(event.day)}
                  >
                    <List aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      </div>
    </>
  );
}
