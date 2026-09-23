import { epochIdentityMatches, hexDigest, sourceHash } from "./identity";
import { type ConfigEpoch, type ConfigScope, MAX_SOURCE_BYTES, scopeSchema } from "./schema";
import { attempt, failure, type LifecycleResult, lifecycleRequest, noStore } from "./transport";

export async function readSourceFile(file: File): Promise<string> {
  if (file.size < 1 || file.size > MAX_SOURCE_BYTES)
    throw new RangeError("Source must be 1 to 2,097,152 UTF-8 bytes.");
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length !== file.size) throw new TypeError("Source file changed while reading.");
  const source = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  if (new TextEncoder().encode(source).length !== bytes.length)
    throw new TypeError("Exact UTF-8 source required.");
  return source;
}

export function exportConfigSource(
  scope: ConfigScope,
  epoch: ConfigEpoch,
  signal?: AbortSignal,
): Promise<LifecycleResult<Blob>> {
  return attempt(async () => {
    if (!scopeSchema.safeParse(scope).success || !(await epochIdentityMatches(scope, epoch)))
      return { kind: "invalid-request" };
    const response = await lifecycleRequest(
      `/api/v1/config/repositories/${scope.installationId}/${scope.repositoryId}/epochs/${epoch.epochId}/source`,
      signal,
    );
    if (response.status !== 200) return failure(response);
    const mediaType = epoch.sourceFormat === "json" ? "application/json" : "application/yaml";
    if (
      !noStore(response) ||
      response.headers.get("content-type") !== mediaType ||
      response.headers.get("etag") !== `"${epoch.sourceHash}"` ||
      response.headers.get("x-ci-config-epoch-id") !== epoch.epochId
    )
      return { kind: "invalid-response" };
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (
      bytes.length !== epoch.sourceByteCount ||
      bytes.length < 1 ||
      bytes.length > MAX_SOURCE_BYTES
    )
      return { kind: "invalid-response" };
    let source: string;
    try {
      source = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
    } catch {
      return { kind: "invalid-response" };
    }
    const rawHash = await hexDigest(bytes);
    const encodedDigest = btoa(
      String.fromCharCode(
        ...Array.from({ length: 32 }, (_, i) =>
          Number.parseInt(rawHash.slice(i * 2, i * 2 + 2), 16),
        ),
      ),
    );
    if (
      response.headers.get("content-digest") !== `sha-256=:${encodedDigest}:` ||
      (await sourceHash(source, epoch.sourceFormat)) !== epoch.sourceHash
    )
      return { kind: "invalid-response" };
    return { kind: "ready", value: new Blob([bytes], { type: mediaType }) };
  });
}
