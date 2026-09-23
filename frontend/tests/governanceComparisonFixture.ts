import type { GovernanceComparison } from "../src/api/governanceComparison/schema";
import { governanceObservationFixture } from "./fixture";
import { governanceBaselineRecordFixture } from "./governanceBaselineFixture";

export function governanceComparisonFixture(
  overrides: Partial<GovernanceComparison> = {},
): GovernanceComparison {
  const observation = governanceObservationFixture();
  const baseline = governanceBaselineRecordFixture();
  return {
    baseline,
    comparison: {
      addedRuleCount: 0,
      baselineStateDigest: baseline.pointer.stateDigest,
      changedCoordinates: [],
      currentStateDigest: observation.stateDigest,
      relation: "matches",
      removedRuleCount: 0,
    },
    observation,
    ok: true,
    scope: { installationId: 1, repositoryId: 1 },
    state: "compared",
    ...overrides,
  };
}

export function unbaselinedGovernanceComparisonFixture(): GovernanceComparison {
  return governanceComparisonFixture({
    baseline: null,
    comparison: null,
    state: "unbaselined",
  });
}
