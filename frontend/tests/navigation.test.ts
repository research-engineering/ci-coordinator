import { expect, test } from "vitest";
import { ACTIVITY_SOURCES, ECONOMICS_TABS } from "../src/domain/consoleSelection";
import {
  consoleHref,
  EVIDENCE_TABS,
  REPOSITORY_VIEWS,
  readConsoleRoute,
  sameScope,
} from "../src/features/workbench/navigation";

const scope = { installationId: 7, repositoryId: 31, limit: 5 };
const coordinates = "installationId=7&repositoryId=31&limit=5";
const callback = `${coordinates}&repositoryAttestation=reviewed&proposalManifestId=proposal:one&reviewOperationId=review-one`;

test.each(ECONOMICS_TABS)("economics tab %s has one canonical active route", (economicsTab) => {
  const href = consoleHref({ scope, view: "economics", tab: "plans", economicsTab });
  const decoded = readConsoleRoute(new URL(href, "https://console.example").search);
  expect(decoded.economicsTab ?? "registered").toBe(economicsTab);
  expect(consoleHref(decoded)).toBe(href);
  expect(href.includes("economicsTab=")).toBe(economicsTab !== "registered");
});

test.each(ACTIVITY_SOURCES)("Activity source %s survives canonical routing", (activitySource) => {
  const href = consoleHref({ scope, view: "activity", tab: "plans", activitySource });
  const decoded = readConsoleRoute(new URL(href, "https://console.example").search);
  expect(decoded.activitySource ?? "security").toBe(activitySource);
  expect(consoleHref(decoded)).toBe(href);
  expect(href.includes("activitySource=")).toBe(activitySource !== "security");
});

test.each([
  [`${coordinates}&view=economics&economicsTab=unknown`, "economicsTab"],
  [`${coordinates}&view=economics&economicsTab=history&economicsTab=history`, "economicsTab"],
  [`${coordinates}&view=activity&economicsTab=history`, "economicsTab"],
  ["view=economics&economicsTab=history", "economicsTab"],
  [`${coordinates}&view=activity&activitySource=unknown`, "activitySource"],
  [
    `${coordinates}&view=activity&activitySource=business&activitySource=business`,
    "activitySource",
  ],
  [`${coordinates}&view=economics&activitySource=business`, "activitySource"],
  ["view=activity&activitySource=business", "activitySource"],
])("rejects invalid subview %s", (search, key) => {
  const result = readConsoleRoute(search);
  expect(result).not.toHaveProperty(key);
  expect(consoleHref(result)).not.toContain(`${key}=`);
});

test.each([undefined, scope])("global Activity preserves optional scope %#", (selected) => {
  const href = consoleHref({ scope: selected, view: "activity", tab: "plans" });
  expect(readConsoleRoute(new URL(href, "https://console.example").search)).toEqual({
    scope: selected,
    view: "activity",
    tab: "plans",
  });
});

test.each(REPOSITORY_VIEWS.flatMap((view) => EVIDENCE_TABS.map((tab) => ({ view, tab }))))(
  "round-trips $view / $tab without changing repository identity",
  ({ view, tab }) => {
    const href = consoleHref({ scope, view, tab });
    expect(href.startsWith("/workbench?")).toBe(true);
    expect(readConsoleRoute(new URL(href, "https://console.example").search)).toEqual({
      scope,
      view,
      tab: view === "overview" ? tab : "plans",
    });
  },
);

test.each([
  "",
  "installationId=7&repositoryId=31",
  "installationId=0&repositoryId=31&limit=5",
  "installationId=7&repositoryId=-1&limit=5",
  "installationId=7&repositoryId=31&limit=0",
  "installationId=7&repositoryId=31&limit=9007199254740992",
  "installationId=7&repositoryId=NaN&limit=5",
  `${coordinates}&installationId=8`,
  `${coordinates}&repositoryId=31`,
  `${coordinates}&limit=5`,
])("rejects absent, invalid or ambiguous scope before navigation: %s", (search) => {
  expect(readConsoleRoute(`${search}&view=workflows`).scope).toBeUndefined();
  expect(readConsoleRoute(`${search}&view=workflows`).view).toBe("repositories");
});

test.each(["", "&view=unknown", "&view=//foreign.example", "&view=audit&view=workflows"])(
  "unrecognized task selection falls back without gaining authority: %s",
  (suffix) => {
    expect(readConsoleRoute(coordinates + suffix)).toEqual({
      scope,
      view: "overview",
      tab: "plans",
    });
  },
);

test.each(["unknown", "runs&tab=configEpochs"])("rejects ambiguous or unknown tab %s", (tab) => {
  expect(readConsoleRoute(`${coordinates}&view=overview&tab=${tab}`).tab).toBe("plans");
});

test("catalog URLs preserve only an admitted selected scope", () => {
  expect(readConsoleRoute(`${coordinates}&view=repositories`)).toEqual({
    scope,
    view: "repositories",
    tab: "plans",
  });
  expect(consoleHref({ scope: undefined, view: "workflows", tab: "runs" })).toBe("/workbench");
  expect(consoleHref({ scope: { ...scope, limit: 0 }, view: "overview", tab: "runs" })).toBe(
    "/workbench",
  );
});

test("exact callback hints select workflows but survive all same-scope views only as hints", () => {
  expect(readConsoleRoute(callback).view).toBe("workflows");
  for (const view of ["repositories", ...REPOSITORY_VIEWS] as const) {
    const href = consoleHref({ scope, view, tab: "plans" }, callback);
    const query = new URL(href, "https://console.example").searchParams;
    expect(query.get("reviewOperationId")).toBe("review-one");
    expect(query.get("proposalManifestId")).toBe("proposal:one");
    expect(readConsoleRoute(query.toString()).view).toBe(view);
  }
});

test.each([
  ["installationId", "8"],
  ["repositoryId", "32"],
  ["repositoryAttestation", "unreviewed"],
  ["reviewOperationId", ""],
  ["reviewOperationId", "nul\0id"],
  ["reviewOperationId", "x".repeat(257)],
] as const)("a mismatching callback %s cannot be carried to this repository", (key, value) => {
  const query = new URLSearchParams(callback);
  query.set(key, value);
  expect(consoleHref({ scope, view: "workflows", tab: "plans" }, query.toString())).not.toContain(
    "reviewOperationId",
  );
});

test("preserves an opaque admitted operation ID without imposing UUID syntax", () => {
  const query = new URLSearchParams(callback);
  query.set("reviewOperationId", "opaque review id");
  const href = consoleHref({ scope, view: "workflows", tab: "plans" }, query.toString());
  expect(new URL(href, "https://console.example").searchParams.get("reviewOperationId")).toBe(
    "opaque review id",
  );
});

test.each([
  "installationId",
  "repositoryId",
  "repositoryAttestation",
  "proposalManifestId",
  "reviewOperationId",
])("missing or duplicated callback field %s is not retained", (key) => {
  const query = new URLSearchParams(callback);
  query.delete(key);
  expect(consoleHref({ scope, view: "workflows", tab: "plans" }, query.toString())).not.toContain(
    "reviewOperationId",
  );
  const duplicated = new URLSearchParams(callback);
  duplicated.append(key, duplicated.get(key) ?? "");
  expect(
    consoleHref({ scope, view: "workflows", tab: "plans" }, duplicated.toString()),
  ).not.toContain("reviewOperationId");
});

test("scope identity includes every admitted coordinate", () => {
  expect(sameScope(undefined, undefined)).toBe(true);
  expect(sameScope(scope, { ...scope })).toBe(true);
  expect(sameScope(scope, undefined)).toBe(false);
  expect(sameScope(undefined, scope)).toBe(false);
  for (const coordinate of ["installationId", "repositoryId", "limit"] as const) {
    expect(sameScope(scope, { ...scope, [coordinate]: scope[coordinate] + 1 })).toBe(false);
  }
});
