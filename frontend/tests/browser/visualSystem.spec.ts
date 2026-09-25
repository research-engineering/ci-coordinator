import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import type { WorkbenchSnapshot } from "../../src/api/workbench/schema";
import { economicsSourcePage } from "../economicsConsoleFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "../fixture";
import { governanceBaselineApprovalFixture } from "../governanceBaselineFixture";
import { governanceComparisonFixture } from "../governanceComparisonFixture";

const SHELL_CSP =
  "default-src 'self'; base-uri 'none'; connect-src 'self'; form-action 'self'; " +
  "frame-ancestors 'none'; img-src 'self' data:; object-src 'none'; script-src 'self'; " +
  "style-src 'self'; worker-src 'none'";

function visualSnapshot(): WorkbenchSnapshot {
  return workbenchFixture({
    plans: [null, "missing_active_policy_epoch", "runner_capacity_unavailable"].map(
      (fallbackReason, index) => ({
        baseSha: "1".repeat(40),
        catalogHash: null,
        eventName: "push",
        executionMode: fallbackReason === null ? "selected" : "full-ci",
        expiresAt: "2026-09-08T09:00:00Z",
        fallbackReason,
        headSha: String(index + 2).repeat(40),
        issuedAt: "2026-09-08T08:12:00Z",
        omittedObligationIds: [],
        planId: `plan:${String(index + 2).repeat(64)}`,
        productionAdmissionReceiptId: null,
        profiles: [],
        recordId: `record-${index}`,
        ref: index === 1 ? "refs/pull/145/merge" : "refs/heads/master",
        requestId: `request-${index}`,
        runAttempt: 1,
        selectedObligationIds: [],
        selectedWitnessIds: fallbackReason === null ? ["lint", "types", "browser"] : [],
        targetRegistryHash: null,
        testManifestId: null,
        verifiedPlanId: null,
        workflowRunId: 4201 + index,
      }),
    ),
  });
}

function consoleUrl(query = ""): string {
  const origin = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!origin) throw new Error("browser test server URL is unavailable");
  return new URL(`/workbench${query}`, origin).href;
}

async function prepare(page: Page) {
  if (test.info().project.name === "desktop-chromium") {
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  const session = controlPlaneSessionFixture();
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({
      json: {
        ...session,
        user: { ...session.user, displayName: "Bart Simpson", preferredUsername: "bart.simpson" },
      },
    }),
  );
  await page.route("**/api/v1/workbench/installations?page=1&perPage=30", (route) =>
    route.fulfill({ json: installationCatalogFixture() }),
  );
  const repositories = [
    "ci-coordinator",
    "fleet-api",
    "claims-portal",
    "legacy-importer",
    "design-tokens",
    "telematics-gateway",
  ].map((name, index) =>
    repositoryFixture({
      name,
      fullName: `example-org/${name}`,
      nodeId: `R_${index + 1}`,
      scope: { installationId: 1, repositoryId: index + 1 },
      archived: index === 3,
      disabled: index === 5,
      workbenchAuthorized: [0, 1, 4].includes(index),
      visibility: index === 4 ? "public" : "private",
    }),
  );
  await page.route("**/api/v1/workbench/installations/1/repositories?*", (route) =>
    route.fulfill({ json: repositoryPageFixture({ repositories, totalCount: 6 }) }),
  );
  await page.route("**/api/v1/workbench/repositories/1/1?*", (route) =>
    route.fulfill({ json: visualSnapshot() }),
  );
  await page.route("**/api/v1/workbench/repositories/1/1/workflow-discovery*", (route) =>
    route.fulfill({ json: workflowDiscoveryFixture() }),
  );
  await page.route("**/api/v1/workbench/repositories/1/1/governance-comparison", (route) =>
    route.fulfill({ json: governanceComparisonFixture() }),
  );
  await page.route("**/api/v2/economics/repositories/1/1/sources?*", (route) =>
    route.fulfill({ json: economicsSourcePage([]) }),
  );
}

async function navigate(page: Page, name: string) {
  const link = page.getByRole("link", { name, exact: true });
  if (!(await link.isVisible())) {
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  }
  await link.click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
}

async function expectConsoleGutters(page: Page) {
  const geometry = await page.locator("main").evaluate((main) => {
    const frame = main.getBoundingClientRect();
    const content = main.querySelector(".page-header")?.getBoundingClientRect();
    if (!content) throw new Error("Console page header is missing");
    return {
      viewport: window.innerWidth,
      right: frame.right,
      available: document.documentElement.clientWidth,
      leading: content.left - frame.left,
      trailing: frame.right - content.right,
    };
  });
  const expected =
    geometry.viewport <= 900 ? 8 : Math.min(24, Math.max(10, geometry.viewport * 0.015));
  expect(geometry.right).toBeCloseTo(geometry.available, 1);
  expect(geometry.leading).toBeCloseTo(expected, 1);
  expect(geometry.trailing).toBeCloseTo(expected, 1);
}

async function capture(page: Page, name: string) {
  await expectConsoleGutters(page);
  await page.evaluate(() => document.fonts.ready);
  const geometry = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(geometry.content).toBeLessThanOrEqual(geometry.width);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: test.info().outputPath(`visual-${name}.png`), fullPage: true });
}

test("renders the reference visual system with same-origin fonts under the shell CSP", async ({
  page,
}) => {
  await prepare(page);
  const violations: string[] = [];
  page.on("console", (message) => {
    if (/content security policy/i.test(message.text())) violations.push(message.text());
  });
  const shell = await page.goto(consoleUrl());
  expect(shell?.headers()["content-security-policy"]).toBe(SHELL_CSP);
  await expect(page.getByText("6 loaded of 6")).toBeVisible();
  const fonts = await page.evaluate(async () => {
    const faces = await Promise.all([
      document.fonts.load("400 16px Lato", "A\u010c"),
      document.fonts.load("700 16px Lato", "A\u010c"),
    ]);
    return faces.flat().map((face) => ({ family: face.family, status: face.status }));
  });
  expect(fonts).toHaveLength(4);
  expect(fonts.every((font) => font.family === "Lato" && font.status === "loaded")).toBe(true);
  const assets = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => entry.name)
      .filter((name) => name.endsWith(".woff2")),
  );
  expect(assets).toHaveLength(4);
  expect(assets.every((url) => new URL(url).origin === new URL(consoleUrl()).origin)).toBe(true);
  await expect(page.locator("style, [style]")).toHaveCount(0);
  const stylesheets = await page
    .locator('link[rel="stylesheet"]')
    .evaluateAll((links) => links.map((link) => (link as HTMLLinkElement).href));
  const css = await Promise.all(
    stylesheets.map(async (url) => (await page.request.get(url)).text()),
  );
  expect(css.join("\n")).toContain("SIL OPEN FONT LICENSE Version 1.1");
  expect(css.join("\n")).toContain("PERMISSION & CONDITIONS");
  await capture(page, "portfolio-compact");
  if (test.info().project.name === "desktop-chromium") {
    for (const width of [768, 2200]) {
      await page.setViewportSize({ width, height: 900 });
      await expectConsoleGutters(page);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  const expand = page.getByRole("button", { name: "Expand sidebar" });
  if (await expand.isVisible()) await expand.click();
  if (test.info().project.name === "desktop-chromium") {
    await expect(page.getByText("Bart Simpson", { exact: true })).toBeVisible();
    await expect(page.getByText("bart.simpson", { exact: true })).toBeVisible();
  }
  await capture(page, "portfolio-expanded");
  await page.getByRole("button", { name: "Open", exact: true }).first().click();
  await expect(page.getByRole("cell", { name: "runner_capacity_unavailable" })).toBeVisible();
  if (test.info().project.name === "desktop-chromium") {
    const plans = page.getByRole("region", { name: "plans in this snapshot. table" });
    const width = await plans.evaluate((element) => ({
      scroll: element.scrollWidth,
      available: element.clientWidth,
    }));
    expect(width.scroll).toBeLessThanOrEqual(width.available);
    for (const identity of await plans.locator(".identity-value").all()) {
      const code = await identity.locator("summary code").boundingBox();
      const copy = await identity.getByRole("button").boundingBox();
      expect(code).not.toBeNull();
      expect(copy).not.toBeNull();
      expect((code?.x ?? 0) + (code?.width ?? 0)).toBeLessThanOrEqual(copy?.x ?? 0);
    }
  }
  await capture(page, "overview");
  await navigate(page, "Workflows");
  await expect(page.getByText("Observe-only policy admitted")).toBeVisible();
  await capture(page, "workflows");
  await navigate(page, "Governance");
  await expect(page.getByText("bytes match", { exact: true })).toBeVisible();
  await capture(page, "governance");
  await navigate(page, "CI economics");
  await expect(page.getByText("No retained runs.")).toBeVisible();
  await capture(page, "economics-empty");
  expect(violations).toEqual([]);
});

for (const outcome of ["ready", "empty", "failure"] as const) {
  test(`indeterminate loading transitions to ${outcome} without fabricated progress`, async ({
    page,
  }) => {
    await prepare(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const pending = Promise.withResolvers<void>();
    await page.route("**/api/v1/workbench/repositories/1/1?*", async (route) => {
      await pending.promise;
      await route.fulfill(
        outcome === "failure"
          ? { status: 503, json: { ok: false, error: "unavailable" } }
          : { json: outcome === "empty" ? workbenchFixture() : visualSnapshot() },
      );
    });
    try {
      await page.goto(consoleUrl());
      await page.getByRole("button", { name: "Open", exact: true }).first().click();
      const progress = page.getByRole("progressbar", { name: "Loading snapshot" });
      await expect(progress).toBeVisible();
      await expect(progress).not.toHaveAttribute("aria-valuenow");
      await expect(page.getByText("Ledger revision")).not.toBeVisible();
      expect(
        await progress.evaluate((element) => getComputedStyle(element, "::after").animationName),
      ).toBe("none");
      await capture(page, `loading-${outcome}`);
    } finally {
      pending.resolve();
    }
    await expect(page.getByRole("progressbar")).toHaveCount(0);
    await expect(
      outcome === "failure"
        ? page.getByRole("heading", { name: "Service unavailable" })
        : outcome === "empty"
          ? page.getByText("No plans in this snapshot.")
          : page.getByRole("cell", { name: "runner_capacity_unavailable" }),
    ).toBeVisible();
  });
}

test("retrying a failed governance read does not claim a retained observation", async ({
  page,
}) => {
  await prepare(page);
  await page.route("**/api/v1/workbench/repositories/1/1/governance-comparison", (route) =>
    route.fulfill({ status: 503, json: { ok: false, error: "unavailable" } }),
  );
  await page.goto(consoleUrl());
  await page.getByRole("button", { name: "Open", exact: true }).first().click();
  await navigate(page, "Governance");
  await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
  const pending = Promise.withResolvers<void>();
  await page.route("**/api/v1/workbench/repositories/1/1/governance-comparison", async (route) => {
    await pending.promise;
    await route.fulfill({ json: governanceComparisonFixture() });
  });
  try {
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(
      page.getByRole("progressbar", { name: "Refreshing governance evidence", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("previous observation shown", { exact: false })).toHaveCount(0);
    await expect(page.getByText("bytes match", { exact: true })).toHaveCount(0);
  } finally {
    pending.resolve();
  }
  await expect(page.getByRole("progressbar")).toHaveCount(0);
  await expect(page.getByText("bytes match", { exact: true })).toBeVisible();
});

test("post-approval refresh preserves the receipt and reason while pausing baseline commands", async ({
  page,
}) => {
  await prepare(page);
  await page.goto(consoleUrl());
  await page.getByRole("button", { name: "Open", exact: true }).first().click();
  await navigate(page, "Governance");
  await expect(page.getByRole("button", { name: "Replace baseline" })).toBeVisible();
  await page.getByLabel("Approval reason").fill("Confirm repository governance intent");
  await expect(page.getByRole("button", { name: "Replace baseline" })).toBeEnabled();
  const pending = Promise.withResolvers<void>();
  let approvedBaseline = governanceBaselineApprovalFixture().baseline;
  await page.route("**/api/v1/workbench/repositories/1/1/governance-comparison", async (route) => {
    await pending.promise;
    await route.fulfill({ json: governanceComparisonFixture({ baseline: approvedBaseline }) });
  });
  await page.route("**/api/v1/workbench/repositories/1/1/governance-baselines", async (route) => {
    const command = route.request().postDataJSON();
    const receipt = governanceBaselineApprovalFixture();
    approvedBaseline = {
      ...receipt.baseline,
      operationId: command.operationId,
      reason: command.reason,
      supersedes: command.expectedActive,
      pointer: {
        ...receipt.baseline.pointer,
        baselineId: `governance-baseline:${"8".repeat(64)}`,
        version: command.expectedActive.version + 1,
      },
    };
    await route.fulfill({
      status: 201,
      json: {
        ...receipt,
        requestOperationId: command.operationId,
        baseline: approvedBaseline,
      },
    });
  });
  try {
    await page.getByRole("button", { name: "Replace baseline" }).click();
    await expect(
      page.getByRole("progressbar", {
        name: "Refreshing governance evidence; previous observation shown",
      }),
    ).toBeVisible();
    await expect(page.getByText("bytes match", { exact: true })).toBeVisible();
    await expect(page.getByText("Expected governance state approved")).toBeVisible();
    await expect(page.getByLabel("Approval reason")).toHaveValue(
      "Confirm repository governance intent",
    );
    await expect(page.getByRole("button", { name: "Replace baseline" })).toBeDisabled();
    await expect(page.getByLabel("Approval reason")).toBeDisabled();
  } finally {
    pending.resolve();
  }
  await expect(page.getByRole("progressbar")).toHaveCount(0);
  await expect(page.getByText("Expected governance state approved")).toBeVisible();
  await expect(page.getByLabel("Approval reason")).toHaveValue(
    "Confirm repository governance intent",
  );
  await expect(page.getByRole("button", { name: "Replace baseline" })).toBeEnabled();
});
