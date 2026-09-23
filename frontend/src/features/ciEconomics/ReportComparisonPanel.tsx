import { useCallback } from "react";
import { compareMeasurementReports } from "../../api/ciEconomics/measurementClient";
import type { RetainedReport } from "../../api/ciEconomics/measurementSchema";
import { formatInteger } from "../../domain/format";
import { EconomicsRead } from "./EconomicsControls";
import { useEconomicsRead } from "./useEconomicsRead";

export function ReportComparisonPanel({
  baseline,
  treatment,
}: {
  readonly baseline: RetainedReport;
  readonly treatment: RetainedReport;
}) {
  const read = useCallback(
    (signal: AbortSignal) => compareMeasurementReports(baseline, treatment, signal),
    [baseline, treatment],
  );
  const query = useEconomicsRead(read);
  return (
    <div className="economics-subsection">
      <dl className="economics-comparison-identities">
        {(
          [
            ["Baseline", baseline],
            ["Treatment", treatment],
          ] as const
        ).map(([label, report]) => {
          return (
            <div key={label}>
              <dt>{label}</dt>
              <dd>
                Run {report.payload.attempt.workflowRunId}, attempt{" "}
                {report.payload.attempt.runAttempt}
                <span>
                  {report.payload.sampleKey} / job {report.payload.providerJobId}
                </span>
                <code>{report.reportId.slice(0, 12)}</code>
              </dd>
            </div>
          );
        })}
      </dl>
      <EconomicsRead state={query.state} onRetry={query.refresh}>
        {(value) => (
          <>
            <p className="economics-provenance">
              Coverage: not verified / Causal effect: not established
            </p>
            {value.mismatches.length > 0 ? (
              <div role="status">
                <strong>Reports are not comparable</strong>
                <ul>
                  {value.mismatches.map((reason) => (
                    <li key={reason}>{reason.replaceAll("_", " ")}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <dl className="economics-metrics">
                {value.differences.map((difference) => (
                  <div key={difference.counter}>
                    <dt>{difference.counter.replaceAll("_", " ")}</dt>
                    <dd>
                      {difference.reduction === null
                        ? "Unavailable"
                        : `${formatInteger(difference.reduction)} us reduction`}
                      <span>{difference.scope.replaceAll("_", " ")}</span>
                      <small>
                        {difference.relativeReduction === null
                          ? "Relative reduction unavailable"
                          : `${difference.relativeReduction.numerator} / ${difference.relativeReduction.denominator} relative reduction`}
                      </small>
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </>
        )}
      </EconomicsRead>
    </div>
  );
}
