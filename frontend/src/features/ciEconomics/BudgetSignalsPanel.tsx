import { useCallback, useState } from "react";
import { fetchBudgetSignals } from "../../api/ciEconomics/budgetClient";
import type { BudgetSignal } from "../../api/ciEconomics/budgetSchema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { IdentityDisclosure } from "../../components/IdentityDisclosure";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateTime, formatInteger } from "../../domain/format";
import { EconomicsPagination, EconomicsRead, EconomicsRefresh } from "./EconomicsControls";
import { COUNTER_LABELS } from "./MeasurementReportDetail";
import { useEconomicsRead } from "./useEconomicsRead";

export function BudgetSignalsPanel({ scope }: { readonly scope: WorkbenchScope }) {
  const [outcome, setOutcome] = useState<BudgetSignal["outcome"] | "all">("all");
  const [page, setPage] = useState({ number: 1, cursor: undefined as string | undefined });
  const read = useCallback(
    (signal: AbortSignal) =>
      fetchBudgetSignals(
        scope,
        {
          afterCursor: page.cursor,
          outcome: outcome === "all" ? undefined : outcome,
        },
        signal,
      ),
    [scope, outcome, page.cursor],
  );
  const query = useEconomicsRead(read);
  return (
    <>
      <div className="economics-subheading">
        <h2>Budget signals</h2>
        <EconomicsRefresh
          onClick={() => {
            setPage({ number: 1, cursor: undefined });
            query.refresh();
          }}
        />
      </div>
      <label className="economics-signal-filter">
        Outcome
        <select
          value={outcome}
          onChange={(event) => {
            setOutcome(event.target.value as typeof outcome);
            setPage({ number: 1, cursor: undefined });
          }}
        >
          <option value="all">All outcomes</option>
          <option value="breached">Breached</option>
          <option value="within_budget">Within budget</option>
          <option value="insufficient_evidence">Insufficient evidence</option>
        </select>
      </label>
      <EconomicsRead state={query.state} onRetry={query.refresh}>
        {(result) => (
          <>
            {result.items.length === 0 ? (
              <p className="economics-message">No retained signals match this view.</p>
            ) : null}
            <ul className="economics-signal-list">
              {result.items.map((signal) => (
                <li key={signal.signalId}>
                  <div className="economics-subheading">
                    <strong>
                      {signal.policy.policyKey} / revision {signal.policy.revision}
                    </strong>
                    <StatusBadge
                      tone={
                        signal.outcome === "breached"
                          ? "warning"
                          : signal.outcome === "within_budget"
                            ? "positive"
                            : "info"
                      }
                    >
                      {signal.outcome.replaceAll("_", " ")}
                    </StatusBadge>
                  </div>
                  <div className="economics-provenance">
                    <span>
                      {COUNTER_LABELS[signal.measurement.counter]}:{" "}
                      {signal.measurement.value === null
                        ? "Unavailable"
                        : `${formatInteger(signal.measurement.value)} us`}
                    </span>
                    <span>Budget {formatInteger(signal.policy.configuration.maximumUs)} us</span>
                    <span>Command exit {signal.commandExitCode}</span>
                    <time dateTime={signal.receivedAt}>{formatDateTime(signal.receivedAt)}</time>
                  </div>
                  {signal.measurement.unavailableReason ? (
                    <p>{signal.measurement.unavailableReason.replaceAll("_", " ")}</p>
                  ) : null}
                  <details>
                    <summary>Evidence identity</summary>
                    <dl className="economics-signal-identity">
                      <dt>Report</dt>
                      <dd>
                        <IdentityDisclosure value={signal.reportId} />
                      </dd>
                      <dt>Source</dt>
                      <dd>
                        <IdentityDisclosure value={signal.sourceId} />
                      </dd>
                      <dt>Policy digest</dt>
                      <dd>
                        <IdentityDisclosure value={signal.policyDigest} />
                      </dd>
                      <dt>Retained until</dt>
                      <dd>{formatDateTime(signal.retainUntil)}</dd>
                    </dl>
                  </details>
                </li>
              ))}
            </ul>
            <EconomicsPagination
              page={page.number}
              next={result.nextCursor}
              onFirst={() => setPage({ number: 1, cursor: undefined })}
              onNext={(cursor) => setPage({ number: page.number + 1, cursor })}
            />
          </>
        )}
      </EconomicsRead>
    </>
  );
}
