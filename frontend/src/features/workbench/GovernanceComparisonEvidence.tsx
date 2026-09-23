import { AlertTriangle, GitCompareArrows, MinusCircle } from "lucide-react";
import type { GovernanceComparison } from "../../api/governanceComparison/schema";
import { StatusBadge } from "../../components/StatusBadge";

export function GovernanceComparisonEvidence({
  evidence,
}: {
  readonly evidence: GovernanceComparison;
}) {
  const comparison = evidence.comparison;
  if (evidence.state === "unbaselined" || comparison === null) {
    return (
      <section className="governance-comparison" aria-labelledby="governance-comparison-heading">
        <MinusCircle aria-hidden="true" />
        <div>
          <h3 id="governance-comparison-heading">Exact comparison</h3>
          <p>No approved expected state is available for comparison.</p>
        </div>
        <StatusBadge tone="neutral">unbaselined</StatusBadge>
      </section>
    );
  }
  const matches = comparison.relation === "matches";
  return (
    <section
      className={`governance-comparison governance-comparison--${comparison.relation}`}
      aria-labelledby="governance-comparison-heading"
    >
      {matches ? <GitCompareArrows aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
      <div>
        <h3 id="governance-comparison-heading">Exact comparison</h3>
        <p>
          {matches
            ? "Current canonical state bytes equal the approved baseline."
            : changedCoordinateSummary(comparison.changedCoordinates)}
        </p>
        {!matches && comparison.changedCoordinates.includes("rules") ? (
          <span>
            {comparison.addedRuleCount} added, {comparison.removedRuleCount} removed exact rule
            objects
          </span>
        ) : null}
      </div>
      <StatusBadge tone={matches ? "info" : "warning"}>
        {matches ? "bytes match" : "bytes differ"}
      </StatusBadge>
    </section>
  );
}

function changedCoordinateSummary(
  coordinates: NonNullable<GovernanceComparison["comparison"]>["changedCoordinates"],
): string {
  const labels: Readonly<Record<string, string>> = {
    api_version: "API version",
    "repository.default_branch": "default branch",
    "repository.full_name": "full repository name",
    "repository.name": "repository name",
    "repository.owner": "repository owner",
    "repository.owner_id": "repository owner ID",
    rules: "exact rule set",
  };
  return `Canonical state differs at: ${coordinates.map((value) => labels[value] ?? value).join(", ")}.`;
}
