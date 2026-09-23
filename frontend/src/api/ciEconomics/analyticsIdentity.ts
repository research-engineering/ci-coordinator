import type { AnalyticsReport } from "./analyticsSchema";

export async function analyticsMappingIsCanonical(report: AnalyticsReport): Promise<boolean> {
  const mapping = report.mapping;
  if (mapping === null) return report.mappingDigest === null;
  const canonical = JSON.stringify({
    entries: mapping.entries.map((entry) => ({
      jobName: entry.jobName,
      purposes: entry.purposes,
      workflowId: entry.workflowId,
    })),
    generation: mapping.generation,
    installationId: mapping.installationId,
    provenance: mapping.provenance,
    repositoryId: mapping.repositoryId,
    version: mapping.version,
  });
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return (
    Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("") ===
    report.mappingDigest
  );
}
