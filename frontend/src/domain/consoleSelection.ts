export const ECONOMICS_TABS = [
  "registered",
  "discover",
  "reconciled",
  "observation",
  "history",
  "analytics",
  "budgets",
  "signals",
] as const;
export type EconomicsTab = (typeof ECONOMICS_TABS)[number];

export const ACTIVITY_SOURCES = ["security", "business"] as const;
export type ActivitySource = (typeof ACTIVITY_SOURCES)[number];
