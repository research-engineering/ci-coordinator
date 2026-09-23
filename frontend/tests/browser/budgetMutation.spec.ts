import { expect, test } from "@playwright/test";
import type { BudgetCommand, BudgetPolicy } from "../../src/api/ciEconomics/budgetSchema";
import { budgetPolicies, budgetPolicy } from "../economicsBudgetFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../fixture";

test("completed budget write remains readable after the editor closes", async ({ page }) => {
  const origin = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!origin) throw new Error("browser test server URL is unavailable");
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
  let policy: BudgetPolicy = budgetPolicy();
  let writes = 0;
  const failures: string[] = [];
  const mutation = "/api/v2/economics/budget-policies";
  page.on("requestfailed", (request) => {
    if (new URL(request.url()).pathname === mutation)
      failures.push(request.failure()?.errorText ?? "unknown");
  });
  await page.route("**/api/v2/economics/repositories/1/1/budget-policies", (route) =>
    route.fulfill({ json: budgetPolicies([policy]) }),
  );
  await page.route(`**${mutation}`, async (route) => {
    const command = route.request().postDataJSON() as BudgetCommand;
    expect(route.request().method()).toBe("POST");
    writes += 1;
    policy = {
      ...policy,
      revision: command.expectedRevision + 1,
      configuration: command.configuration,
    };
    await route.fulfill({
      json: {
        schemaVersion: "ci-economics-budget-mutation/v1",
        operationId: command.operationId,
        outcome: "committed",
        policy,
      },
    });
  });
  await page.goto(
    new URL(
      "/workbench?installationId=1&repositoryId=1&limit=10&view=economics&economicsTab=budgets",
      origin,
    ).href,
  );
  await page.getByRole("button", { name: "Edit backend-cpu", exact: true }).click();
  const pending = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" && new URL(response.url()).pathname === mutation,
  );
  await page.getByRole("button", { name: "Save policy", exact: true }).click();
  const response = await pending;
  await expect(page.getByRole("heading", { name: "Edit backend-cpu" })).toHaveCount(0);
  expect(await response.finished()).toBeNull();
  expect((await response.json()).outcome).toBe("committed");
  expect(failures).toEqual([]);
  expect(writes).toBe(1);
  await page.reload();
  await expect(page.getByText(/revision 2$/)).toBeVisible();
  expect(writes).toBe(1);
});
