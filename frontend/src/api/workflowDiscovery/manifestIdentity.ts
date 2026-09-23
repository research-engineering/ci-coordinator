import type { components } from "../generated";

type WorkflowDiscoveryReport = components["schemas"]["WorkflowDiscoveryResponse"];

export async function proposalManifestIdentityMatches(
  report: WorkflowDiscoveryReport,
): Promise<boolean> {
  if (!globalThis.crypto?.subtle) return false;
  const proposal = report.proposal;
  const policySourceHash =
    proposal.policySource === null ? null : await sha256Hex(proposal.policySource);
  const projection = {
    admittedEpochId: proposal.admittedEpochId,
    blockers: [...proposal.blockers],
    diagnostics: proposal.diagnostics.map((item) => ({
      code: item.code,
      instancePointer: item.instancePointer,
      phase: item.phase,
      ruleId: item.ruleId,
    })),
    generatorVersion: proposal.generatorVersion,
    inventoryDigest: proposal.inventoryDigest,
    nonClaims: [...proposal.nonClaims],
    policySourceHash,
    selectedEvents: [...proposal.selectedEvents],
    selectedJobId: proposal.selectedJobId,
    selectedJobName: proposal.selectedJobName,
    selectedWorkflowPath: proposal.selectedWorkflowPath,
    state: proposal.state,
    unknownIds: [...proposal.unknownIds],
  };
  const digest = await sha256Hex(canonicalJson(projection));
  return proposal.manifestId === `proposal:${digest.slice(0, 32)}`;
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value !== "object") throw new TypeError("manifest projection escaped JSON domain");
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(",")}}`;
}
