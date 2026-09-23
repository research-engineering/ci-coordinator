import type { CiEconomicsAttemptJobs, CiEconomicsDuration } from "../../api/ciEconomics/schema";
import { type DataColumn, DataTable } from "../../components/DataTable";
import { IdentityDisclosure } from "../../components/IdentityDisclosure";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateTime, formatInteger } from "../../domain/format";

export function CiEconomicsAttemptEvidence({
  economics,
}: {
  readonly economics: CiEconomicsAttemptJobs;
}) {
  return (
    <>
      <dl className="economics-metrics" aria-label="Server-projected CI durations">
        <DurationMetric label="Queue" value={economics.queue} />
        <DurationMetric label="Runner occupancy" value={economics.runnerOccupancy} />
        <DurationMetric label="Attempt wall" value={economics.attemptWall} />
      </dl>
      <div className="economics-provenance">
        <span>
          Recorded <strong>{formatDateTime(economics.recordedAt)}</strong>
        </span>
        <span>
          Retained until <strong>{formatDateTime(economics.retainUntil)}</strong>
        </span>
        <div>
          Evidence
          <IdentityDisclosure value={economics.observationSetHash} />
        </div>
      </div>
      <DataTable
        columns={JOB_COLUMNS}
        emptyLabel="No jobs on this attempt page."
        keyOf={(row) => String(row.providerJobId)}
        rows={economics.jobs}
      />
    </>
  );
}

const JOB_COLUMNS: readonly DataColumn<CiEconomicsAttemptJobs["jobs"][number]>[] = [
  { header: "Job", render: (row) => row.name },
  {
    header: "Conclusion",
    render: (row) => (
      <StatusBadge tone={conclusionTone(row.conclusion)}>
        {row.conclusion.replaceAll("_", " ")}
      </StatusBadge>
    ),
  },
  {
    header: "Created",
    presentation: "time",
    render: (row) => formatOptionalInstant(row.timing.createdAt),
  },
  {
    header: "Started",
    presentation: "time",
    render: (row) => formatOptionalInstant(row.timing.startedAt),
  },
  {
    header: "Completed",
    presentation: "time",
    render: (row) => formatOptionalInstant(row.timing.completedAt),
  },
  { header: "Runner", render: (row) => row.runner.runnerName ?? "Unassigned" },
  { header: "Labels", render: (row) => row.labels.join(", ") || "None" },
];

export function DurationMetric({
  label,
  value,
}: {
  readonly label: string;
  readonly value: CiEconomicsDuration;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {value.knownValueMs === null ? "Unavailable" : `${formatInteger(value.knownValueMs)} ms`}
        <span>
          <StatusBadge tone={qualityTone(value.quality)}>{value.quality}</StatusBadge>
          {` ${value.knownJobCount}/${value.totalJobCount} jobs`}
        </span>
        {value.reasonCode ? <small>{value.reasonCode.replaceAll("_", " ")}</small> : null}
      </dd>
    </div>
  );
}

function formatOptionalInstant(value: string | null): string {
  return value === null ? "Unknown" : formatDateTime(value);
}

function qualityTone(
  quality: CiEconomicsDuration["quality"],
): "info" | "negative" | "positive" | "warning" {
  if (quality === "exact") return "positive";
  if (quality === "partial") return "warning";
  if (quality === "conflict") return "negative";
  return "info";
}

function conclusionTone(
  conclusion: CiEconomicsAttemptJobs["jobs"][number]["conclusion"],
): "info" | "negative" | "positive" | "warning" {
  if (conclusion === "success") return "positive";
  if (conclusion === "failure" || conclusion === "timed_out" || conclusion === "startup_failure") {
    return "negative";
  }
  if (conclusion === "cancelled" || conclusion === "stale") {
    return "warning";
  }
  return "info";
}
