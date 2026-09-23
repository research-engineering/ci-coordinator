import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { economicsScenario } from "../../dev/economics";
import type { BudgetCommand, BudgetPolicy } from "../../src/api/ciEconomics/budgetSchema";
import { budgetPolicies, budgetPolicy, budgetSignals } from "../economicsBudgetFixture";
import {
  economicsSource,
  economicsSourceItem,
  economicsSourcePage,
} from "../economicsConsoleFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../fixture";

async function prepare(page: Page, registrationFailures = 0) {
  const economics = economicsScenario();
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
    const result = economics.handle({
      url: new URL(request.url()),
      method: request.method(),
      body: request.postData() === null ? undefined : request.postDataJSON(),
      csrf: request.headers()["x-csrf-token"],
    });
    if (!result) throw new Error("Unexpected economics request");
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v2/economics/sources" &&
      registrationFailures > 0
    ) {
      registrationFailures -= 1;
      await route.fulfill({ status: 503, json: { ok: false, error: "provider_unavailable" } });
      return;
    }
    await route.fulfill(result);
  });
  return { commands: economics.commands };
}

function url(): string {
  const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!base) throw new Error("Browser server unavailable");
  return new URL("/workbench?installationId=1&repositoryId=1&limit=10&view=economics", base).href;
}

async function navigateTo(page: Page, name: "Repositories" | "CI economics") {
  const link = page.getByRole("link", { name, exact: true });
  if (!(await link.isVisible()))
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  await link.click();
  await expect(
    page.getByRole("heading", {
      name,
      level: 1,
    }),
  ).toBeVisible();
}

async function checkLayout(page: Page, label: string) {
  const dimensions = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.width);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: test.info().outputPath(`${label}.png`), fullPage: true });
}

test("persistent policies are editable and signals expose their historical evidence", async ({
  page,
}) => {
  await prepare(page);
  let policies: BudgetPolicy[] = [];
  const commands: BudgetCommand[] = [];
  await page.route("**/api/v2/economics/**/budget-policies", (route) =>
    route.fulfill({ json: budgetPolicies(policies) }),
  );
  await page.route("**/api/v2/economics/budget-policies", async (route) => {
    const command: BudgetCommand = route.request().postDataJSON();
    commands.push(command);
    const policy: BudgetPolicy = {
      schemaVersion: "ci-economics-budget-policy/v1",
      installationId: 1,
      repositoryId: 1,
      policyKey: command.policyKey,
      revision: command.expectedRevision + 1,
      configuration: command.configuration,
    };
    policies = [policy];
    await route.fulfill({
      json: {
        schemaVersion: "ci-economics-budget-mutation/v1",
        operationId: command.operationId,
        outcome: "committed",
        policy,
      },
    });
  });
  await page.route("**/api/v2/economics/**/budget-signals?*", (route) =>
    route.fulfill({ json: budgetSignals() }),
  );
  await page.goto(url());
  await page.getByRole("tab", { name: "Budgets", exact: true }).click();
  await page.getByRole("button", { name: "New policy" }).click();
  await page.getByRole("textbox", { name: "Policy key" }).fill("backend-cpu");
  await page.getByRole("textbox", { name: "Sample key" }).fill("backend-tests");
  await page.getByRole("textbox", { name: "Producer SHA-256" }).fill("c".repeat(64));
  await page.getByRole("combobox", { name: "Counter" }).selectOption("cpu_user");
  await page.getByRole("textbox", { name: "Maximum (microseconds)" }).fill("20");
  await checkLayout(page, "economics-budget-editor");
  await page.getByRole("button", { name: "Save policy" }).click();
  await page.getByRole("button", { name: "Edit backend-cpu" }).click();
  await page.getByRole("checkbox", { name: "Enabled" }).uncheck();
  await page.getByRole("button", { name: "Save policy" }).click();
  await expect(page.getByText("Disabled", { exact: true })).toBeVisible();
  expect(commands.map((command) => command.expectedRevision)).toEqual([0, 1]);
  await page.getByRole("tab", { name: "Signals", exact: true }).click();
  await expect(page.getByText("backend-cpu / revision 1", { exact: true })).toBeVisible();
  await page.getByText("Evidence identity", { exact: true }).click();
  await expect(page.getByText("Policy digest", { exact: true })).toBeVisible();
  await checkLayout(page, "economics-budget-signal");
});

for (const outcome of ["network", "revision_conflict"] as const) {
  test(`budget ${outcome} recovery cannot reopen cached editing`, async ({ page }) => {
    await prepare(page);
    let reads = 0;
    const refreshed = Promise.withResolvers<void>();
    const commands: BudgetCommand[] = [];
    await page.route("**/api/v2/economics/**/budget-policies", async (route) => {
      reads += 1;
      if (reads > 1) await refreshed.promise;
      await route.fulfill({
        json: budgetPolicies([{ ...budgetPolicy(), revision: reads > 1 ? 2 : 1 }]),
      });
    });
    await page.route("**/api/v2/economics/budget-policies", async (route) => {
      const command: BudgetCommand = route.request().postDataJSON();
      commands.push(command);
      if (outcome === "network") return route.abort("failed");
      await route.fulfill({
        status: 409,
        json: {
          schemaVersion: "ci-economics-budget-mutation/v1",
          operationId: command.operationId,
          outcome,
          policy: null,
        },
      });
    });
    await page.goto(url());
    await page.getByRole("tab", { name: "Budgets", exact: true }).click();
    await page.getByRole("button", { name: "Edit backend-cpu" }).click();
    await page.getByRole("button", { name: "Save policy" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "New policy" })).toBeDisabled();
    await page.getByRole("button", { name: "Close editor" }).click();
    await expect.poll(() => reads).toBe(2);
    await expect(page.getByRole("button", { name: "Edit backend-cpu" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "New policy" })).toHaveCount(0);
    expect(commands).toHaveLength(1);
    refreshed.resolve();
    await page.getByRole("button", { name: "Edit backend-cpu" }).click();
    await expect(page.getByText("Expected revision 2", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Save policy" }).click();
    await expect.poll(() => commands.length).toBe(2);
    expect(commands.map((command) => command.expectedRevision)).toEqual([1, 2]);
  });
}

test("retained economics is a bounded run-to-report-to-comparison journey", async ({ page }) => {
  const { commands } = await prepare(page);
  await page.goto(url());
  await expect(page.getByRole("tab", { name: "Registered runs" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("button", { name: "Inspect run 4201, attempt 2" })).toBeVisible();
  await checkLayout(page, "economics-catalog");
  for (const [run, choice] of [
    [4201, "baseline"],
    [4202, "treatment"],
  ] as const) {
    await page.getByRole("button", { name: `Inspect run ${run}, attempt 2` }).click();
    await expect(page.getByRole("heading", { name: "Provider durations" })).toBeVisible();
    await page.getByRole("button", { name: /^Inspect report/ }).click();
    await expect(
      page.getByRole("definition").filter({ hasText: /^20 us\s*waited children$/ }),
    ).toBeVisible();
    await page.getByRole("button", { name: `Use as ${choice}` }).click();
    const budgetHeading = page.getByRole("heading", { name: "One-report budget" });
    expect(
      await budgetHeading.evaluate((heading) => {
        const text = document.createRange();
        text.selectNodeContents(heading);
        return text.getBoundingClientRect().left - heading.getBoundingClientRect().left;
      }),
    ).toBeGreaterThanOrEqual(12);
    await checkLayout(page, `economics-${choice}`);
    await page.getByRole("button", { name: "Registered runs", exact: true }).click();
  }
  await page.getByRole("button", { name: "Compare 2/2" }).click();
  await expect(page.getByRole("heading", { name: "Report comparison" })).toBeVisible();
  await expect(
    page.getByText("Coverage: not verified / Causal effect: not established"),
  ).toBeVisible();
  await checkLayout(page, "economics-comparison");
  expect(commands).toHaveLength(0);
});

test("discovery requires an explicit query and registration requires another explicit command", async ({
  page,
}) => {
  const { commands } = await prepare(page);
  await page.goto(url());
  await page.getByRole("tab", { name: "Discover runs" }).click();
  expect(commands).toHaveLength(0);
  await page.getByLabel("Created from (UTC)").fill("2026-09-08T00:00");
  await page.getByLabel("Created through (UTC)").fill("2026-09-08T12:00");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("button", { name: "Register run 4201, attempt 2" })).toBeVisible();
  expect(commands).toHaveLength(1);
  await checkLayout(page, "economics-discovery");
  await page.getByRole("button", { name: "Register run 4201, attempt 2" }).click();
  await expect(page.getByText("Registered for collection")).toBeVisible();
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual({
    path: "/api/v2/economics/sources",
    csrf: "c".repeat(43),
    body: { installationId: 1, repositoryId: 1, workflowRunId: 4201, runAttempt: 2 },
  });
  await checkLayout(page, "economics-registration");
});

test("registration retries the exact failed command without another discovery", async ({
  page,
}) => {
  const { commands } = await prepare(page, 1);
  await page.goto(url());
  await page.getByRole("tab", { name: "Discover runs" }).click();
  await page.getByLabel("Created from (UTC)").fill("2026-09-08T00:00");
  await page.getByLabel("Created through (UTC)").fill("2026-09-08T12:00");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("button", { name: "Register run 4201, attempt 2" }).click();
  await expect(page.getByText("Registration temporarily unavailable")).toBeVisible();
  await expect(page.getByText("Registered for collection")).toHaveCount(0);
  expect(commands).toHaveLength(2);
  await checkLayout(page, "economics-registration-failure");
  await page.getByLabel("Created from (UTC)").fill("2026-09-07T00:00");
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Registered for collection")).toBeVisible();
  expect(commands).toHaveLength(3);
  expect(commands[2]).toEqual(commands[1]);
  expect(commands[2]).toEqual({
    path: "/api/v2/economics/sources",
    csrf: "c".repeat(43),
    body: { installationId: 1, repositoryId: 1, workflowRunId: 4201, runAttempt: 2 },
  });
  await checkLayout(page, "economics-registration-retry");
});

test("a newly selected repository rejects an old-scope catalog and can retry", async ({ page }) => {
  const { commands } = await prepare(page);
  await page.route("**/api/v1/workbench/repositories/1/2?*", (route) =>
    route.fulfill({ json: workbenchFixture({ scope: { installationId: 1, repositoryId: 2 } }) }),
  );
  let stale = true;
  let scopedReads = 0;
  await page.route("**/api/v2/economics/repositories/1/2/sources?*", (route) => {
    scopedReads += 1;
    return route.fulfill({
      json: stale
        ? economicsSourcePage([economicsSourceItem(economicsSource(4201))])
        : {
            ...economicsSourcePage([economicsSourceItem(economicsSource(9002, 2))]),
            repositoryId: 2,
          },
    });
  });
  await page.goto(url());
  await expect(page.getByRole("button", { name: "Inspect run 4201, attempt 2" })).toBeVisible();
  await navigateTo(page, "Repositories");
  await page.getByText("Advanced degraded access").click();
  await page.getByRole("spinbutton", { name: "Repository", exact: true }).fill("2");
  await page.getByRole("button", { name: "Load snapshot" }).click();
  await navigateTo(page, "CI economics");
  await expect(page.getByText("Evidence could not be validated")).toBeVisible();
  await expect(page.getByRole("button", { name: "Inspect run 4201, attempt 2" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Inspect run 4202, attempt 2" })).toHaveCount(0);
  expect(scopedReads).toBe(1);
  stale = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("button", { name: "Inspect run 9002, attempt 2" })).toBeVisible();
  expect(scopedReads).toBe(2);
  expect(commands).toHaveLength(0);
  await checkLayout(page, "economics-new-scope");
});
