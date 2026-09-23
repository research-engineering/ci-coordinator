import { createHash } from "node:crypto";
import type {
  BudgetCommand,
  BudgetPolicies,
  BudgetPolicy,
  BudgetSignal,
  BudgetSignals,
} from "../src/api/ciEconomics/budgetSchema";
import { ECONOMICS_SCOPE, economicsReport } from "./economicsConsoleFixture";

export function budgetPolicy(configuration = economicsReport().payload): BudgetPolicy {
  return {
    schemaVersion: "ci-economics-budget-policy/v1",
    ...ECONOMICS_SCOPE,
    policyKey: "backend-cpu",
    revision: 1,
    configuration: {
      enabled: true,
      selector: {
        sampleKey: configuration.sampleKey,
        producerDigest: configuration.producerDigest,
        method: configuration.method,
        runnerClassDigest: null,
      },
      counter: "cpu_user",
      maximumUs: 20,
    },
  };
}
export function budgetCommand(policy = budgetPolicy()): BudgetCommand {
  return {
    ...ECONOMICS_SCOPE,
    operationId: "configure-budget",
    policyKey: policy.policyKey,
    expectedRevision: policy.revision - 1,
    configuration: policy.configuration,
  };
}
export function budgetPolicies(policies = [budgetPolicy()]): BudgetPolicies {
  return {
    schemaVersion: "ci-economics-budget-policies/v1",
    ...ECONOMICS_SCOPE,
    policies,
    maximumPolicies: 16,
  };
}
export function budgetSignal(policy = budgetPolicy()): BudgetSignal {
  const report = economicsReport();
  const measurement = report.payload.measurements.find(
    (item) => item.counter === policy.configuration.counter,
  );
  if (!measurement) throw new Error("budget fixture counter absent");
  const policyDigest = hash(policy);
  return {
    schemaVersion: "ci-economics-budget-signal/v1",
    signalId: hash({
      schemaVersion: "ci-economics-budget-signal-identity/v1",
      policyDigest,
      reportId: report.reportId,
    }),
    policy,
    policyDigest,
    sourceId: report.source.sourceId,
    reportId: report.reportId,
    reportDigest: report.reportDigest,
    measurement,
    commandExitCode: report.payload.commandExitCode,
    receivedAt: report.receivedAt,
    retainUntil: report.retainUntil,
    outcome:
      measurement.value === null
        ? "insufficient_evidence"
        : measurement.value > policy.configuration.maximumUs
          ? "breached"
          : "within_budget",
  };
}
export function budgetSignals(items = [budgetSignal()]): BudgetSignals {
  return {
    schemaVersion: "ci-economics-budget-signals/v1",
    ...ECONOMICS_SCOPE,
    items,
    nextCursor: null,
  };
}
function hash(value: object): string {
  const canonical = (item: unknown): unknown =>
    Array.isArray(item)
      ? item.map(canonical)
      : item !== null && typeof item === "object"
        ? Object.fromEntries(
            Object.entries(item)
              .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
              .map(([key, nested]) => [key, canonical(nested)]),
          )
        : item;
  return createHash("sha256")
    .update(JSON.stringify(canonical(value)))
    .digest("hex");
}
