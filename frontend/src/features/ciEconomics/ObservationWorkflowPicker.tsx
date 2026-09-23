import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useCallback, useState } from "react";
import { fetchObservationWorkflows } from "../../api/ciEconomics/observationClient";
import type { WorkbenchScope } from "../../api/workbench/client";
import { EconomicsRead, EconomicsRefresh } from "./EconomicsControls";
import { useEconomicsRead } from "./useEconomicsRead";

export function ObservationWorkflowPicker({
  scope,
  selected,
  onChange,
}: {
  readonly scope: WorkbenchScope;
  readonly selected: readonly number[];
  readonly onChange: (ids: readonly number[]) => void;
}) {
  const [page, setPage] = useState(1);
  const read = useCallback(
    (signal: AbortSignal) => fetchObservationWorkflows(scope, page, signal),
    [scope, page],
  );
  const catalog = useEconomicsRead(read);
  return (
    <section className="observation-workflows" aria-label="Workflow selection">
      <div className="economics-subheading">
        <h3>Selected workflows ({selected.length}/32)</h3>
        <EconomicsRefresh onClick={catalog.refresh} disabled={catalog.state.kind === "loading"} />
      </div>
      <EconomicsRead state={catalog.state} onRetry={catalog.refresh}>
        {(value) => (
          <>
            <ul className="observation-workflow-list">
              {value.items.map((workflow) => (
                <li key={workflow.workflowId}>
                  <label>
                    <input
                      type="checkbox"
                      checked={selected.includes(workflow.workflowId)}
                      disabled={!selected.includes(workflow.workflowId) && selected.length >= 32}
                      onChange={(event) =>
                        onChange(
                          event.target.checked
                            ? [...selected, workflow.workflowId]
                            : selected.filter((id) => id !== workflow.workflowId),
                        )
                      }
                    />
                    <span>
                      <strong>{workflow.name}</strong>
                      <code>{workflow.path}</code>
                      <small>
                        #{workflow.workflowId} / {workflow.state}
                      </small>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
            {value.items.length === 0 ? (
              <p className="economics-prompt">No workflows on this provider page.</p>
            ) : null}
            {value.termination === "truncated" ? (
              <p className="economics-prompt" role="status">
                The provider catalogue is incomplete. Existing selections remain unchanged.
              </p>
            ) : null}
            <nav className="economics-pagination" aria-label="Workflow pages">
              <button
                type="button"
                className="icon-button"
                aria-label="Previous workflow page"
                title="Previous workflow page"
                disabled={page === 1}
                onClick={() => setPage((current) => current - 1)}
              >
                <ChevronLeft aria-hidden="true" />
              </button>
              <span aria-current="page">Page {page}</span>
              <button
                type="button"
                className="icon-button"
                aria-label="Next workflow page"
                title="Next workflow page"
                disabled={value.termination !== "next_page"}
                onClick={() => setPage((current) => current + 1)}
              >
                <ChevronRight aria-hidden="true" />
              </button>
            </nav>
          </>
        )}
      </EconomicsRead>
      {selected.length > 0 ? (
        <details className="observation-selected">
          <summary>Selected workflow IDs ({selected.length})</summary>
          <ul className="economics-source-list">
            {selected.map((id) => (
              <li key={id}>
                <span>Workflow #{id}</span>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Remove workflow ${id}`}
                  title={`Remove workflow ${id}`}
                  onClick={() => onChange(selected.filter((item) => item !== id))}
                >
                  <X aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </details>
      ) : (
        <p className="economics-prompt" role="status">
          Select at least one workflow.
        </p>
      )}
    </section>
  );
}
