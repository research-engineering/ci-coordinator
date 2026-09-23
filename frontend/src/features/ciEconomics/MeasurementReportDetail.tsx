import { useCallback, useState } from "react";
import type { ReportPointer } from "../../api/ciEconomics/catalogSchema";
import { fetchMeasurementReport } from "../../api/ciEconomics/measurementClient";
import type { ReportMeasurement, RetainedReport } from "../../api/ciEconomics/measurementSchema";
import type { ProviderSource } from "../../api/ciEconomics/sourceSchema";
import { IdentityDisclosure } from "../../components/IdentityDisclosure";
import { formatDateTime, formatInteger } from "../../domain/format";
import { EconomicsRead } from "./EconomicsControls";
import { ReportBudgetControl } from "./ReportBudgetControl";
import { useEconomicsRead } from "./useEconomicsRead";

export function MeasurementReportDetail({
  source,
  pointer,
  onBaseline,
  onTreatment,
}: {
  readonly source: ProviderSource;
  readonly pointer: ReportPointer;
  readonly onBaseline: (report: RetainedReport) => void;
  readonly onTreatment: (report: RetainedReport) => void;
}) {
  const [selectedRole, setSelectedRole] = useState<string>();
  const read = useCallback(
    (signal: AbortSignal) => fetchMeasurementReport(source, pointer, signal),
    [source, pointer],
  );
  const query = useEconomicsRead(read);
  return (
    <EconomicsRead state={query.state} onRetry={query.refresh}>
      {(report) => (
        <div className="economics-subsection">
          <h3>{report.payload.sampleKey}</h3>
          <div className="economics-provenance">
            <span>Job {report.payload.providerJobId}</span>
            <span>Exit code {report.payload.commandExitCode}</span>
            <span>Received {formatDateTime(report.receivedAt)}</span>
            <IdentityDisclosure value={report.reportId} />
          </div>
          <dl className="economics-metrics">
            {report.payload.measurements.map((measurement) => (
              <CounterMetric key={measurement.counter} measurement={measurement} />
            ))}
          </dl>
          <div className="economics-actions">
            <button
              type="button"
              className="button button--compact"
              onClick={() => {
                onBaseline(report);
                setSelectedRole("Baseline selected");
              }}
            >
              Use as baseline
            </button>
            <button
              type="button"
              className="button button--compact"
              onClick={() => {
                onTreatment(report);
                setSelectedRole("Treatment selected");
              }}
            >
              Use as treatment
            </button>
            {selectedRole ? <span role="status">{selectedRole}</span> : null}
          </div>
          <ReportBudgetControl report={report} />
        </div>
      )}
    </EconomicsRead>
  );
}

export function CounterMetric({ measurement }: { readonly measurement: ReportMeasurement }) {
  return (
    <div>
      <dt>{COUNTER_LABELS[measurement.counter]}</dt>
      <dd>
        {measurement.value === null ? "Unavailable" : `${formatInteger(measurement.value)} us`}
        <span>{measurement.scope.replaceAll("_", " ")}</span>
        {measurement.unavailableReason ? (
          <small>{measurement.unavailableReason.replaceAll("_", " ")}</small>
        ) : null}
      </dd>
    </div>
  );
}

export const COUNTER_LABELS = {
  cpu_user: "CPU user",
  cpu_system: "CPU system",
  elapsed: "Reporter elapsed",
};
