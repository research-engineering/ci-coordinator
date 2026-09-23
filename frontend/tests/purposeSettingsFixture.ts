import type {
  PurposeSettingsCommand,
  PurposeSettingsSnapshot,
} from "../src/api/ciEconomics/purposeSettingsSchema";

export const purposeQuery = { installationId: 1, repositoryId: 1, generation: 1 };
export function purposeCommand(): PurposeSettingsCommand {
  return {
    ...purposeQuery,
    expectedRevision: 0,
    operationId: "operation-one",
    entries: [{ workflowId: 17, jobName: "Ruff", purposes: ["lint"] }],
  };
}
export function purposeSnapshot(command?: PurposeSettingsCommand): PurposeSettingsSnapshot {
  return command
    ? {
        installationId: command.installationId,
        repositoryId: command.repositoryId,
        generation: command.generation,
        revision: command.expectedRevision + 1,
        mapping: {
          ...purposeQuery,
          generation: command.generation,
          installationId: command.installationId,
          repositoryId: command.repositoryId,
          version: `repository-settings:${command.expectedRevision + 1}`,
          provenance: "administrator-api/v1",
          entries: command.entries,
        },
      }
    : { ...purposeQuery, revision: 0, mapping: null };
}
