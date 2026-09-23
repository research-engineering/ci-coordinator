import type { CiEconomicsAttemptSummary } from "../../api/ciEconomics/schema";
import { type DataColumn, DataTable } from "../../components/DataTable";
import { IdentityDisclosure } from "../../components/IdentityDisclosure";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateTime, formatInteger } from "../../domain/format";

export function CiEconomicsAttemptList({
  attempts,
  onSelect,
  selected,
}: {
  readonly attempts: readonly CiEconomicsAttemptSummary[];
  readonly onSelect: (attempt: CiEconomicsAttemptSummary) => void;
  readonly selected: CiEconomicsAttemptSummary | undefined;
}) {
  const columns: readonly DataColumn<CiEconomicsAttemptSummary>[] = [
    { header: "Recorded", presentation: "time", render: (row) => formatDateTime(row.recordedAt) },
    {
      header: "Run",
      render: (row) => `${row.attempt.workflowRunId} / ${row.attempt.runAttempt}`,
    },
    {
      header: "Route",
      render: (row) => (
        <StatusBadge tone={routeTone(row.plannedRoute)}>
          {row.plannedRoute.replaceAll("_", " ")}
        </StatusBadge>
      ),
    },
    { header: "Jobs", render: (row) => formatInteger(row.jobCount) },
    {
      header: "Head",
      presentation: "identity",
      render: (row) => <IdentityDisclosure value={row.attempt.headSha} />,
    },
    {
      header: "Evidence",
      render: (row) => (
        <button
          aria-label={`View jobs for run ${row.attempt.workflowRunId}, attempt ${row.attempt.runAttempt}`}
          aria-pressed={selected?.subjectId === row.subjectId}
          className="button button--compact"
          onClick={() => onSelect(row)}
          type="button"
        >
          View jobs
        </button>
      ),
    },
  ];
  return (
    <div className="economics-subsection">
      <div className="economics-subheading">
        <h3>Retained attempts</h3>
        <span>Repository-scoped server evidence</span>
      </div>
      <DataTable
        columns={columns}
        emptyLabel="No retained CI attempts."
        keyOf={(row) => row.subjectId}
        rows={attempts}
      />
    </div>
  );
}

function routeTone(
  route: CiEconomicsAttemptSummary["plannedRoute"],
): "info" | "neutral" | "warning" {
  if (route === "selected") return "info";
  if (route === "full_ci_counterfactual") return "warning";
  return "neutral";
}
