import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type {
  ObservationCommand,
  ObservationSnapshot,
} from "../../src/api/ciEconomics/observationSchema";
import { economicsSourcePage } from "../economicsConsoleFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../fixture";
import {
  observationGaps,
  observationMutation,
  observationStatus,
  observationWorkflows,
} from "../observationFixture";

test("observation configures exact provider workflows, survives reload and pauses without removing evidence", async ({
  page,
}) => {
  let snapshot: ObservationSnapshot | null = null;
  const commands: ObservationCommand[] = [];
  let writesWithLostReply = 1;
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
      expect(path).toBe("/api/v2/economics/observation");
      const command: ObservationCommand = request.postDataJSON();
      commands.push(command);
      const result = observationMutation(command);
      snapshot = result.snapshot;
      if (writesWithLostReply-- > 0) return route.abort("failed");
      return route.fulfill({
        json: { ...result, outcome: commands.length === 2 ? "replayed" : "committed" },
      });
    }
    const status = observationStatus(snapshot);
    const window = { createdFrom: "2026-09-10T09:00:00Z", createdThrough: "2026-09-10T10:00:00Z" };
    const observedStatus = {
      ...status,
      scans: status.scans.map((scan) => ({
        ...scan,
        interval: window,
        window,
        cycleStartedAt: status.observedAt,
        pageNumber: 1,
        leaseExpiresAt: "2026-09-10T10:01:00Z",
      })),
    };
    const json = path.endsWith("/workflows")
      ? observationWorkflows()
      : path.endsWith("/gaps")
        ? observationGaps()
        : path.endsWith("/observation")
          ? observedStatus
          : economicsSourcePage();
    return route.fulfill({ json });
  });
  const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!base) throw new Error("Browser server unavailable");
  await page.goto(
    new URL("/workbench?installationId=1&repositoryId=1&limit=10&view=economics", base).href,
  );
  await page.getByRole("tab", { name: "Observation", exact: true }).click();
  await expect(page.getByText("Not configured", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "Observation enabled" }).check();
  await page.getByRole("combobox", { name: "Workflows", exact: true }).selectOption("selected");
  await page.getByRole("checkbox", { name: /Full Check/ }).check();
  await page.getByRole("combobox", { name: "Initial history" }).selectOption("6");
  await page.getByRole("button", { name: "Save observation" }).click();
  await expect(page.getByText(/Save outcome unknown/)).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Observation enabled" })).toBeDisabled();
  await page.getByRole("button", { name: "Retry same operation" }).click();
  await expect(page.getByText("Saved revision 1", { exact: true })).toBeVisible();
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
  expect(commands[0]?.configuration).toEqual({
    enabled: true,
    selector: { kind: "selected", workflowIds: [101] },
    backfillDays: 6,
  });
  await page.reload();
  await page.getByRole("tab", { name: "Observation", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Observation enabled" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /Full Check/ })).toBeChecked();
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const indicator = page.locator(".observation-lane-state--claimed svg").first();
  await expect(indicator).toHaveCSS("animation-name", "observation-scan-search");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(indicator).toHaveCSS("animation-name", "none");
  await page.screenshot({
    path: test.info().outputPath("observation-claimed.png"),
    fullPage: true,
  });
  await page.getByRole("checkbox", { name: "Observation enabled" }).uncheck();
  await page.getByRole("button", { name: "Save observation" }).click();
  await expect(page.getByText("Saved revision 2", { exact: true })).toBeVisible();
  expect(commands.at(-1)?.configuration.enabled).toBe(false);
  await expect(page.locator(".observation-lane-state--claimed")).toHaveCount(0);
  await page.getByText("Coverage gaps", { exact: true }).click();
  await expect(page.getByText("provider truncated", { exact: true })).toBeVisible();
  const size = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    width: document.documentElement.clientWidth,
  }));
  expect(size.scroll).toBeLessThanOrEqual(size.width);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: test.info().outputPath("observation-paused.png"), fullPage: true });
  await page.getByRole("tab", { name: "Registered runs", exact: true }).click();
  await expect(page.getByRole("button", { name: "Inspect run 4201, attempt 2" })).toBeVisible();
});
