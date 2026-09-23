import type { ConfigEpoch, ConfigScope, ConfigValidation, SourceFormat } from "./schema";

export async function sourceHash(source: string, format: SourceFormat): Promise<string> {
  return hexDigest(new TextEncoder().encode(`ci-policy-source/v1\0${format}\0${source}`));
}

export async function epochIdentityMatches(
  scope: ConfigScope,
  epoch: Pick<ConfigEpoch, "epochId" | "sourceHash" | "documentHash" | "epochHash">,
): Promise<boolean> {
  const scopeBytes = JSON.stringify({
    installationId: scope.installationId,
    repositoryId: scope.repositoryId,
  });
  const identity = `ci-config-epoch/v1\0${scopeBytes}\0${epoch.sourceHash}\0${epoch.documentHash}\0${epoch.epochHash}`;
  return epoch.epochId === (await hexDigest(new TextEncoder().encode(identity)));
}

export async function validationMatches(
  scope: ConfigScope,
  source: string,
  format: SourceFormat,
  validation: ConfigValidation,
): Promise<boolean> {
  return (
    validation.installationId === scope.installationId &&
    validation.repositoryId === scope.repositoryId &&
    validation.sourceHash === (await sourceHash(source, format)) &&
    (await epochIdentityMatches(scope, validation))
  );
}

export async function hexDigest(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}
