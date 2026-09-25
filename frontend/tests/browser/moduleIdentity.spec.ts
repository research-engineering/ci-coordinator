import { expect, type Page, test } from "@playwright/test";
import { analyticsQuerySchema } from "../../src/api/ciEconomics/analyticsSchema";
import { activityPage, activityRequest } from "../activityFixture";
import { analyticsReport } from "../analyticsFixture";
import { configHeaders, configStatus } from "../configurationFixture";
import { economicsSourcePage } from "../economicsConsoleFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../fixture";
import { historyStatus } from "../historyFixture";
import { purposeSnapshot } from "../purposeSettingsFixture";

test("the mounted production bundle evaluates its entry once and retains drafts across lazy imports", async ({
  page,
}) => {
  const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!base) throw new Error("Browser server unavailable");
  const errors: string[] = [];
  const unexpected: string[] = [];
  const assets: { path: string; status: number; redirected: boolean }[] = [];
  let sessionReads = 0;
  let catalogReads = 0;
  let snapshotReads = 0;
  const session = controlPlaneSessionFixture({ roles: ["audit", "configure", "read"] });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/assets/"))
      assets.push({
        path: url.pathname,
        status: response.status(),
        redirected: response.request().redirectedFrom() !== null,
      });
  });
  await page.route("**/api/**", (route) => {
    unexpected.push(route.request().url());
    return route.fulfill({ status: 404, json: { ok: false } });
  });
  await page.route("**/api/v1/auth/session", (route) => {
    sessionReads += 1;
    return route.fulfill({ json: session });
  });
  await page.route("**/api/v1/workbench/installations?*", (route) => {
    catalogReads += 1;
    return route.fulfill({ json: installationCatalogFixture() });
  });
  await page.route("**/api/v1/workbench/installations/1/repositories?*", (route) =>
    route.fulfill({ json: repositoryPageFixture() }),
  );
  await page.route("**/api/v1/workbench/repositories/1/1?*", (route) => {
    snapshotReads += 1;
    return route.fulfill({ json: workbenchFixture() });
  });
  await page.route("**/api/v1/config/repositories/1/1/status?*", (route) =>
    route.fulfill({ json: configStatus(), headers: configHeaders }),
  );
  await page.route("**/api/v1/activity/**", (route) =>
    route.fulfill({ json: activityPage(activityRequest(new URL(route.request().url())).query) }),
  );
  await page.route("**/api/v2/economics/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/history")) return route.fulfill({ json: historyStatus() });
    if (url.pathname.endsWith("/analytics/settings"))
      return route.fulfill({
        json: { outcome: "available", snapshot: purposeSnapshot(), unavailable: null },
      });
    if (url.pathname.endsWith("/history/analytics")) {
      const query = analyticsQuerySchema.parse({
        installationId: 1,
        repositoryId: 1,
        generation: 1,
        createdFrom: url.searchParams.get("createdFrom"),
        createdUntil: url.searchParams.get("createdUntil"),
        horizonDays: Number(url.searchParams.get("horizonDays")),
      });
      return route.fulfill({
        json: { outcome: "available", report: analyticsReport(query), unavailable: null },
      });
    }
    return route.fulfill({ json: economicsSourcePage([]) });
  });
  const debuggerSession = await page.context().newCDPSession(page);
  await debuggerSession.send("Profiler.enable");
  await debuggerSession.send("Profiler.startPreciseCoverage", { callCount: true, detailed: true });
  try {
    const shell = await page.goto(new URL("/workbench", base).href);
    expect(shell?.headers()["cache-control"]).toBe("no-store");
    expect(shell?.headers()["content-security-policy"]).toContain("base-uri 'none'");
    const entries = await page
      .locator('script[type="module"][src]')
      .evaluateAll((scripts) => scripts.map((script) => (script as HTMLScriptElement).src));
    expect(entries).toHaveLength(1);
    const entry = entries[0];
    if (!entry) throw new Error("Production module entry missing");
    const namespace = new URL(entry).pathname.match(/^\/assets\/_bundle\/[a-f0-9]{64}\//)?.[0];
    expect(namespace).toBeDefined();
    const entryName = new URL(entry).pathname.split("/").at(-1);
    const filter = page.getByRole("searchbox", { name: "Search loaded page" });
    await expect(page.getByRole("button", { name: "Open", exact: true })).toBeVisible();
    await filter.fill("ci-coordinator");
    await page.getByRole("button", { name: "Open", exact: true }).click();
    await navigate(page, "Configuration");
    const draft = '{"unsubmitted": "keep across lazy navigation"}';
    await page.getByRole("textbox", { name: "Source", exact: true }).fill(draft);
    await navigate(page, "Activity");
    await expect(page.getByRole("table", { name: "Retained access events" })).toBeVisible();
    await navigate(page, "Repositories");
    await expect(filter).toHaveValue("ci-coordinator");
    await navigate(page, "Configuration");
    await expect(page.getByRole("textbox", { name: "Source", exact: true })).toHaveValue(draft);
    await navigate(page, "CI economics");
    await page.getByRole("tab", { name: "Analytics", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Retained runner usage" })).toBeVisible();
    await navigate(page, "Repositories");
    await expect(filter).toHaveValue("ci-coordinator");
    await navigate(page, "Configuration");
    await expect(page.getByRole("textbox", { name: "Source", exact: true })).toHaveValue(draft);

    const coverage = await debuggerSession.send("Profiler.takePreciseCoverage");
    const entryEvaluations = coverage.result.filter((script) =>
      script.url.endsWith(`/${entryName}`),
    );
    expect(entryEvaluations).toHaveLength(1);
    expect(entryEvaluations[0]?.url).toBe(entry);
    expect(entryEvaluations[0]?.functions[0]?.ranges[0]?.count).toBe(1);
    expect(assets.some(({ path }) => /ActivityPanel.*\.js$/.test(path))).toBe(true);
    expect(assets.some(({ path }) => /AnalyticsPanel.*\.js$/.test(path))).toBe(true);
    expect(
      assets.every(
        ({ path, status, redirected }) =>
          path.startsWith(namespace ?? "invalid") && status === 200 && !redirected,
      ),
    ).toBe(true);
    expect(sessionReads).toBe(1);
    expect(catalogReads).toBe(1);
    expect(snapshotReads).toBe(1);
    expect(errors).toEqual([]);
    expect(unexpected).toEqual([]);
  } finally {
    await debuggerSession.send("Profiler.stopPreciseCoverage");
    await debuggerSession.detach();
  }
});

async function navigate(page: Page, name: string) {
  const link = page.getByRole("link", { name, exact: true });
  if (!(await link.isVisible()))
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  await link.click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
}
