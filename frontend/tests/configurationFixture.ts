import { createHash } from "node:crypto";
import type { RollbackCommand, ValidatedSource } from "../src/api/configLifecycle/client";
import type {
  ConfigEpoch,
  ConfigScope,
  ConfigStatus,
  ConfigValidation,
  SourceFormat,
} from "../src/api/configLifecycle/schema";

export const configurationScope: ConfigScope = { installationId: 1, repositoryId: 1 };
export const configurationSource =
  '{"schemaVersion":"ci-repository-policy/v1","repository":{"installationId":1,"repositoryId":1,"owner":"example-org","name":"ci-coordinator","defaultBranch":"main","dynamicCi":null,"rules":[{"name":"main","on":{"event":"push","branches":["main"]},"mode":"observe","timing":{"expectedSignalTimeoutSeconds":3600,"absenceVerificationWindowSeconds":300,"absencePollLookbackSeconds":3600,"lateFindingWindowSeconds":86400,"mutableDecisionWindowSeconds":300},"expectedSignals":[{"kind":"workflow","name":"CI","workflowFile":"ci.yml","source":"native","requiredConclusion":"success","required":true}],"omittedSignals":[]}]}}';
export const configHeaders = { "cache-control": "no-store", "content-type": "application/json" };
const digest = (value: string) => createHash("sha256").update(value, "utf8").digest("hex");

export function configValidation(
  source = configurationSource,
  sourceFormat: SourceFormat = "json",
  scope = configurationScope,
): ConfigValidation {
  const sourceHash = digest(`ci-policy-source/v1\0${sourceFormat}\0${source}`);
  const documentHash = digest("synthetic normalized document");
  const epochHash = digest("synthetic compiled policy");
  const scopeBytes = `{"installationId":${scope.installationId},"repositoryId":${scope.repositoryId}}`;
  return {
    schemaVersion: "ci-config-epoch-validation-result/v1",
    ok: true,
    ...scope,
    sourceHash,
    documentHash,
    epochHash,
    epochId: digest(
      `ci-config-epoch/v1\0${scopeBytes}\0${sourceHash}\0${documentHash}\0${epochHash}`,
    ),
    documentSchemaId: "ci-repository-policy/v1",
    documentProfileId: "ci-policy-document/v1",
    semanticProfileId: "ci-repository-policy-semantics/v1",
    compiledSchemaId: "ci-compiled-repository-policy/v1",
  };
}

export function validatedSource(
  source = configurationSource,
  sourceFormat: SourceFormat = "json",
): ValidatedSource {
  return {
    scope: configurationScope,
    source,
    sourceFormat,
    validation: configValidation(source, sourceFormat),
  };
}

export function configEpoch(
  source = configurationSource,
  sourceFormat: SourceFormat = "json",
): ConfigEpoch {
  const { epochId, sourceHash, documentHash, epochHash } = configValidation(source, sourceFormat);
  return {
    epochId,
    sourceHash,
    documentHash,
    epochHash,
    sourceFormat,
    sourceByteCount: Buffer.byteLength(source, "utf8"),
  };
}

export function configStatus(): ConfigStatus {
  const epochs = [configEpoch(), configEpoch(`${configurationSource}\n`)].sort((left, right) =>
    left.epochId < right.epochId ? -1 : 1,
  );
  return {
    schemaVersion: "ci-config-epoch-status/v1",
    ...configurationScope,
    active: { epochId: configEpoch(`${configurationSource}\n`).epochId, revision: 4 },
    epochs,
    nextCursor: null,
  };
}

export function rollbackCommand(): RollbackCommand {
  return {
    kind: "rollback",
    currentEpochId: configEpoch(`${configurationSource}\n`).epochId,
    body: {
      schemaVersion: "ci-config-epoch-rollback/v1",
      ...configurationScope,
      expectedRevision: 4,
      targetEpochId: configEpoch().epochId,
      operationId: "rollback-one",
      reason: "Restore reviewed configuration",
    },
  };
}

export function sourceResponse(
  source = configurationSource,
  sourceFormat: SourceFormat = "json",
): Response {
  const epoch = configEpoch(source, sourceFormat);
  return new Response(Buffer.from(source), {
    headers: {
      "cache-control": "no-store",
      "content-type": sourceFormat === "json" ? "application/json" : "application/yaml",
      etag: `"${epoch.sourceHash}"`,
      "x-ci-config-epoch-id": epoch.epochId,
      "content-digest": `sha-256=:${createHash("sha256").update(source, "utf8").digest("base64")}:`,
    },
  });
}

export function configJson(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: configHeaders });
}
