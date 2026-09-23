import { canonicalJsonText } from "./canonicalJson";
import type { GovernanceObservation } from "./schema";

export interface GovernanceStateIdentity {
  readonly apiVersion: string;
  readonly repository: GovernanceObservation["repository"];
  readonly rules: GovernanceObservation["rules"];
  readonly stateDigest: string;
}

export async function governanceStateDigestMatches(
  state: GovernanceStateIdentity,
): Promise<boolean> {
  if (!globalThis.crypto?.subtle) return false;
  try {
    const bytes = new TextEncoder().encode(governanceStateCanonicalText(state));
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    const actual = [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    return actual === state.stateDigest;
  } catch {
    return false;
  }
}

export function governanceStateCanonicalText(state: GovernanceStateIdentity): string {
  const repository = state.repository;
  return canonicalJsonText({
    apiVersion: state.apiVersion,
    repository: {
      defaultBranch: repository.defaultBranch,
      fullName: repository.fullName,
      name: repository.name,
      owner: repository.owner,
      ownerId: repository.ownerId,
    },
    rules: state.rules.map((rule) => JSON.parse(rule.canonicalJson) as unknown),
    schemaVersion: "github-effective-governance-state/v1",
    scope: {
      installationId: repository.scope.installationId,
      repositoryId: repository.scope.repositoryId,
    },
  });
}
