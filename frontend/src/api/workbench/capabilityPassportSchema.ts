import { z } from "zod";
import type { components } from "../generated";
import { installationSchema } from "../providerInventory/schema";

export type CapabilityPassport = components["schemas"]["CapabilityPassportResponse"];
export type CapabilityPassportError = components["schemas"]["CapabilityPassportErrorResponse"];
type CapabilityNextAction = CapabilityPassport["nextAction"];

const dateTime = z.iso.datetime({ offset: true });
const inventoryFailureReason = z.enum([
  "unavailable",
  "rate_limited",
  "not_found",
  "malformed_provider_response",
  "provider_binding_mismatch",
]);

const installationGrant = z
  .strictObject({
    installation: installationSchema.nullable(),
    observedAt: dateTime,
    reason: z.union([inventoryFailureReason, z.literal("inventory_grant_absent")]).nullable(),
    source: z.literal("github_app_installation"),
    status: z.enum(["known", "denied", "unavailable"]),
  })
  .superRefine((value, context) => {
    const consistent =
      value.status === "known"
        ? value.installation !== null && value.reason === null
        : value.installation === null &&
          (value.status === "denied"
            ? value.reason === "inventory_grant_absent"
            : value.reason !== null && value.reason !== "inventory_grant_absent");
    if (!consistent) {
      context.addIssue({ code: "custom", message: "installation grant contradicts its status" });
    }
  });

const providerLimits = z
  .strictObject({
    observedAt: dateTime,
    reason: z.enum(["rate_limited", "no_retained_limit_observation"]),
    retryAfterSeconds: z.number().int().min(0).max(3_600).nullable(),
    source: z.literal("github_app_installation"),
    status: z.enum(["known", "unknown"]),
  })
  .superRefine((value, context) => {
    if (
      (value.status === "known") !== (value.reason === "rate_limited") ||
      (value.status === "unknown" && value.retryAfterSeconds !== null)
    ) {
      context.addIssue({ code: "custom", message: "provider limit contradicts its status" });
    }
  });

// Each unprojected fact has exactly one backend source and reason.
const webhookObservation = z.strictObject({
  reason: z.literal("receipt_clock_not_indexed"),
  source: z.literal("history_delivery_inbox"),
  status: z.literal("unknown"),
});

const productionAdmissionKeys = z.strictObject({
  reason: z.literal("not_projected"),
  source: z.literal("production_admission_authorities"),
  status: z.literal("unknown"),
});

const reconciliationLane = z.strictObject({
  lane: z.enum(["recent", "backfill"]),
  lastCompletedThrough: dateTime.nullable(),
  lastOutcome: z
    .enum([
      "provider_unavailable",
      "provider_binding_mismatch",
      "provider_malformed",
      "provider_incomplete",
      "provider_not_terminal",
      "provider_unstable",
      "access_unavailable",
      "timed_out",
      "page_recorded",
      "capacity_reached",
    ])
    .nullable(),
  lastPageAt: dateTime.nullable(),
  nextAttemptAt: dateTime,
});

const RECONCILIATION_REASONS = {
  denied: "access_denied",
  unavailable: "store_unavailable",
  unknown: "observation_not_configured",
} as const;

const reconciliation = z
  .strictObject({
    backgroundState: z.enum([
      "healthy",
      "degraded",
      "starting",
      "stopped",
      "not_configured",
      "unobservable",
      "terminal_failure",
    ]),
    detailTruncatedUntil: dateTime.nullable(),
    enabled: z.boolean().nullable(),
    gapsRecorded: z.boolean().nullable(),
    lanes: z.array(reconciliationLane).max(2),
    observedAt: dateTime.nullable(),
    reason: z
      .enum([
        "observation_not_configured",
        "observation_paused",
        "access_denied",
        "store_unavailable",
      ])
      .nullable(),
    source: z.literal("ci_observation_status"),
    status: z.enum(["known", "unknown", "denied", "unavailable"]),
  })
  .superRefine((value, context) => {
    const known = value.status === "known";
    const observed = known || value.status === "unknown";
    const expectedReason =
      value.status === "known"
        ? value.enabled === false
          ? "observation_paused"
          : null
        : RECONCILIATION_REASONS[value.status];
    if (
      value.reason !== expectedReason ||
      observed !== (value.observedAt !== null) ||
      known !== (value.gapsRecorded !== null) ||
      known !== (value.enabled !== null) ||
      (!observed && (value.lanes.length > 0 || value.detailTruncatedUntil !== null))
    ) {
      context.addIssue({ code: "custom", message: "reconciliation contradicts its status" });
    }
  });

const TRUST_KEY_STATUSES = {
  cache_absent: "unknown",
  cache_closed: "unknown",
  cache_expired: "expired",
  not_configured: "unknown",
} as const;

const trustKeys = z
  .strictObject({
    ageSeconds: z.number().int().nonnegative().safe().nullable(),
    keyCount: z.number().int().min(0).max(64),
    observedAt: dateTime,
    reason: z.enum(["cache_expired", "cache_absent", "cache_closed", "not_configured"]).nullable(),
    source: z.literal("github_actions_jwks_cache"),
    status: z.enum(["known", "expired", "unknown"]),
  })
  .superRefine((value, context) => {
    const cached = value.status !== "unknown";
    const expectedStatus = value.reason === null ? "known" : TRUST_KEY_STATUSES[value.reason];
    if (
      value.status !== expectedStatus ||
      cached !== value.keyCount > 0 ||
      cached !== (value.ageSeconds !== null)
    ) {
      context.addIssue({ code: "custom", message: "trust keys contradict their status" });
    }
  });

export const capabilityPassportSchema: z.ZodType<CapabilityPassport> = z
  .strictObject({
    actionsOidcKeys: trustKeys,
    installationGrant,
    nextAction: z.enum([
      "request_access",
      "repair_webhook",
      "refresh_evidence",
      "complete_configuration",
      "none",
    ]),
    observedAt: dateTime,
    ok: z.literal(true),
    productionAdmissionKeys,
    providerLimits,
    reconciliation,
    schemaVersion: z.literal("capability-passport/v1"),
    scope: z.strictObject({
      installationId: z.number().int().safe().positive(),
      repositoryId: z.number().int().safe().positive(),
    }),
    webhookObservation,
  })
  .superRefine((value, context) => {
    const installation = value.installationGrant.installation;
    if (installation !== null && installation.installationId !== value.scope.installationId) {
      context.addIssue({ code: "custom", message: "installation grant crosses passport scope" });
    }
    if (
      (value.providerLimits.status === "known") !==
      (value.installationGrant.reason === "rate_limited")
    ) {
      context.addIssue({ code: "custom", message: "provider limit contradicts the grant read" });
    }
    if (value.nextAction !== derivedNextAction(value)) {
      context.addIssue({ code: "custom", message: "next action contradicts the passport facts" });
    }
  });

// Mirrors the backend's closed derivation; webhook silence never yields a repair.
function derivedNextAction(
  passport: Pick<CapabilityPassport, "actionsOidcKeys" | "installationGrant" | "reconciliation">,
): CapabilityNextAction {
  const grant = passport.installationGrant;
  const reconciliation = passport.reconciliation;
  if (
    grant.status === "denied" ||
    reconciliation.status === "denied" ||
    (grant.installation !== null && grant.installation.state !== "active")
  ) {
    return "request_access";
  }
  if (grant.status === "unavailable" || reconciliation.status === "unavailable") {
    return "refresh_evidence";
  }
  if (reconciliation.status === "unknown" || reconciliation.enabled === false) {
    return "complete_configuration";
  }
  const keyReason = passport.actionsOidcKeys.reason;
  if (
    reconciliation.gapsRecorded === true ||
    keyReason === "cache_expired" ||
    keyReason === "cache_absent"
  ) {
    return "refresh_evidence";
  }
  return "none";
}

export const capabilityPassportErrorSchema: z.ZodType<CapabilityPassportError> = z.strictObject({
  error: z.enum(["unauthenticated", "forbidden", "unavailable"]),
  ok: z.literal(false),
});
