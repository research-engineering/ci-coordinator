import { ArrowLeft } from "lucide-react";
import type { AnalyticsReport } from "../../api/ciEconomics/analyticsSchema";
import { archiveQuerySchema } from "../../api/ciEconomics/archiveReadSchema";
import type { HistoryStatus } from "../../api/ciEconomics/historyStatusSchema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { ArchiveBrowser } from "./ArchiveBrowser";

export function AnalyticsSources({
  scope,
  history,
  report,
  day,
  onBack,
}: {
  readonly scope: WorkbenchScope;
  readonly history: HistoryStatus;
  readonly report: AnalyticsReport;
  readonly day: string | undefined;
  readonly onBack: () => void;
}) {
  const validDay =
    day === undefined ||
    report.buckets.some((bucket) => Date.parse(bucket.day) === Date.parse(day));
  const from = validDay ? (day ?? report.query.createdFrom) : report.query.createdFrom;
  const until =
    day === undefined || !validDay
      ? report.query.createdUntil
      : new Date(Date.parse(day) + 86400000).toISOString();
  const query = archiveQuerySchema.safeParse({
    installationId: report.query.installationId,
    repositoryId: report.query.repositoryId,
    generation: report.query.generation,
    kind: "records",
    createdFrom: from,
    createdThrough: new Date(Date.parse(until) - 1000)
      .toISOString()
      .replace(/\.\d{3}Z$/, ".999999Z"),
    workflowId: report.query.workflowId,
    jobName: report.query.jobName,
  });
  return (
    <section aria-label="Analytics source runs">
      <button type="button" className="button button--secondary" onClick={onBack}>
        <ArrowLeft aria-hidden="true" className="button-icon" />
        Back to analytics
      </button>
      <h3>Source runs</h3>
      <p>
        {from.slice(0, 10)} to {until.slice(0, 10)} (exclusive UTC).{" "}
        {report.query.jobName === null ? "All selected jobs" : `Job: ${report.query.jobName}`}.
        Dataset revision {report.dataRevision}.
      </p>
      {report.query.purpose !== null ? (
        <p>
          Statistics category: {report.query.purpose}. Source runs retain workflow and job filters,
          but may also contain jobs outside this category.
        </p>
      ) : null}
      {query.success && validDay ? (
        <ArchiveBrowser
          scope={scope}
          status={history}
          session={undefined}
          onChanged={() => {}}
          inspection={{
            query: query.data,
            configurationRevision: report.configurationRevision,
            dataRevision: report.dataRevision,
          }}
        />
      ) : (
        <p role="alert">The source selection could not be validated. Refresh analytics.</p>
      )}
    </section>
  );
}
