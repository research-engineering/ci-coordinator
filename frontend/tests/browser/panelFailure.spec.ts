import { expect, type Page, test } from "@playwright/test";
import { configHeaders, configStatus } from "../configurationFixture";
import { economicsSourcePage } from "../economicsConsoleFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../fixture";
import { historyStatus } from "../historyFixture";
import { observationWorkflows } from "../observationFixture";

for (const [panel, module] of [
  ["Activity", "ActivityPanel"],
  ["Analytics", "AnalyticsPanel"],
  ["Archive", "ArchiveBrowser"],
  ["Archive gaps", "ArchiveBrowser"],
] as const) {
  test(`${panel} cold chunk failure preserves sibling drafts until explicit reload`, async ({
    page,
  }) => {
    const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
    if (!base) throw new Error("Browser server unavailable");
    const session = controlPlaneSessionFixture({ roles: ["audit", "configure", "read"] });
    const release = Promise.withResolvers<void>();
    const missingChunks: string[] = [];
    const unexpected: string[] = [];
    const writes: string[] = [];
    const errors: string[] = [];
    let documents = 0;
    let intercepted = 0;
    page.on("request", (request) => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame()) documents += 1;
      if (request.method() !== "GET") writes.push(request.url());
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/**", (route) => {
      unexpected.push(route.request().url());
      return route.fulfill({ status: 404, json: { ok: false } });
    });
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: session }));
    await page.route("**/api/v1/workbench/installations?*", (route) =>
      route.fulfill({ json: installationCatalogFixture() }),
    );
    await page.route("**/api/v1/workbench/installations/1/repositories?*", (route) =>
      route.fulfill({ json: repositoryPageFixture() }),
    );
    await page.route("**/api/v1/workbench/repositories/1/1?*", (route) =>
      route.fulfill({ json: workbenchFixture() }),
    );
    await page.route("**/api/v1/config/repositories/1/1/status?*", (route) =>
      route.fulfill({ json: configStatus(), headers: configHeaders }),
    );
    await page.route("**/api/v2/economics/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/history")) return route.fulfill({ json: historyStatus(null) });
      if (path.endsWith("/workflows")) return route.fulfill({ json: observationWorkflows() });
      if (path.endsWith("/sources")) return route.fulfill({ json: economicsSourcePage([]) });
      unexpected.push(route.request().url());
      return route.fulfill({ status: 404, json: { ok: false } });
    });
    await page.route(new RegExp(`/assets/.*${module}-[^/]+\\.js$`), async (route) => {
      intercepted += 1;
      const url = new URL(route.request().url());
      const currentBundle = url.pathname.match(/^\/assets\/_bundle\/([a-f0-9]{64})\//)?.[1];
      expect(currentBundle).toBeDefined();
      if (!currentBundle) throw new Error("Chunk request did not use the production namespace");
      const absentBundle = (currentBundle[0] === "0" ? "1" : "0") + currentBundle.slice(1);
      url.pathname = url.pathname.replace(currentBundle, absentBundle);
      await release.promise;
      // The failure body/status comes from the actual ASGI mount, not a fake asset server.
      const missing = await route.fetch({ url: url.href });
      expect(missing.status()).toBe(404);
      expect(missing.headers()["cache-control"]).toBe("no-store");
      missingChunks.push(url.href);
      await route.fulfill({ response: missing });
    });

    try {
      const shell = await page.goto(new URL("/workbench", base).href);
      expect(shell?.headers()["cache-control"]).toBe("no-store");
      expect(shell?.headers()["content-security-policy"]).toContain("base-uri 'none'");
      await expect(page.locator('script[type="module"][src]')).toHaveAttribute(
        "src",
        /^\/assets\/_bundle\/[a-f0-9]{64}\//,
      );
      await expect(page.getByRole("button", { name: "Open", exact: true })).toBeVisible();
      await page.getByRole("searchbox", { name: "Search loaded page" }).fill("ci-coordinator");
      await page.getByRole("button", { name: "Open", exact: true }).click();
      await navigate(page, "Configuration");
      const source = page.getByRole("textbox", { name: "Source", exact: true });
      const draft = '{"unsubmitted": "preserve until explicit reload"}';
      await source.fill(draft);
      if (panel === "Activity") await navigate(page, "Activity");
      else {
        await navigate(page, "CI economics");
        await page
          .getByRole("tab", {
            name: module === "ArchiveBrowser" ? "History" : "Analytics",
            exact: true,
          })
          .click();
      }
      await expect.poll(() => intercepted).toBe(1);
      if (module === "ArchiveBrowser") {
        await page.getByLabel("Import runs created since (UTC)").fill("2020-01-01");
        await page
          .getByRole("radio", {
            name: panel === "Archive" ? "Retained runs" : "Collection gaps",
            exact: true,
          })
          .check();
      }
      release.resolve();
      await expect(
        page.getByRole("heading", { name: `${panel} unavailable`, level: 2 }),
      ).toBeVisible();
      expect(missingChunks).toHaveLength(1);
      await expect(page.getByRole("main")).toHaveCount(1);
      await expect(page.getByRole("heading", { name: "Console unavailable" })).toHaveCount(0);
      await expect(
        page
          .getByText("Refreshing discards unsaved work in this tab.", { exact: true })
          .filter({ visible: true }),
      ).toHaveCount(1);
      await expect(
        page
          .getByText("Check the current state before repeating an interrupted action.", {
            exact: true,
          })
          .filter({ visible: true }),
      ).toHaveCount(1);
      if (module === "ArchiveBrowser")
        await expect(page.getByLabel("Import runs created since (UTC)")).toHaveValue("2020-01-01");
      expect(documents).toBe(1);
      await navigate(page, "Configuration");
      await expect(source).toHaveValue(draft);
      await navigate(page, "Repositories");
      await expect(page.getByRole("searchbox", { name: "Search loaded page" })).toHaveValue(
        "ci-coordinator",
      );
      if (panel === "Activity") await navigate(page, "Activity");
      else await navigate(page, "CI economics");
      await expect(
        page.getByRole("heading", { name: `${panel} unavailable`, level: 2 }),
      ).toBeVisible();
      expect(intercepted).toBe(1);
      expect(documents).toBe(1);
      expect(writes).toEqual([]);

      const location = page.url();
      await Promise.all([
        page.waitForEvent("load"),
        page.getByRole("button", { name: "Refresh console", exact: true }).click(),
      ]);
      await expect(page).toHaveURL(location);
      if (module === "ArchiveBrowser")
        await page
          .getByRole("radio", {
            name: panel === "Archive" ? "Retained runs" : "Collection gaps",
            exact: true,
          })
          .check();
      await expect(
        page.getByRole("heading", { name: `${panel} unavailable`, level: 2 }),
      ).toBeVisible();
      expect(documents).toBe(2);
      expect(missingChunks).toHaveLength(2);
      expect(writes).toEqual([]);
      expect(unexpected).toEqual([]);
      expect(errors).toEqual([]);
    } finally {
      release.resolve();
    }
  });
}

async function navigate(page: Page, name: string) {
  const link = page.getByRole("link", { name, exact: true });
  if (!(await link.isVisible()))
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  await link.click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
}
