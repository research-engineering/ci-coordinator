import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { analyticsQuerySchema } from "../../src/api/ciEconomics/analyticsSchema";
import {
  type PurposeSettingsCommand,
  purposeSettingsCommandSchema,
} from "../../src/api/ciEconomics/purposeSettingsSchema";
import { analyticsReport } from "../analyticsFixture";
import { economicsSourcePage } from "../economicsConsoleFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../fixture";
import { historyStatus } from "../historyFixture";
import { purposeSnapshot } from "../purposeSettingsFixture";

for (const width of [1280, 390])
  test(`purpose settings retain a lost-response operation at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({
        json: controlPlaneSessionFixture({ roles: ["audit", "configure", "read"] }),
      }),
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
    const commands: PurposeSettingsCommand[] = [];
    let snapshot = purposeSnapshot();
    await page.route("**/api/v2/economics/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname.endsWith("/analytics/settings")) {
        if (request.method() === "PUT") {
          const command = purposeSettingsCommandSchema.parse(request.postDataJSON());
          commands.push(command);
          snapshot = purposeSnapshot(command);
          if (commands.length === 1) return route.abort("failed");
          expect(command).toEqual(commands[0]);
          expect(request.headers()["x-csrf-token"]).toBe("c".repeat(43));
          return route.fulfill({
            json: { operationId: command.operationId, outcome: "replayed", snapshot },
          });
        }
        return route.fulfill({ json: { outcome: "available", snapshot, unavailable: null } });
      }
      expect(request.method()).toBe("GET");
      if (url.pathname.endsWith("/history")) return route.fulfill({ json: historyStatus() });
      if (url.pathname.endsWith("/analytics")) {
        const query = analyticsQuerySchema.parse({
          installationId: 1,
          repositoryId: 1,
          generation: 1,
          createdFrom: url.searchParams.get("createdFrom"),
          createdUntil: url.searchParams.get("createdUntil"),
          purpose: url.searchParams.get("purpose"),
          horizonDays: Number(url.searchParams.get("horizonDays")),
        });
        return route.fulfill({
          json: { outcome: "available", report: analyticsReport(query), unavailable: null },
        });
      }
      return route.fulfill({ json: economicsSourcePage() });
    });
    const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
    if (!base) throw new Error("Browser server unavailable");
    await page.goto(
      new URL("/workbench?installationId=1&repositoryId=1&limit=10&view=economics", base).href,
    );
    await page.getByRole("tab", { name: "Analytics", exact: true }).click();
    await page.getByText("Analytics settings", { exact: true }).click();
    await page.getByRole("button", { name: "Add mapping" }).click();
    await page.getByLabel("Workflow ID 1", { exact: true }).fill("17");
    await page.getByLabel("Exact job name 1", { exact: true }).fill("Ruff");
    await page.getByRole("checkbox", { name: "lint", exact: true }).check();
    await page.getByRole("checkbox", { name: "build", exact: true }).check();
    await page.getByRole("button", { name: "Save analytics settings" }).click();
    await expect(page.getByText(/Save outcome unknown/)).toBeVisible();
    await page.getByRole("tab", { name: "Registered runs", exact: true }).click();
    await page.getByRole("tab", { name: "Analytics", exact: true }).click();
    await expect(page.getByLabel("Exact job name 1", { exact: true })).toHaveValue("Ruff");
    await expect(page.getByLabel("Exact job name 1", { exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Retry same operation" }).click();
    await expect(page.getByText("Saved analytics settings, revision 1.")).toBeVisible();
    expect(commands).toHaveLength(2);
    expect(commands[0]?.entries[0]?.purposes).toEqual(["lint", "build"]);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: test.info().outputPath(`purpose-settings-${width}.png`),
      fullPage: true,
    });
  });
