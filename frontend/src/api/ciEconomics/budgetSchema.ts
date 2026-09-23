import { z } from "zod";
import type { components } from "../generated";
import {
  counterSchema,
  reportMeasurementSchema,
  safeSigned,
  safeUnsigned,
} from "./measurementSchema";
import { canonicalUtcInstant } from "./schema";
import { digest, positiveInteger, sameScope, utcInstant } from "./sourceSchema";

export type BudgetPolicy = components["schemas"]["BudgetPolicyPayload"];
export type BudgetConfiguration = components["schemas"]["BudgetConfigurationPayload"];
export type BudgetPolicies = components["schemas"]["BudgetPoliciesResponse"];
export type BudgetMutation = components["schemas"]["BudgetPolicyMutationResponse"];
export type BudgetCommand = components["schemas"]["ConfigureBudgetPolicyBody"];
export type BudgetSignal = components["schemas"]["BudgetSignalResponse"];
export type BudgetSignals = components["schemas"]["BudgetSignalsResponse"];
export const budgetKey = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]*$/);
export const budgetOutcome = z.enum(["breached", "within_budget", "insufficient_evidence"]);
export const budgetConfigurationSchema: z.ZodType<BudgetConfiguration> = z.strictObject({
  enabled: z.boolean(),
  selector: z.strictObject({
    sampleKey: budgetKey,
    producerDigest: digest,
    method: z.literal("waited_children/v1"),
    runnerClassDigest: digest.nullable(),
  }),
  counter: counterSchema,
  maximumUs: safeUnsigned,
});
export const budgetPolicySchema: z.ZodType<BudgetPolicy> = z.strictObject({
  schemaVersion: z.literal("ci-economics-budget-policy/v1"),
  installationId: positiveInteger,
  repositoryId: positiveInteger,
  policyKey: budgetKey,
  revision: positiveInteger,
  configuration: budgetConfigurationSchema,
});
export const budgetCommandSchema: z.ZodType<BudgetCommand> = z.strictObject({
  installationId: positiveInteger,
  repositoryId: positiveInteger,
  policyKey: budgetKey,
  expectedRevision: safeUnsigned.max(Number.MAX_SAFE_INTEGER - 1),
  configuration: budgetConfigurationSchema,
  operationId: budgetKey,
});
export const budgetPoliciesSchema: z.ZodType<BudgetPolicies> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-budget-policies/v1"),
    installationId: positiveInteger,
    repositoryId: positiveInteger,
    policies: z.array(budgetPolicySchema).max(16),
    maximumPolicies: z.literal(16),
  })
  .refine((page) =>
    page.policies.every(
      (policy, index) =>
        sameScope(policy, page) &&
        (index === 0 ||
          (page.policies[index - 1]?.policyKey ?? policy.policyKey) < policy.policyKey),
    ),
  );
export const budgetMutationSchema: z.ZodType<BudgetMutation> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-budget-mutation/v1"),
    operationId: budgetKey,
    outcome: z.enum([
      "committed",
      "replayed",
      "revision_conflict",
      "operation_conflict",
      "capacity_reached",
    ]),
    policy: budgetPolicySchema.nullable(),
  })
  .refine(
    (value) =>
      (value.outcome === "committed" || value.outcome === "replayed") === (value.policy !== null),
  );
export const budgetSignalSchema: z.ZodType<BudgetSignal> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-budget-signal/v1"),
    signalId: digest,
    policy: budgetPolicySchema,
    policyDigest: digest,
    sourceId: digest,
    reportId: digest,
    reportDigest: digest,
    measurement: reportMeasurementSchema,
    commandExitCode: safeSigned,
    receivedAt: utcInstant,
    retainUntil: utcInstant,
    outcome: budgetOutcome,
  })
  .refine((value) => {
    const received = canonicalUtcInstant(value.receivedAt);
    const expiry = canonicalUtcInstant(value.retainUntil);
    const measurement = value.measurement;
    return (
      value.policy.configuration.enabled &&
      received !== undefined &&
      expiry !== undefined &&
      received < expiry &&
      measurement.counter === value.policy.configuration.counter &&
      (measurement.value === null
        ? value.outcome === "insufficient_evidence"
        : value.outcome ===
          (measurement.value > value.policy.configuration.maximumUs ? "breached" : "within_budget"))
    );
  });
export const budgetSignalsSchema: z.ZodType<BudgetSignals> = z
  .strictObject({
    schemaVersion: z.literal("ci-economics-budget-signals/v1"),
    installationId: positiveInteger,
    repositoryId: positiveInteger,
    items: z.array(budgetSignalSchema).max(100),
    nextCursor: digest.nullable(),
  })
  .refine(
    (page) =>
      page.items.every(
        (signal, index) =>
          sameScope(signal.policy, page) &&
          (index === 0 || (page.items[index - 1]?.signalId ?? signal.signalId) < signal.signalId),
      ) &&
      (page.nextCursor === null || page.nextCursor === page.items.at(-1)?.signalId),
  );

export async function signalIdentityIsCanonical(signal: BudgetSignal): Promise<boolean> {
  const policy = signal.policy;
  const config = policy.configuration;
  const policyDigest = await hash({
    configuration: {
      counter: config.counter,
      enabled: config.enabled,
      maximumUs: config.maximumUs,
      selector: {
        method: config.selector.method,
        producerDigest: config.selector.producerDigest,
        runnerClassDigest: config.selector.runnerClassDigest,
        sampleKey: config.selector.sampleKey,
      },
    },
    installationId: policy.installationId,
    policyKey: policy.policyKey,
    repositoryId: policy.repositoryId,
    revision: policy.revision,
    schemaVersion: policy.schemaVersion,
  });
  return (
    signal.policyDigest === policyDigest &&
    signal.signalId ===
      (await hash({
        policyDigest,
        reportId: signal.reportId,
        schemaVersion: "ci-economics-budget-signal-identity/v1",
      }))
  );
}

async function hash(canonicalObject: object): Promise<string> {
  const result = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(canonicalObject)),
  );
  return Array.from(new Uint8Array(result), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
