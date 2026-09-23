import type { components } from "../generated";
import {
  type GovernanceStateIdentity,
  governanceStateCanonicalText,
} from "../governanceObservation/identity";
import type { GovernanceComparison } from "./schema";

type ExactComparison = components["schemas"]["ExactGovernanceComparisonResponse"];
type ChangedCoordinate = components["schemas"]["GovernanceChangedCoordinate"];

export function deriveGovernanceComparison(
  baseline: GovernanceStateIdentity,
  current: GovernanceStateIdentity,
): ExactComparison {
  if (!sameScope(baseline.repository.scope, current.repository.scope)) {
    throw new TypeError("governance comparison cannot cross repository scope");
  }
  const changedCoordinates: ChangedCoordinate[] = [];
  if (baseline.apiVersion !== current.apiVersion) changedCoordinates.push("api_version");
  if (baseline.repository.ownerId !== current.repository.ownerId) {
    changedCoordinates.push("repository.owner_id");
  }
  if (baseline.repository.owner !== current.repository.owner) {
    changedCoordinates.push("repository.owner");
  }
  if (baseline.repository.name !== current.repository.name) {
    changedCoordinates.push("repository.name");
  }
  if (baseline.repository.fullName !== current.repository.fullName) {
    changedCoordinates.push("repository.full_name");
  }
  if (baseline.repository.defaultBranch !== current.repository.defaultBranch) {
    changedCoordinates.push("repository.default_branch");
  }
  const baselineRules = new Set(baseline.rules.map((rule) => rule.canonicalJson));
  const currentRules = new Set(current.rules.map((rule) => rule.canonicalJson));
  const addedRuleCount = differenceSize(currentRules, baselineRules);
  const removedRuleCount = differenceSize(baselineRules, currentRules);
  if (addedRuleCount > 0 || removedRuleCount > 0) changedCoordinates.push("rules");

  return {
    addedRuleCount,
    baselineStateDigest: baseline.stateDigest,
    changedCoordinates,
    currentStateDigest: current.stateDigest,
    relation:
      governanceStateCanonicalText(baseline) === governanceStateCanonicalText(current)
        ? "matches"
        : "differs",
    removedRuleCount,
  };
}

export function governanceComparisonMatchesEvidence(evidence: GovernanceComparison): boolean {
  try {
    if (evidence.baseline === null || evidence.comparison === null) {
      return (
        evidence.state === "unbaselined" &&
        evidence.baseline === null &&
        evidence.comparison === null
      );
    }
    if (evidence.state !== "compared") return false;
    const derived = deriveGovernanceComparison(evidence.baseline.state, evidence.observation);
    const advertised = evidence.comparison;
    return (
      advertised.relation === derived.relation &&
      advertised.baselineStateDigest === derived.baselineStateDigest &&
      advertised.currentStateDigest === derived.currentStateDigest &&
      advertised.addedRuleCount === derived.addedRuleCount &&
      advertised.removedRuleCount === derived.removedRuleCount &&
      sameCoordinates(advertised.changedCoordinates, derived.changedCoordinates)
    );
  } catch {
    return false;
  }
}

function differenceSize(left: ReadonlySet<string>, right: ReadonlySet<string>): number {
  let size = 0;
  for (const value of left) {
    if (!right.has(value)) size += 1;
  }
  return size;
}

function sameScope(
  left: { readonly installationId: number; readonly repositoryId: number },
  right: { readonly installationId: number; readonly repositoryId: number },
): boolean {
  return left.installationId === right.installationId && left.repositoryId === right.repositoryId;
}

function sameCoordinates(
  left: readonly ChangedCoordinate[],
  right: readonly ChangedCoordinate[],
): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}
