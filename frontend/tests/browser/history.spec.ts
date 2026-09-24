import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { HistoryCommand, HistoryDataset } from "../../src/api/ciEconomics/historySchema";
import { economicsSourcePage } from "../economicsConsoleFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../fixture";
import { historyMutation, historyStatus } from "../historyFixture";
import { observationWorkflows } from "../observationFixture";

test("catalog creation date offers an explicit full-range shortcut without a write", async ({
  page,
}) => {
  let writes = 0;
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: controlPlaneSessionFixture({ roles: ["audit", "configure", "read"] }) }),
  );
  await page.route("**/api/v1/workbench/installations?*", (route) =>
    route.fulfill({ json: installationCatalogFixture() }),
  );
  await page.route("**/api/v1/workbench/installations/1/repositories?*", (route) =>
    route.fulfill({ json: repositoryPageFixture() }),
  );
  await page.route("**/api/v1/workbench/repositories/1/1?*", (route) =>
    route.fulfill({ json: workbenchFixture() }),
  );
  await page.route("**/api/v1/workbench/repositories/1/2?*", (route) =>
    route.fulfill({ json: workbenchFixture({ scope: { installationId: 1, repositoryId: 2 } }) }),
  );
  await page.route("**/api/v2/economics/**", (route) => {
    if (route.request().method() === "POST") writes += 1;
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({
      json: {
        ...historyStatus(null),
        repositoryId: path.includes("/repositories/1/2/") ? 2 : 1,
      },
    });
  });
  const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!base) throw new Error("Browser server unavailable");
  await page.goto(new URL("/workbench", base).href);
  await page.getByRole("button", { name: "Open" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  const economics = page.getByRole("link", { name: "CI economics" });
  if (!(await economics.isVisible()))
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  await economics.click();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(page.getByLabel("Import runs created since (UTC)")).toHaveValue("");
  await page.getByRole("button", { name: "Use repository creation date (2020-01-01)" }).click();
  await expect(page.getByLabel("Import runs created since (UTC)")).toHaveValue("2020-01-01");
  await expect(page.getByRole("button", { name: "Save history" })).toBeEnabled();
  await page.screenshot({
    path: test.info().outputPath("creation-date-shortcut.png"),
    fullPage: true,
  });
  expect(writes).toBe(0);
  await page.evaluate(() => {
    history.pushState(
      null,
      "",
      "/workbench?installationId=1&repositoryId=2&limit=10&view=economics&economicsTab=history",
    );
    dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.getByLabel("Import runs created since (UTC)")).toHaveValue("");
  await expect(page.getByRole("button", { name: /Use repository creation date/ })).toHaveCount(0);
});

test("historical collection is explicit, replay-safe, accessible and responsive", async ({
  page,
}) => {
  let snapshot: HistoryDataset | null = null;
  const commands: HistoryCommand[] = [];
  let holdRead: Promise<void> | undefined;
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: controlPlaneSessionFixture({ roles: ["audit", "configure", "read"] }) }),
  );
  await page.route("**/api/v1/workbench/installations?*", (route) =>
    route.fulfill({ json: installationCatalogFixture() }),
  );
  await page.route("**/api/v1/workbench/installations/1/repositories?*", (route) =>
    route.fulfill({ json: repositoryPageFixture() }),
  );
  await page.route("**/api/v1/workbench/repositories/1/1?*", (route) =>
    route.fulfill({ json: workbenchFixture() }),
  );
  await page.route("**/api/v2/economics/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      expect(path).toBe("/api/v2/economics/history");
      const command: HistoryCommand = request.postDataJSON();
      commands.push(command);
      const result = historyMutation(command);
      snapshot = result.snapshot;
      if (commands.length === 1) return route.abort("failed");
      return route.fulfill({
        json: { ...result, outcome: commands.length === 2 ? "replayed" : "committed" },
      });
    }
    if (path.endsWith("/history")) {
      await holdRead;
      return route.fulfill({ json: historyStatus(snapshot) });
    }
    return route.fulfill({
      json: path.endsWith("/workflows") ? observationWorkflows() : economicsSourcePage(),
    });
  });
  const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!base) throw new Error("Browser server unavailable");
  await page.goto(
    new URL("/workbench?installationId=1&repositoryId=1&limit=10&view=economics", base).href,
  );
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(page.getByText("No historical collection configured.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save history" })).toBeDisabled();
  await page.getByLabel("Import runs created since (UTC)").fill("2020-01-01");
  await page.getByRole("checkbox", { name: "Collection enabled" }).check();
  await page.getByRole("combobox", { name: "Workflows", exact: true }).selectOption("selected");
  await page.getByRole("checkbox", { name: /Full Check/ }).check();
  await page.getByText("Retention and capacity limits", { exact: true }).click();
  await page.getByRole("combobox", { name: "Detailed records" }).selectOption("forever");
  await page.getByRole("button", { name: "Save history" }).click();
  await expect(page.getByText(/Save outcome unknown/)).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Collection enabled" })).toBeDisabled();
  await page.getByRole("tab", { name: "Registered runs", exact: true }).click();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Collection enabled" })).toBeDisabled();
  await page.getByRole("button", { name: "Retry same operation" }).click();
  await expect(page.getByText(/Saved revision 1\./)).toBeVisible();
  expect(commands).toHaveLength(2);
  expect(commands[0]).toEqual(commands[1]);
  expect(commands[0]?.configuration.workflowIds).toEqual([101]);
  expect(commands[0]?.configuration.detailRetention).toEqual({ mode: "forever" });
  await page.reload();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await page.getByRole("radio", { name: "Collection settings", exact: true }).check();
  await expect(page.getByRole("checkbox", { name: "Collection enabled" })).toBeChecked();
  await expect(page.getByText("Recorded coverage gaps", { exact: true })).toBeVisible();
  const readBarrier = Promise.withResolvers<void>();
  holdRead = readBarrier.promise;
  await page
    .getByRole("region", { name: "Historical Actions archive" })
    .getByRole("button", { name: "Refresh evidence", exact: true })
    .first()
    .click();
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const indicator = page.locator(".history-refreshing");
  await expect(indicator).toHaveCSS("animation-name", "history-refresh");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(indicator).toHaveCSS("animation-name", "none");
  await page.screenshot({ path: test.info().outputPath("history-refresh.png"), fullPage: true });
  holdRead = undefined;
  readBarrier.resolve();
  await expect(indicator).toHaveCount(0);
  await page.getByRole("checkbox", { name: "Collection enabled" }).uncheck();
  await page.getByRole("button", { name: "Save history" }).click();
  await expect(page.getByText(/Saved revision 2\./)).toBeVisible();
  await page.getByRole("checkbox", { name: "Rescan the configured history" }).check();
  await page.getByRole("button", { name: "Save history" }).click();
  await expect(page.getByText(/Saved revision 3\./)).toBeVisible();
  expect(commands.at(-1)?.rescan).toBe(true);
  expect(commands.at(-1)?.configuration.enabled).toBe(false);
  await page.getByText("Scan and storage details", { exact: true }).click();
  await expect(page.getByText("Effective detail policy", { exact: true })).toBeVisible();
  const size = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    width: document.documentElement.clientWidth,
  }));
  expect(size.scroll).toBeLessThanOrEqual(size.width);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: test.info().outputPath("history-paused.png"), fullPage: true });
  await page.getByRole("tab", { name: "History", exact: true }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: "Observation", exact: true })).toBeFocused();
});
