import { ArrowLeft, ArrowRight } from "lucide-react";
import { useCallback, useState } from "react";
import { fetchEconomicsSources } from "../../api/ciEconomics/catalogClient";
import type { SourceItem } from "../../api/ciEconomics/catalogSchema";
import type { RetainedReport } from "../../api/ciEconomics/measurementSchema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateTime } from "../../domain/format";
import { EconomicsPagination, EconomicsRead, EconomicsRefresh } from "./EconomicsControls";
import { ReportComparisonPanel } from "./ReportComparisonPanel";
import { SourceEvidence } from "./SourceEvidence";
import { useEconomicsRead } from "./useEconomicsRead";

export function RegisteredRuns({ scope }: { readonly scope: WorkbenchScope }) {
  const [page, setPage] = useState({ number: 1, cursor: null as string | null });
  const [selected, setSelected] = useState<SourceItem>();
  const [baseline, setBaseline] = useState<RetainedReport>();
  const [treatment, setTreatment] = useState<RetainedReport>();
  const [comparing, setComparing] = useState(false);
  const read = useCallback(
    (signal: AbortSignal) =>
      fetchEconomicsSources(
        { installationId: scope.installationId, repositoryId: scope.repositoryId },
        page.cursor,
        signal,
      ),
    [scope.installationId, scope.repositoryId, page.cursor],
  );
  const query = useEconomicsRead(read);
  function refresh() {
    setSelected(undefined);
    setBaseline(undefined);
    setTreatment(undefined);
    setComparing(false);
    query.refresh();
  }
  return (
    <>
      <div className="economics-subheading">
        <h2>
          {comparing
            ? "Report comparison"
            : selected
              ? `Run ${selected.source.attempt.workflowRunId}`
              : "Registered runs"}
        </h2>
        <div className="economics-actions">
          {baseline || treatment ? (
            <button
              type="button"
              className="button button--compact"
              disabled={!baseline || !treatment}
              onClick={() => setComparing(true)}
            >
              Compare {Number(!!baseline) + Number(!!treatment)}/2
            </button>
          ) : null}
          <EconomicsRefresh onClick={refresh} />
        </div>
      </div>
      {comparing && baseline && treatment ? (
        <>
          <nav className="economics-navigation" aria-label="Comparison navigation">
            <button
              type="button"
              className="button button--compact"
              onClick={() => setComparing(false)}
            >
              <ArrowLeft className="button-icon" aria-hidden="true" />
              Runs
            </button>
          </nav>
          <ReportComparisonPanel baseline={baseline} treatment={treatment} />
        </>
      ) : selected ? (
        <>
          <nav className="economics-navigation" aria-label="Run navigation">
            <button
              type="button"
              className="button button--compact"
              onClick={() => setSelected(undefined)}
            >
              <ArrowLeft className="button-icon" aria-hidden="true" />
              Registered runs
            </button>
          </nav>
          <SourceEvidence
            key={selected.source.sourceId}
            item={selected}
            onBaseline={setBaseline}
            onTreatment={setTreatment}
          />
        </>
      ) : (
        <EconomicsRead state={query.state} onRetry={query.refresh}>
          {(value) => (
            <>
              <ul className="economics-source-list">
                {value.items.map((item) => (
                  <li key={item.source.sourceId}>
                    <div>
                      <strong>Run {item.source.attempt.workflowRunId}</strong>
                      <span>
                        Attempt {item.source.attempt.runAttempt} /{" "}
                        {item.source.attempt.headSha.slice(0, 8)}
                      </span>
                      <time dateTime={item.source.runCreatedAt}>
                        {formatDateTime(item.source.runCreatedAt)}
                      </time>
                    </div>
                    <StatusBadge
                      tone={
                        item.status === "captured"
                          ? "positive"
                          : item.status === "terminal_unavailable"
                            ? "warning"
                            : "info"
                      }
                    >
                      {item.status.replaceAll("_", " ")}
                    </StatusBadge>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={`Inspect run ${item.source.attempt.workflowRunId}, attempt ${item.source.attempt.runAttempt}`}
                      title="Inspect run"
                      onClick={() => setSelected(item)}
                    >
                      <ArrowRight aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
              {value.items.length === 0 ? (
                <p className="economics-prompt">No retained runs.</p>
              ) : null}
              <EconomicsPagination
                page={page.number}
                next={value.nextCursor}
                onNext={(cursor) => setPage((old) => ({ number: old.number + 1, cursor }))}
                onFirst={() => setPage({ number: 1, cursor: null })}
              />
            </>
          )}
        </EconomicsRead>
      )}
    </>
  );
}
