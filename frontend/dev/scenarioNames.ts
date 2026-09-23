export const SCENARIOS = {
  populated: "Populated portfolio",
  empty: "Empty portfolio",
  unauthorized: "Unauthorized access",
  "provider-failure-retry": "Provider failure and retry",
  runs: "Successful and failed retained runs",
  economics: "Economics report comparison",
  "stale-navigation": "Late response after repository navigation",
} as const;

export type ScenarioName = keyof typeof SCENARIOS;
export const DEMO_TIME = "2026-09-08T12:00:00.000Z";

export function scenarioName(value: string): ScenarioName {
  if (!Object.hasOwn(SCENARIOS, value)) throw new Error(`Unknown synthetic scenario: ${value}`);
  return value as ScenarioName;
}
