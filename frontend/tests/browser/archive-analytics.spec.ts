import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { analyticsQuerySchema } from "../../src/api/ciEconomics/analyticsSchema";
import { archiveQuerySchema } from "../../src/api/ciEconomics/archiveReadSchema";
import { analyticsReport } from "../analyticsFixture";
import { archivePage } from "../archiveFixture";
import { economicsSourcePage } from "../economicsConsoleFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../fixture";
import { historyStatus } from "../historyFixture";
import { purposeSnapshot } from "../purposeSettingsFixture";

test.use({ timezoneId: "America/New_York" });

test("archive and analytics remain readable, scoped and accessible in the production shell", async ({
  page,
}) => {
  let analyticsUpdatedAt: string | undefined;
  let analyticsMinute = 0;
  let analyticsReads = 0;
  const archiveRequests: URL[] = [];
  const repairCommands: unknown[] = [];
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
  await page.route("**/api/v2/economics/**", (route) => {
    const request = route.request();
    if (new URL(request.url()).pathname.endsWith("/history/gaps/retry")) {
      expect(request.method()).toBe("POST");
      const command = request.postDataJSON() as { operationId: string };
      repairCommands.push(command);
      return route.fulfill({
        json: {
          outcome: "committed",
          operationId: command.operationId,
          receipt: {
            request: command,
            intervals: [{ workflowRunId: 101, fromAttempt: 1, throughAttempt: 1 }],
          },
        },
      });
    }
    expect(request.method()).toBe("GET");
    const url = new URL(request.url());
    if (url.pathname.endsWith("/history")) return route.fulfill({ json: historyStatus() });
    if (url.pathname.endsWith("/analytics/settings"))
      return route.fulfill({
        json: { outcome: "available", snapshot: purposeSnapshot(), unavailable: null },
      });
    if (url.pathname.endsWith("/history/analytics")) {
      analyticsReads += 1;
      const query = analyticsQuerySchema.parse({
        installationId: 1,
        repositoryId: 1,
        generation: 1,
        createdFrom: url.searchParams.get("createdFrom"),
        createdUntil: url.searchParams.get("createdUntil"),
        horizonDays: Number(url.searchParams.get("horizonDays")),
      });
      const report = analyticsReport(query);
      if (analyticsMinute)
        report.observedAt = new Date(
          Date.parse(query.createdUntil) + analyticsMinute * 60_000,
        ).toISOString();
      const second = report.buckets[1];
      const third = report.buckets[2];
      if (second && third) {
        report.buckets[1] = { ...third, day: second.day };
        report.buckets[2] = { ...second, day: third.day };
      }
      analyticsUpdatedAt = report.observedAt;
      return route.fulfill({ json: { outcome: "available", report, unavailable: null } });
    }
    if (url.pathname.includes("/history/archive/")) {
      archiveRequests.push(url);
      const query = archiveQuerySchema.parse({
        installationId: 1,
        repositoryId: 1,
        generation: 1,
        createdFrom: url.searchParams.get("created_from"),
        createdThrough: url.searchParams.get("created_through"),
        kind: url.pathname.endsWith("jobs")
          ? "jobs"
          : url.pathname.endsWith("gaps")
            ? "gaps"
            : "records",
        workflowRunId: url.searchParams.has("workflow_run_id")
          ? Number(url.searchParams.get("workflow_run_id"))
          : null,
        runAttempt: url.searchParams.has("run_attempt")
          ? Number(url.searchParams.get("run_attempt"))
          : null,
      });
      const result = archivePage(query);
      return route.fulfill({
        json: {
          ...result,
          records: result.records.map((record) => ({
            ...record,
            header: { ...record.header, attempt: { ...record.header.attempt, repositoryId: 1 } },
          })),
          gaps:
            query.kind === "gaps"
              ? [
                  {
                    gapId: "b".repeat(64),
                    recordedAt: "2026-09-12T10:00:00Z",
                    configurationRevision: 4,
                    reason: "retry_exhausted",
                    workflowRunId: 101,
                    runAttempt: 1,
                    sourceWindow: null,
                    runCreatedAt: "2026-09-10T09:00:00Z",
                    retained: null,
                    retrySupported: true,
                    resolution: "missing",
                  },
                ]
              : [],
        },
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
  await expect(
    page.getByRole("heading", { name: "Retained runner usage", exact: true }),
  ).toBeVisible();
  await page.getByLabel("From (UTC)", { exact: true }).fill("2026-09-10");
  await page.getByLabel("Until, exclusive (UTC)", { exact: true }).fill("2026-09-13");
  await page.getByRole("button", { name: "Apply", exact: true }).click();
  await expect.poll(() => analyticsUpdatedAt).toBe("2026-09-13T00:00:00Z");
  const readsBeforeRefresh = analyticsReads;
  analyticsMinute = 1;
  const refreshed = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === "GET" && url.pathname.endsWith("/history/analytics");
  });
  await page
    .getByRole("heading", { name: "Usage and performance", exact: true })
    .locator("..")
    .getByRole("button", { name: "Refresh evidence", exact: true })
    .click();
  const refreshedUrl = new URL((await refreshed).url());
  expect(refreshedUrl.searchParams.get("createdFrom")).toBe("2026-09-10T00:00:00Z");
  expect(refreshedUrl.searchParams.get("createdUntil")).toBe("2026-09-13T00:00:00Z");
  await expect.poll(() => analyticsReads).toBe(readsBeforeRefresh + 1);
  await expect(
    page.getByRole("heading", { name: "Retained runner usage", exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => new Date().getTimezoneOffset())).not.toBe(0);
  if (analyticsUpdatedAt === undefined) throw new Error("Analytics response unavailable");
  const provenance = page
    .locator(".analytics-provenance")
    .filter({ hasText: /^Retained attempts by run-creation day\./ });
  await expect(provenance).toHaveCount(1);
  await expect(provenance).toContainText("13 Sep 2026, 00:01:00 UTC");
  const insets = await page
    .getByRole("region", { name: "Historical CI analytics", exact: true })
    .evaluate((section) => {
      const frame = section.getBoundingClientRect();
      return [".analytics-filters", ".usage-chart"].map((selector) => {
        const content = section.querySelector(selector)?.getBoundingClientRect();
        if (!content) throw new Error(`Missing analytics content: ${selector}`);
        return { leading: content.left - frame.left, trailing: frame.right - content.right };
      });
    });
  for (const inset of insets) {
    expect(inset.leading).toBeGreaterThanOrEqual(16);
    expect(inset.trailing).toBeGreaterThanOrEqual(16);
    expect(inset.leading).toBeCloseTo(inset.trailing, 1);
  }
  const plot = page.getByRole("img", { name: /Retained runner usage/ });
  await expect(plot.locator(".usage-observed-point")).toHaveCount(2);
  expect(
    await plot
      .locator(".usage-observed-point")
      .first()
      .evaluate((element) => ({
        stroke: getComputedStyle(element).strokeWidth,
        scaling: getComputedStyle(element).vectorEffect,
      })),
  ).toEqual({ stroke: "3px", scaling: "non-scaling-stroke" });
  const box = await plot.boundingBox();
  expect.soft(box?.width).toBeGreaterThan(100);
  expect.soft(box?.height).toBeGreaterThan(100);
  await expect.soft(page.getByText(/Daily measurements are incomplete/)).toBeVisible();
  expect.soft((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect
    .soft(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({ path: test.info().outputPath("archive-analytics.png"), fullPage: true });
  const beforeMetricChange = analyticsReads;
  await page.getByRole("combobox", { name: "Metric", exact: true }).selectOption("queue");
  await expect(page.getByText("No measured usage in this period.", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Metric", exact: true }).selectOption("failures");
  await expect(page.getByRole("img", { name: /Failed jobs/ })).toBeVisible();
  await page.getByRole("combobox", { name: "Metric", exact: true }).selectOption("runner");
  expect(analyticsReads).toBe(beforeMetricChange);
  const chart = page.getByRole("figure", { name: "Retained runner usage", exact: true });
  await chart.getByText("Data table", { exact: true }).click();
  await chart
    .getByRole("button", { name: "Inspect source runs for 2026-09-10", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "Source runs", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /#101/ })).toBeVisible();
  const sourceRequest = archiveRequests.at(-1);
  expect(sourceRequest?.pathname).toBe(
    "/api/v2/economics/repositories/1/1/history/archive/records",
  );
  expect(Object.fromEntries(sourceRequest?.searchParams ?? [])).toMatchObject({
    generation: "1",
    created_from: "2026-09-10T00:00:00.000Z",
    created_through: "2026-09-10T23:59:59.999999Z",
  });
  await expect(
    page.getByRole("region", { name: "Analytics source runs", exact: true }),
  ).toContainText("2026-09-10 to 2026-09-11 (exclusive UTC).");
  await page.getByRole("button", { name: "Back to analytics", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Retained runner usage", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "10 Sep 2026, 09:00:00 UTC", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /#101/ }).click();
  await expect.soft(page.getByRole("cell", { name: "Ruff #301", exact: true })).toBeVisible();
  const viewport = page.getByRole("region", { name: "Retained job table", exact: true });
  const job = viewport
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: "Ruff #301", exact: true }) });
  await expect(job).toHaveCount(1);
  await expect
    .soft(job.getByRole("cell", { name: "10 Sep 2026, 09:00:00 UTC", exact: true }))
    .toBeVisible();
  await expect
    .soft(job.getByRole("cell", { name: "10 Sep 2026, 09:00:02 UTC", exact: true }))
    .toBeVisible();
  await expect.soft(page.getByText(/availability in GitHub has not been checked/)).toBeVisible();
  await viewport.focus();
  await expect.soft(viewport).toBeFocused();
  expect.soft((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect
    .soft(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({ path: test.info().outputPath("archive-attempt.png"), fullPage: true });
  await page.getByRole("radio", { name: "Collection gaps", exact: true }).check();
  await expect(
    page.getByRole("cell", { name: "12 Sep 2026, 10:00:00 UTC", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("cell", { name: "Not retained", exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "Select retryable gaps on this page" }).check();
  await page.getByRole("button", { name: "Retry selected gaps", exact: true }).click();
  await expect(page.getByText("#101: attempt 1")).toBeVisible();
  await page.getByRole("button", { name: "Queue archive retries", exact: true }).click();
  await expect(page.getByText(/Archive retry admission confirmed/)).toBeVisible();
  expect(repairCommands).toHaveLength(1);
  expect(repairCommands[0]).toMatchObject({
    installationId: 1,
    repositoryId: 1,
    generation: 1,
    expectedRevision: 4,
    gapIds: ["b".repeat(64)],
  });
  const repairInsets = await page.locator(".archive-gap-repair-body").evaluate((body) => {
    const paragraph = body.querySelector("p");
    if (paragraph === null) throw new Error("Retry summary is missing");
    const outer = body.getBoundingClientRect();
    const inner = paragraph.getBoundingClientRect();
    return { left: inner.left - outer.left, right: outer.right - inner.right };
  });
  expect(repairInsets.left).toBeGreaterThanOrEqual(16);
  expect(repairInsets.right).toBeGreaterThanOrEqual(16);
  expect(Math.abs(repairInsets.left - repairInsets.right)).toBeLessThanOrEqual(1);
  expect.soft((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: test.info().outputPath("archive-gap-recovery.png"),
    fullPage: true,
  });
});
