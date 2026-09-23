import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import {
  ACTIVITY_ACTOR,
  ACTIVITY_CURSOR,
  activityContinuation,
  activityRequest,
} from "../activityFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../fixture";

test.use({ timezoneId: "America/New_York" });

test("activity supports accessible source navigation and bounded export in the production shell", async ({
  page,
}) => {
  const activityRequests: { path: string; params: [string, string][] }[] = [];
  const now = Date.now();
  await page.clock.install({ time: now });
  let expired = false;
  let renewed = false;
  let loginCount = 0;
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill(
      expired && !renewed
        ? { status: 401, json: { ok: false, error: "unauthenticated" } }
        : {
            json: controlPlaneSessionFixture({
              roles: ["audit", "configure", "read"],
              expiresAt: new Date(now + (renewed ? 600_000 : 60_000)).toISOString(),
            }),
          },
    ),
  );
  await page.route("**/api/v1/auth/keycloak/start", async (route) => {
    loginCount += 1;
    renewed = true;
    await route.fulfill({ status: 302, headers: { location: "/workbench" } });
  });
  await page.route("**/api/v1/workbench/installations?*", (route) =>
    route.fulfill({ json: installationCatalogFixture() }),
  );
  await page.route("**/api/v1/workbench/installations/1/repositories?*", (route) =>
    route.fulfill({ json: repositoryPageFixture() }),
  );
  await page.route("**/api/v1/workbench/repositories/1/1?*", (route) =>
    route.fulfill({ json: workbenchFixture() }),
  );
  await page.route("**/api/v1/activity/**", (route) => {
    const request = route.request();
    expect(request.method()).toBe("GET");
    const url = new URL(request.url());
    activityRequests.push({ path: url.pathname, params: [...url.searchParams].sort() });
    const { query, cursor } = activityRequest(url, { installationId: 1, repositoryId: 1 });
    return route.fulfill({ json: activityContinuation(query, cursor) });
  });
  const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!base) throw new Error("Browser server unavailable");
  await page.goto(
    new URL("/workbench?installationId=1&repositoryId=1&limit=10&view=activity", base).href,
  );
  await expect(page.getByRole("heading", { name: "Activity", exact: true })).toBeVisible();
  await expect(page.getByRole("table", { name: "Retained access events" })).toBeVisible();
  expect(await page.evaluate(() => new Date().getTimezoneOffset())).not.toBe(0);
  await expect(page.locator("time")).toContainText("01:00:00 UTC");
  const access = page.getByRole("tab", { name: "Access and sessions" });
  await access.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Repository actions" })).toBeFocused();
  await expect(page.getByRole("table", { name: "Repository activity references" })).toBeVisible();
  const businessDestination = new URL(
    "/workbench?installationId=1&repositoryId=1&limit=10&view=activity&activitySource=business",
    base,
  ).href;
  await expect(page).toHaveURL(businessDestination);
  await page.getByRole("tab", { name: "Access and sessions" }).click();
  await page.goBack();
  await expect(page.getByRole("tab", { name: "Repository actions" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("table", { name: "Repository activity references" })).toBeVisible();
  await page.getByRole("combobox", { name: "Action" }).selectOption("config-epoch-registration/v1");
  await page.getByRole("textbox", { name: "Exact actor ID" }).fill(ACTIVITY_ACTOR);
  await page.getByRole("button", { name: "Apply", exact: true }).click();
  await expect
    .poll(() => activityRequests.at(-1)?.params)
    .toContainEqual(["actor", ACTIVITY_ACTOR]);
  await expect(page.getByRole("table", { name: "Repository activity references" })).toBeVisible();
  const first = activityRequests.at(-1)?.params;
  if (!first) throw new Error("Applied activity query missing");
  expect(first).toContainEqual(["actor", ACTIVITY_ACTOR]);
  expect(first).toContainEqual(["action", "config-epoch-registration/v1"]);
  const continuation = [...first, ["cursor", ACTIVITY_CURSOR]].sort();
  await page.getByRole("button", { name: "Next activity page" }).click();
  await expect(page.getByText("Page 2", { exact: true })).toBeVisible();
  await expect(page.getByText(`audit_${"c".repeat(32)}`, { exact: true })).toBeAttached();
  await page.getByText("Details", { exact: true }).click();
  await expect(page.getByText(`audit_${"c".repeat(32)}`, { exact: true })).toBeVisible();
  expect(activityRequests.at(-1)?.params).toEqual(continuation);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: test.info().outputPath("administrator-activity.png"),
    fullPage: true,
  });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export page" }).click();
  const downloaded = await download;
  expect(downloaded.suggestedFilename()).toMatch(/^activity-business-\d{4}-\d{2}-\d{2}\.json$/);
  expect(activityRequests.at(-1)).toEqual({
    path: "/api/v1/activity/repositories/1/1/export",
    params: continuation,
  });
  const stream = await downloaded.createReadStream();
  if (!stream) throw new Error("Download stream unavailable");
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  expect(JSON.parse(Buffer.concat(chunks).toString("utf-8"))).toMatchObject({
    items: [{ sequence: 7 }],
    nextCursor: null,
  });
  const readsBeforeRenewal = activityRequests.length;
  expired = true;
  await page.clock.fastForward(60_000);
  await expect.poll(() => loginCount).toBe(1);
  await expect(page).toHaveURL(businessDestination);
  await expect(page.getByRole("tab", { name: "Repository actions" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("table", { name: "Repository activity references" })).toBeVisible();
  expect(activityRequests.length).toBeGreaterThan(readsBeforeRenewal);
  await expect(page.getByRole("textbox", { name: "Exact actor ID" })).toHaveValue("");
  await expect(page.locator("time")).toContainText("01:00:00 UTC");
  await page.reload();
  await expect(page.getByRole("tab", { name: "Repository actions" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("table", { name: "Repository activity references" })).toBeVisible();
});
