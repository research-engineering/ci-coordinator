import { RotateCw } from "lucide-react";
import { useState } from "react";
import { selectedRepairIntervals } from "../../api/ciEconomics/archiveGapRepairSchema";
import type { ArchiveRead } from "../../api/ciEconomics/archiveReadSchema";
import { ScrollableRegion } from "../../components/ScrollableRegion";
import { formatDateTime } from "../../domain/format";

const evidenceLabels = {
  not_evaluated: "Window not evaluated",
  missing: "Not retained",
  retained_complete: "Complete retained summary",
  retained_incomplete: "Incomplete retained summary",
  retained_conflicting: "Conflicting retained summary",
} as const;

export function ArchiveGaps({
  page,
  disabled,
  onRetry,
}: {
  readonly page: ArchiveRead;
  readonly disabled: boolean;
  readonly onRetry: (ids: readonly string[]) => void;
}) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const selectable = page.gaps.filter((gap) => gap.retrySupported);
  const ids = [...selected].sort();
  const intervals = selectedRepairIntervals(page, ids);
  const allSelected = selectable.length > 0 && selectable.every((gap) => selected.has(gap.gapId));
  return (
    <>
      <div className="economics-subheading">
        <span>{selected.size} selected</span>
        <button
          type="button"
          className="button button--secondary"
          disabled={disabled || intervals === null}
          onClick={() => onRetry(ids)}
        >
          <RotateCw aria-hidden="true" className="button-icon" />
          Retry selected gaps
        </button>
      </div>
      {ids.length > 0 && intervals === null ? (
        <p role="alert">Select at most 50 attempts across the run intervals.</p>
      ) : null}
      <ScrollableRegion className="archive-table-scroll" label="Collection gap table">
        <table className="archive-table archive-gap-table">
          <caption>Recorded failures and current retained evidence</caption>
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="Select retryable gaps on this page"
                  disabled={disabled || selectable.length === 0}
                  checked={allSelected}
                  onChange={(event) =>
                    setSelected(
                      event.currentTarget.checked
                        ? new Set(selectable.map((gap) => gap.gapId))
                        : new Set(),
                    )
                  }
                />
              </th>
              <th>Recorded</th>
              <th>Original reason</th>
              <th>Run</th>
              <th>Current archive</th>
              <th>Source window</th>
            </tr>
          </thead>
          <tbody>
            {page.gaps.map((gap) => (
              <tr key={gap.gapId}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Select gap ${gap.gapId}`}
                    disabled={disabled || !gap.retrySupported}
                    checked={selected.has(gap.gapId)}
                    title={
                      gap.retrySupported
                        ? "Select for bounded archive retry"
                        : "Targeted retry metadata unavailable; use full rescan"
                    }
                    onChange={(event) => {
                      const next = new Set(selected);
                      if (event.currentTarget.checked) next.add(gap.gapId);
                      else next.delete(gap.gapId);
                      setSelected(next);
                    }}
                  />
                </td>
                <td>{formatDateTime(gap.recordedAt)}</td>
                <td>{gap.reason.replaceAll("_", " ")}</td>
                <td>
                  {gap.workflowRunId === null
                    ? "Unknown"
                    : `#${gap.workflowRunId} / ${gap.runAttempt}`}
                </td>
                <td>{evidenceLabels[gap.resolution]}</td>
                <td>
                  {gap.sourceWindow === null
                    ? "Not applicable"
                    : `${gap.sourceWindow.windowFrom} - ${gap.sourceWindow.windowThrough}`}
                </td>
              </tr>
            ))}
            {page.gaps.length === 0 ? (
              <tr>
                <td colSpan={6}>No recorded gaps on this page.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </ScrollableRegion>
    </>
  );
}
