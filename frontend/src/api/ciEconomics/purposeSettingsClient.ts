import { decodePurposeSettings } from "./purposeSettingsDecoder";
import {
  type PurposeSettingsCommand,
  type PurposeSettingsQuery,
  type PurposeSettingsRead,
  type PurposeSettingsWrite,
  purposeSettingsCommandSchema,
  purposeSettingsQuerySchema,
  purposeSettingsReadSchema,
  purposeSettingsWriteSchema,
} from "./purposeSettingsSchema";
import { sameScope } from "./sourceSchema";
import { type EconomicsResult, requestEconomics } from "./transport";

function path(query: PurposeSettingsQuery) {
  return `/api/v2/economics/repositories/${query.installationId}/${query.repositoryId}/history/analytics/settings`;
}

export async function fetchPurposeSettings(
  input: PurposeSettingsQuery,
  signal?: AbortSignal,
): Promise<EconomicsResult<PurposeSettingsRead>> {
  const query = purposeSettingsQuerySchema.safeParse(input);
  if (!query.success) return { kind: "invalid-response" };
  return requestEconomics({
    path: `${path(query.data)}?generation=${query.data.generation}`,
    schema: purposeSettingsReadSchema,
    decode: decodePurposeSettings,
    maximumBytes: 266240,
    signal,
    admits: (value) =>
      value.outcome === "unavailable" ||
      (sameScope(value.snapshot, query.data) &&
        value.snapshot.generation === query.data.generation),
  });
}

export async function configurePurposeSettings(
  input: PurposeSettingsCommand,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<EconomicsResult<PurposeSettingsWrite>> {
  const parsed = purposeSettingsCommandSchema.safeParse(input);
  if (!parsed.success) return { kind: "invalid-response" };
  const command = parsed.data;
  return requestEconomics({
    path: path(command),
    schema: purposeSettingsWriteSchema,
    decode: decodePurposeSettings,
    method: "PUT",
    body: command,
    csrfToken,
    signal,
    maximumBytes: 266240,
    successStatuses: [200, 409],
    admits: (value, status) =>
      value.operationId === command.operationId &&
      (value.snapshot === null
        ? status === 409
        : status === 200 &&
          sameScope(value.snapshot, command) &&
          value.snapshot.generation === command.generation &&
          value.snapshot.revision === command.expectedRevision + 1 &&
          JSON.stringify(value.snapshot.mapping?.entries) === JSON.stringify(command.entries)),
  });
}
