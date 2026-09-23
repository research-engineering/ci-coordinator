import { ArrowLeft, ArrowRight } from "lucide-react";
import { useCallback, useState } from "react";
import { fetchEconomicsReports } from "../../api/ciEconomics/catalogClient";
import type { ReportPointer, SourceItem } from "../../api/ciEconomics/catalogSchema";
import { fetchAttemptMeasurements } from "../../api/ciEconomics/measurementClient";
import type { RetainedReport } from "../../api/ciEconomics/measurementSchema";
import { IdentityDisclosure } from "../../components/IdentityDisclosure";
import { formatDateTime } from "../../domain/format";
import { DurationMetric } from "../workbench/CiEconomicsAttemptEvidence";
import { EconomicsPagination, EconomicsRead } from "./EconomicsControls";
import { MeasurementReportDetail } from "./MeasurementReportDetail";
import { useEconomicsRead } from "./useEconomicsRead";

export function SourceEvidence({
  item,
  onBaseline,
  onTreatment,
}: {
  readonly item: SourceItem;
  readonly onBaseline: (report: RetainedReport) => void;
  readonly onTreatment: (report: RetainedReport) => void;
}) {
  const source = item.source;
  const [page, setPage] = useState({ number: 1, cursor: null as string | null });
  const [selected, setSelected] = useState<ReportPointer>();
  const measurementRead = useCallback(
    (signal: AbortSignal) => fetchAttemptMeasurements(source, signal),
    [source],
  );
  const reportsRead = useCallback(
    (signal: AbortSignal) => fetchEconomicsReports(source, page.cursor, signal),
    [source, page.cursor],
  );
  const measurements = useEconomicsRead(measurementRead);
  const reports = useEconomicsRead(reportsRead);
  if (selected)
    return (
      <>
        <nav className="economics-navigation" aria-label="Report navigation">
          <button
            type="button"
            className="button button--compact"
            onClick={() => setSelected(undefined)}
          >
            <ArrowLeft className="button-icon" aria-hidden="true" />
            Run evidence
          </button>
        </nav>
        <MeasurementReportDetail
          key={selected.reportId}
          source={source}
          pointer={selected}
          onBaseline={onBaseline}
          onTreatment={onTreatment}
        />
      </>
    );
  return (
    <div className="economics-subsection">
      <div className="economics-provenance">
        <span>
          Attempt {source.attempt.runAttempt} / {item.status.replaceAll("_", " ")}
        </span>
        <span>
          Collection attempts {item.attemptCount}/{item.maxAttempts}
        </span>
        <span>Retained until {formatDateTime(item.retainUntil)}</span>
        <IdentityDisclosure value={source.sourceId} />
      </div>
      {item.lastFailureReason ? (
        <p role="status">
          Previous collection failure: {item.lastFailureReason.replaceAll("_", " ")}
        </p>
      ) : null}
      {item.terminalReason ? (
        <p role="status">Collection ended: {item.terminalReason.replaceAll("_", " ")}</p>
      ) : null}
      <h3>Provider durations</h3>
      <EconomicsRead state={measurements.state} onRetry={measurements.refresh}>
        {(value) => (
          <>
            <dl className="economics-metrics">
              <DurationMetric label="Queue" value={value.queue} />
              <DurationMetric label="Runner occupancy" value={value.runnerOccupancy} />
              <DurationMetric label="Attempt wall" value={value.attemptWall} />
            </dl>
            <div className="economics-provenance">
              <span>Source: {value.source.sourceKind.replaceAll("_", " ")}</span>
              <span>Recorded {formatDateTime(value.recordedAt)}</span>
            </div>
          </>
        )}
      </EconomicsRead>
      <h3>Measurement reports</h3>
      <EconomicsRead state={reports.state} onRetry={reports.refresh}>
        {(value) => (
          <>
            <ul className="economics-source-list">
              {value.items.map((pointer) => (
                <li key={pointer.reportId}>
                  <div>
                    <strong>Report {pointer.reportId.slice(0, 10)}</strong>
                    <time dateTime={pointer.receivedAt}>{formatDateTime(pointer.receivedAt)}</time>
                  </div>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Inspect report ${pointer.reportId.slice(0, 10)}`}
                    title="Inspect report"
                    onClick={() => setSelected(pointer)}
                  >
                    <ArrowRight aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
            {value.items.length === 0 ? <p>No retained reports for this run.</p> : null}
            <EconomicsPagination
              page={page.number}
              next={value.nextCursor}
              onFirst={() => setPage({ number: 1, cursor: null })}
              onNext={(cursor) => setPage((old) => ({ number: old.number + 1, cursor }))}
            />
          </>
        )}
      </EconomicsRead>
    </div>
  );
}
