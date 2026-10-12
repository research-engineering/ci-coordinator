import type { CapabilityPassport } from "../src/api/workbench/capabilityPassportSchema";
import { installationFixture } from "./fixture";

const OBSERVED_AT = "2026-10-11T12:00:00Z";

export function capabilityPassportFixture(
  overrides: Partial<CapabilityPassport> = {},
): CapabilityPassport {
  return {
    actionsOidcKeys: {
      ageSeconds: 12,
      keyCount: 2,
      observedAt: OBSERVED_AT,
      reason: null,
      source: "github_actions_jwks_cache",
      status: "known",
    },
    installationGrant: {
      installation: installationFixture({
        events: ["workflow_run"],
        permissions: [{ level: "read", name: "actions" }],
      }),
      observedAt: OBSERVED_AT,
      reason: null,
      source: "github_app_installation",
      status: "known",
    },
    nextAction: "none",
    observedAt: OBSERVED_AT,
    ok: true,
    productionAdmissionKeys: {
      reason: "not_projected",
      source: "production_admission_authorities",
      status: "unknown",
    },
    providerLimits: {
      observedAt: OBSERVED_AT,
      reason: "no_retained_limit_observation",
      retryAfterSeconds: null,
      source: "github_app_installation",
      status: "unknown",
    },
    reconciliation: {
      backgroundState: "healthy",
      detailTruncatedUntil: null,
      enabled: true,
      gapsRecorded: false,
      lanes: [
        {
          lane: "recent",
          lastCompletedThrough: OBSERVED_AT,
          lastOutcome: "page_recorded",
          lastPageAt: OBSERVED_AT,
          nextAttemptAt: OBSERVED_AT,
        },
      ],
      observedAt: OBSERVED_AT,
      reason: null,
      source: "ci_observation_status",
      status: "known",
    },
    schemaVersion: "capability-passport/v1",
    scope: { installationId: 1, repositoryId: 1 },
    webhookObservation: {
      reason: "receipt_clock_not_indexed",
      source: "history_delivery_inbox",
      status: "unknown",
    },
    ...overrides,
  };
}
