import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { ciEconomicsAttemptPageFixture } from "../ciEconomicsFixture";
import { economicsSourcePage } from "../economicsConsoleFixture";
import {
  configActivationFixture,
  controlPlaneSessionFixture,
  installationCatalogFixture,
  installationFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "../fixture";
import {
  governanceBaselineApprovalFixture,
  governanceBaselineRecordFixture,
} from "../governanceBaselineFixture";
import {
  governanceComparisonFixture,
  unbaselinedGovernanceComparisonFixture,
} from "../governanceComparisonFixture";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({ json: { error: "unauthenticated", ok: false }, status: 401 });
  });
});

async function routeReadyPortfolio(page: Page, governanceAuthorized = false) {
  await page.route("**/api/v2/economics/repositories/1/1/sources?*", async (route) => {
    await route.fulfill({ json: economicsSourcePage([]) });
  });
  await page.route("**/api/v1/economics/repositories/1/1/attempts*", async (route) => {
    await route.fulfill({ json: ciEconomicsAttemptPageFixture({ items: [] }) });
  });
  await page.route("**/api/v1/workbench/installations?*", async (route) => {
    await route.fulfill({ json: installationCatalogFixture() });
  });
  await page.route(
    "**/api/v1/workbench/installations/1/repositories?page=1&perPage=100",
    async (route) => {
      await route.fulfill({ json: repositoryPageFixture() });
    },
  );
  await page.route("**/api/v1/workbench/repositories/1/1?limit=10", async (route) => {
    await route.fulfill({ json: workbenchFixture() });
  });
  await page.route("**/api/v1/workbench/repositories/1/1/workflow-discovery*", async (route) => {
    await route.fulfill({ json: workflowDiscoveryFixture() });
  });
  await page.route("**/api/v1/workbench/repositories/1/1/governance-comparison", async (route) => {
    await route.fulfill(
      governanceAuthorized
        ? { json: unbaselinedGovernanceComparisonFixture() }
        : {
            json: { error: "unauthenticated", ok: false, retryAfterSeconds: null },
            status: 401,
          },
    );
  });
}

test("renders the admitted workbench without viewport overflow", async ({ page }) => {
  await routeReadyPortfolio(page);
  await page.goto(workbenchUrl());
  await expect(page.getByRole("heading", { name: "Repositories", level: 1 })).toBeVisible();
  await page.getByRole("button", { name: "Open" }).click();
  await expect(page.getByText("Ledger revision")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Workflow discovery" })).not.toBeVisible();
  await navigateTo(page, "Workflows");
  await expect(page.getByText("Observe-only policy admitted")).toBeVisible();
  await expect(page.getByText("Ledger revision")).not.toBeVisible();
  await navigateTo(page, "Governance");
  await expect(page.getByText("Authentication required")).toBeVisible();
  await expectNoViewportOverflow(page);
});

test("has no automatically detectable accessibility violations", async ({ page }) => {
  await routeReadyPortfolio(page);
  await page.goto(workbenchUrl());
  await page.getByRole("button", { name: "Open" }).click();
  await expect(page.getByText("Ledger revision")).toBeVisible();
  const navigation = page.getByRole("complementary");
  const navigationHeight = await navigation.evaluate(
    (element) => element.getBoundingClientRect().height,
  );
  for (const [view, content] of [
    ["Overview", "No plans in this snapshot."],
    ["Workflows", "Observe-only policy admitted"],
    ["CI economics", "No retained runs."],
    ["Governance", "Authentication required"],
    ["Audit log", "No audit events in this snapshot."],
    ["Repositories", "ci-coordinator"],
  ] as const) {
    await navigateTo(page, view);
    await expect(page.getByText(content, { exact: true }).filter({ visible: true })).toBeVisible();
    expect(
      await navigation.evaluate((element) => element.getBoundingClientRect().height),
    ).toBeCloseTo(navigationHeight, 0);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await expectNoViewportOverflow(page);
    await page.screenshot({
      path: test.info().outputPath(`${view.replaceAll(" ", "-")}.png`),
      fullPage: true,
    });
  }
});

test("scope controls are keyboard reachable", async ({ page }) => {
  await routeReadyPortfolio(page);
  await page.goto(workbenchUrl());
  await expect(page.getByRole("combobox", { name: "Organization" })).toBeVisible();
  await page.keyboard.press("Tab");
  const toggle = page.getByRole("button", { name: "Toggle navigation" });
  if (await toggle.isVisible()) {
    await expect(toggle).toBeFocused();
  } else {
    await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Repositories", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Activity", exact: true })).toBeFocused();
  }
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Sign in" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Refresh repositories" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("combobox", { name: "Organization" })).toBeFocused();
});

test("native organization selection has one decorative chevron and selects exact inventory", async ({
  page,
}) => {
  await routeReadyPortfolio(page);
  const other = installationFixture({
    installationId: 2,
    accountLogin: "another-organization-with-a-long-name",
  });
  await page.route("**/api/v1/workbench/installations?*", (route) =>
    route.fulfill({
      json: installationCatalogFixture({ installations: [installationFixture(), other] }),
    }),
  );
  let otherReads = 0;
  await page.route(
    "**/api/v1/workbench/installations/2/repositories?page=1&perPage=100",
    async (route) => {
      otherReads += 1;
      await route.fulfill({
        json: repositoryPageFixture({ installation: other, repositories: [], totalCount: 0 }),
      });
    },
  );
  await page.goto(workbenchUrl());
  const select = page.getByRole("combobox", { name: "Organization" });
  await expect(select).toHaveValue("1");
  await expect(page.getByRole("button", { name: "Open" })).toBeVisible();
  expect(
    await select.evaluate((element) => element.parentElement?.querySelectorAll("svg").length),
  ).toBe(1);
  await expect(select.locator("..").locator("svg")).toHaveAttribute("aria-hidden", "true");
  await expect(select.locator("..").locator("svg")).toHaveCSS("pointer-events", "none");
  await select.focus();
  await select.press("ArrowDown");
  await select.press("Enter");
  await expect(select).toHaveValue("2");
  await expect.poll(() => otherReads).toBe(1);
  await expect(page.getByText("No repositories.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open" })).toHaveCount(0);
  await expectNoViewportOverflow(page);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: test.info().outputPath("organization-select.png"),
    fullPage: true,
  });
});

test("organization pagination stays accessible when the next page has no active account", async ({
  page,
}) => {
  await routeReadyPortfolio(page);
  await page.route("**/api/v1/workbench/installations?*", (route) => {
    const current = Number(new URL(route.request().url()).searchParams.get("page"));
    return route.fulfill({
      json: installationCatalogFixture({
        page: current,
        hasNextPage: current === 1,
        installations:
          current === 1
            ? [installationFixture()]
            : [
                installationFixture({
                  installationId: 2,
                  accountLogin: "suspended-org",
                  state: "suspended",
                }),
              ],
      }),
    });
  });
  await page.goto(workbenchUrl());
  await expect(page.getByRole("button", { name: "Open" })).toBeVisible();
  await page.getByRole("button", { name: "Next organization page" }).click();
  await expect(page.getByRole("navigation", { name: "Organization pages" })).toHaveText("Page 2");
  await expect(page.getByRole("button", { name: "Open" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Next organization page" })).toBeDisabled();
  await expectNoViewportOverflow(page);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: test.info().outputPath("organization-pagination.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Previous organization page" }).click();
  await expect(page.getByRole("button", { name: "Open" })).toBeVisible();
});

test("sidebar retains named navigation in both modes and across the mobile breakpoint", async ({
  page,
}) => {
  await routeReadyPortfolio(page);
  await page.goto(workbenchUrl());
  await expect(page.getByRole("button", { name: "Open" })).toBeVisible();
  const mobile = (page.viewportSize()?.width ?? 0) <= 900;
  const collapse = page.getByRole("button", { name: "Collapse sidebar" });
  const expand = page.getByRole("button", { name: "Expand sidebar" });
  if (mobile) {
    await expect(expand).not.toBeVisible();
    await page.getByRole("button", { name: "Toggle navigation" }).click();
    await expect(page.getByRole("link", { name: "Repositories", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  } else {
    const rail = page.getByRole("complementary");
    await expect(expand).toHaveAttribute("aria-expanded", "false");
    const compactWidth = await rail.evaluate((element) => element.getBoundingClientRect().width);
    await expand.click();
    expect(await rail.evaluate((element) => element.getBoundingClientRect().width)).toBeGreaterThan(
      compactWidth,
    );
    await collapse.click();
  }
  await page.getByRole("button", { name: "Open" }).click();
  await expect(page.getByText("Ledger revision")).toBeVisible();
  if (!mobile) {
    await expect(expand).toBeVisible();
    await expand.focus();
    for (const name of [
      "Repositories",
      "Activity",
      "Overview",
      "Workflows",
      "Configuration",
      "CI economics",
      "Governance",
      "Audit log",
    ]) {
      const link = page.getByRole("link", { name, exact: true });
      await page.keyboard.press("Tab");
      await expect(link).toBeFocused();
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute("title", name);
      const box = await link.boundingBox();
      expect(box?.width).toBeGreaterThanOrEqual(44);
      expect(box?.height).toBeGreaterThanOrEqual(44);
    }
    for (const name of ["Governance", "CI economics", "Configuration", "Workflows"]) {
      await page.keyboard.press("Shift+Tab");
      await expect(page.getByRole("link", { name, exact: true })).toBeFocused();
    }
    await page.screenshot({ path: test.info().outputPath("compact-keyboard-focus.png") });
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Workflows", exact: true })).toBeFocused();
    await expect(page.getByText("Observe-only policy admitted")).toBeVisible();
    const desktop = page.viewportSize();
    if (!desktop) throw new Error("Viewport is required by the browser contract");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(expand).not.toBeVisible();
    await page.getByRole("button", { name: "Toggle navigation" }).click();
    await expect(
      page.getByRole("link", { name: "Workflows", exact: true }).locator("span"),
    ).toHaveCSS("position", "static");
    await page.getByRole("link", { name: "Workflows", exact: true }).press("Escape");
    await expect(page.getByRole("button", { name: "Toggle navigation" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await page.setViewportSize(desktop);
    await expect(expand).toBeVisible();
  }
  await expectNoViewportOverflow(page);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: test.info().outputPath("sidebar-preference.png"), fullPage: true });
});

test("scope errors identify their inputs without requesting an invalid scope", async ({ page }) => {
  await routeReadyPortfolio(page);
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.goto(workbenchUrl());
  await page.getByText("Advanced degraded access").click();
  const installation = page.getByRole("spinbutton", { name: "Installation" });
  await installation.fill("0");
  await page.getByRole("button", { name: "Load snapshot" }).click();
  await expect(installation).toBeFocused();
  await expect(installation).toHaveAttribute("aria-invalid", "true");
  await expect(installation).toHaveAccessibleDescription(/Installation: enter a whole number/);
  expect(requests.some((url) => url.includes("/repositories/0/"))).toBe(false);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await expectNoViewportOverflow(page);
});

test("full evidence identifiers support keyboard and touch disclosure", async ({
  page,
  isMobile,
}) => {
  await routeReadyPortfolio(page);
  const hash = "abcdef0123456789".repeat(4);
  await page.route("**/api/v1/workbench/repositories/1/1?limit=10", async (route) => {
    await route.fulfill({
      json: workbenchFixture({
        auditEvents: [
          {
            actor: "operator-".repeat(10),
            auditEventId: "audit-one",
            createdAt: "2026-09-05T12:00:00Z",
            eventHash: hash,
            eventType: "observed",
            payload: {},
            payloadHash: "payload-one",
            previousEventHash: null,
            sequence: 1,
            subjectId: "subject-one",
            subjectType: "run",
          },
        ],
      }),
    });
  });
  await page.goto(workbenchUrl());
  await page.getByRole("button", { name: "Open" }).click();
  await navigateTo(page, "Audit log");
  await expect(page.getByRole("heading", { name: "Audit events" })).toBeVisible();
  const summary = page.getByLabel("Inspect identifier abcdef01..456789");
  const full = page.getByText(hash, { exact: true });
  await expect(full).not.toBeVisible();
  if (isMobile) await summary.tap();
  else {
    await summary.focus();
    await page.keyboard.press("Enter");
  }
  await expect(full).toBeVisible();
  await expect(full).toHaveText(hash);
  const actorCell = page.getByRole("cell", { name: "operator-".repeat(10), exact: true });
  const timeCell = page.getByRole("cell", { name: "05 Sep 2026, 12:00:00 UTC", exact: true });
  await expect(actorCell).toHaveClass("column-prose");
  expect((await actorCell.boundingBox())?.width).toBeGreaterThanOrEqual(190);
  expect((await timeCell.boundingBox())?.width).toBeGreaterThanOrEqual(200);
  expect(await actorCell.evaluate((cell) => cell.scrollWidth <= cell.clientWidth)).toBe(true);
  for (const cell of [
    page.getByRole("columnheader", { name: "Sequence", exact: true }),
    page.getByRole("columnheader", { name: "Event", exact: true }),
    page.getByRole("cell", { name: "observed", exact: true }),
  ]) {
    expect(
      await cell.evaluate((element) => {
        const text = document.createRange();
        text.selectNodeContents(element);
        return text.getClientRects().length;
      }),
    ).toBe(1);
  }
  await expectNoViewportOverflow(page);
  await page.screenshot({
    path: test.info().outputPath("identity-disclosure.png"),
    fullPage: true,
  });
});

test("a rendering crash recovers through explicit same-URL refresh", async ({ page }) => {
  await routeReadyPortfolio(page);
  await page.addInitScript(() => {
    const marker = "render-crash-witness";
    if (sessionStorage.getItem(marker)) return;
    sessionStorage.setItem(marker, "injected");
    Date.prototype.getUTCHours = () => {
      throw new Error("private-render-witness-payload");
    };
  });
  const diagnostics: string[] = [];
  const mutations: string[] = [];
  page.on("console", (message) => diagnostics.push(message.text()));
  page.on("request", (request) => {
    if (request.method() !== "GET") mutations.push(request.method());
  });
  const url = new URL(workbenchUrl());
  url.search = "installationId=1&repositoryId=1&limit=10";
  await page.goto(url.href);
  await expect(page.getByRole("heading", { name: "Console unavailable" })).toBeVisible();
  const failedUrl = page.url();
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: test.info().outputPath("console-recovery.png"), fullPage: true });
  expect(diagnostics).toContain("Operator console rendering failed.");
  expect(diagnostics.join("\n")).not.toContain("private-render-witness-payload");
  expect(mutations).toEqual([]);
  await page.getByRole("button", { name: "Refresh console" }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await expect(page.getByText("Ledger revision")).toBeVisible();
  expect(page.url()).toBe(failedUrl);
  expect(mutations).toEqual([]);
});

test("verifies repository authority before configuration activation", async ({ page }) => {
  let activated = false;
  await page.unroute("**/api/v1/auth/session");
  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({ json: controlPlaneSessionFixture() });
  });
  await routeReadyPortfolio(page, true);
  await page.route("**/api/v1/workbench/repositories/1/1?limit=10", (route) =>
    route.fulfill({
      json: workbenchFixture({
        configEpochs: activated
          ? [
              {
                active: true,
                activeRevision: 1,
                epochId: "4".repeat(64),
                sourceFormat: "json",
                sourceHash: "a".repeat(64),
                documentHash: "b".repeat(64),
                epochHash: "c".repeat(64),
                documentSchemaId: "ci-repository-policy/v1",
                documentProfileId: "ci-policy-document/v1",
                semanticProfileId: "ci-repository-policy-semantics/v1",
                compiledSchemaId: "ci-compiled-repository-policy/v1",
              },
            ]
          : [],
      }),
    }),
  );
  await page.route("**/api/v1/repository-attestations/github/start", async (route) => {
    await route.fulfill({ json: { error: "already_reviewed", ok: false }, status: 409 });
  });
  await page.route("**/api/v1/config/activations", async (route) => {
    activated = true;
    await route.fulfill({ json: configActivationFixture() });
  });

  await page.goto(workbenchUrl());
  const expandAccount = page.getByRole("button", { name: "Expand sidebar", exact: true });
  if (await expandAccount.isVisible()) await expandAccount.click();
  await expect(page.getByText("Bart Simpson")).toBeVisible();
  await page.getByRole("button", { name: "Open" }).click();
  await navigateTo(page, "Workflows");
  await page.getByRole("button", { name: "Verify authority" }).click();
  await page.getByRole("button", { name: "Activate proposal" }).click();

  await expect(page.getByText("Configuration activated")).toBeVisible();
  await expect(page.getByText("Activation recorded at revision 1")).toBeVisible();
  await expect(page.getByText(/Current observed revision 1/)).toBeVisible();
  await expectNoViewportOverflow(page);
});

test("retries baseline approval with one operation identity", async ({ page }) => {
  const firstResponse = Promise.withResolvers<void>();
  const failedCommands: string[] = [];
  page.on("requestfailed", (request) => {
    if (request.url().endsWith("/governance-baselines")) failedCommands.push(request.url());
  });
  await page.unroute("**/api/v1/auth/session");
  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({ json: controlPlaneSessionFixture() });
  });
  await routeReadyPortfolio(page, true);
  await page.unroute("**/api/v1/workbench/repositories/1/1/governance-comparison");
  const operationIds: string[] = [];
  let retainedOperationId: string | undefined;
  await page.route("**/api/v1/workbench/repositories/1/1/governance-comparison", async (route) => {
    await route.fulfill({
      json: retainedOperationId
        ? governanceComparisonFixture({
            baseline: governanceBaselineRecordFixture({
              operationId: retainedOperationId,
            }),
          })
        : unbaselinedGovernanceComparisonFixture(),
    });
  });
  await page.route("**/api/v1/workbench/repositories/1/1/governance-baselines", async (route) => {
    const command = route.request().postDataJSON();
    operationIds.push(command.operationId);
    if (operationIds.length === 1) {
      await firstResponse.promise;
      await route.fulfill({
        json: { error: "unavailable", ok: false },
        status: 503,
      });
      return;
    }
    retainedOperationId = command.operationId;
    await route.fulfill({
      json: governanceBaselineApprovalFixture({
        baseline: {
          ...governanceBaselineApprovalFixture().baseline,
          operationId: command.operationId,
        },
        requestOperationId: command.operationId,
      }),
      status: 201,
    });
  });

  await page.goto(workbenchUrl());
  await page.getByRole("button", { name: "Open" }).click();
  await navigateTo(page, "Governance");
  await page.getByRole("textbox", { name: "Approval reason" }).fill("Adopt governance");
  await navigateTo(page, "Overview");
  await navigateTo(page, "Governance");
  await expect(page.getByRole("textbox", { name: "Approval reason" })).toHaveValue(
    "Adopt governance",
  );
  await page.getByRole("button", { name: "Approve baseline" }).click();
  try {
    await expect.poll(() => operationIds.length).toBe(1);
    if ((page.viewportSize()?.width ?? 0) > 900) {
      for (const name of ["Collapse sidebar", "Expand sidebar"]) {
        await page.getByRole("button", { name, exact: true }).click();
        await expect(page.getByRole("textbox", { name: "Approval reason" })).toHaveValue(
          "Adopt governance",
        );
        await expect(page.getByRole("button", { name: "Approving", exact: true })).toBeDisabled();
        expect(operationIds).toHaveLength(1);
        expect(failedCommands).toEqual([]);
      }
    }
  } finally {
    firstResponse.resolve();
  }
  await expect(page.getByText("Baseline service unavailable")).toBeVisible();
  await navigateTo(page, "Workflows");
  await navigateTo(page, "Governance");
  expect(operationIds).toHaveLength(1);
  await page.getByRole("button", { name: "Retry baseline approval" }).click();

  await expect(page.getByText("Expected governance state approved")).toBeVisible();
  await expect(page.getByText("bytes match")).toBeVisible();
  expect(operationIds).toHaveLength(2);
  expect(operationIds[0]).toBe(operationIds[1]);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await expectNoViewportOverflow(page);
});

test("navigation restores history and keyboard tabs without reloading the repository", async ({
  page,
}) => {
  await routeReadyPortfolio(page);
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET") mutations.push(request.url());
  });
  await page.goto(workbenchUrl());
  await page.getByRole("button", { name: "Open" }).click();
  await expect(page.getByText("Ledger revision")).toBeVisible();
  const plans = page.getByRole("tab", { name: "Plans", exact: true });
  await plans.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Runs", selected: true })).toBeFocused();
  await expect(page.getByText("No runs in this snapshot.")).toBeVisible();
  await expect(page.getByText("No plans in this snapshot.")).not.toBeVisible();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "Overrides", selected: true })).toBeFocused();
  await expect(page.getByText("No overrides in this snapshot.")).toBeVisible();
  await page.keyboard.press("Home");
  await expect(plans).toBeFocused();
  await navigateTo(page, "Workflows");
  const workflowsUrl = page.url();
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeFocused();
  await page.goForward();
  await expect(page.getByRole("heading", { name: "Workflows", level: 1 })).toBeFocused();
  expect(page.url()).toBe(workflowsUrl);
  await page.reload();
  await expect(page.getByText("Observe-only policy admitted")).toBeVisible();
  expect(page.url()).toBe(workflowsUrl);
  expect(mutations).toEqual([]);
  await expectNoViewportOverflow(page);
});

test("a narrow navigation menu closes with Escape and returns focus", async ({ page }) => {
  await routeReadyPortfolio(page);
  await page.goto(workbenchUrl());
  const toggle = page.getByRole("button", { name: "Toggle navigation" });
  if (!(await toggle.isVisible())) {
    await expect(page.getByRole("link", { name: "Repositories", exact: true })).toBeVisible();
    return;
  }
  await toggle.click();
  const repositories = page.getByRole("link", { name: "Repositories", exact: true });
  await repositories.focus();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Escape");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();
  await expect(repositories).not.toBeVisible();
});

test("renders the provider-free catalog as a valid empty state", async ({ page }) => {
  await page.route("**/api/v1/workbench/installations?*", async (route) => {
    await route.fulfill({
      json: installationCatalogFixture({ installations: [] }),
    });
  });
  await page.goto(workbenchUrl());
  await expect(
    page.locator(".catalog-message").getByText("No authorized organizations", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Organization" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Organization" })).toBeDisabled();
  await expectNoViewportOverflow(page);
});

test("contains a malformed catalog response without layout overflow", async ({ page }) => {
  await page.route("**/api/v1/workbench/installations?*", async (route) => {
    await route.fulfill({ body: '<html lang="en"></html>', contentType: "text/html", status: 200 });
  });
  await page.goto(workbenchUrl());
  await expect(page.getByText("Repositories response rejected")).toBeVisible();
  await expectNoViewportOverflow(page);
});

function workbenchUrl(): string {
  const baseUrl = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!baseUrl) throw new Error("browser test server URL is unavailable");
  return new URL("/workbench", baseUrl).href;
}

async function expectNoViewportOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
}

async function navigateTo(page: Page, name: string) {
  const link = page.getByRole("link", { name, exact: true });
  if (!(await link.isVisible()))
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  await link.click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
}
