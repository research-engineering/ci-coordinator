import { expect, type Page } from "@playwright/test";
import { DEMO_TIME, SCENARIOS, scenarioName } from "../../dev/scenarioNames";
import { test } from "./demoFixture";

async function navigate(page: Page, name: string) {
  const link = page.getByRole("link", { name, exact: true });
  if (!(await link.isVisible()))
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  await link.click();
}

for (const name of Object.keys(SCENARIOS).map(scenarioName)) {
  test.describe(`synthetic ${name}`, () => {
    test.use({ demoScenario: name });
    test("has deterministic visible data and repeats in a fresh context", async ({ demo }) => {
      for (let iteration = 0; iteration < 2; iteration += 1) {
        const page = demo.page;
        await expect(page.getByRole("region", { name: "Synthetic session" })).toBeVisible();
        await expect(page.getByRole("combobox", { name: "Scenario" })).toHaveValue(name);
        expect(await page.evaluate(() => new Date().toISOString())).toBe(DEMO_TIME);
        if (name === "empty") {
          await expect(
            page
              .locator(".catalog-message")
              .getByText("No authorized organizations", { exact: true }),
          ).toBeVisible();
          await expect(page.getByRole("combobox", { name: "Organization" })).toBeDisabled();
        } else if (name === "unauthorized") {
          await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
          await expect(
            page.getByText("Repositories authentication required.", { exact: true }),
          ).toBeVisible();
          await expect(page.getByRole("button", { name: "Open", exact: true })).toHaveCount(0);
        } else if (name === "provider-failure-retry") {
          await expect(page.getByText("Repositories unavailable.", { exact: true })).toBeVisible();
          const retried = page.waitForResponse(
            (response) => new URL(response.url()).pathname === "/api/v1/workbench/installations",
            { timeout: 5_000 },
          );
          await page.getByRole("button", { name: "Retry", exact: true }).click();
          expect((await retried).status()).toBe(200);
          await expect(page.getByRole("button", { name: "Open", exact: true })).toHaveCount(2);
        } else if (name === "runs") {
          await expect(page.getByRole("tab", { name: "Runs", selected: true })).toBeVisible();
          await expect(page.getByRole("cell", { name: "success", exact: true })).toBeVisible();
          await expect(page.getByRole("cell", { name: "failure", exact: true })).toBeVisible();
        } else if (name === "economics") {
          await expect(
            page.getByRole("button", { name: "Inspect run 4201, attempt 2" }),
          ).toBeVisible();
          await expect(
            page.getByRole("button", { name: "Inspect run 4202, attempt 2" }),
          ).toBeVisible();
        } else if (name === "stale-navigation") {
          await demo.scenario.staleRequested;
          await expect(page.getByRole("button", { name: "Release old response" })).toBeVisible();
          await expect(
            page.getByRole("button", { name: "Inspect run 4201, attempt 2" }),
          ).toHaveCount(0);
        } else {
          await expect(page.getByRole("heading", { name: "Repositories", level: 1 })).toBeVisible();
          await expect(page.getByRole("button", { name: "Open", exact: true })).toHaveCount(2);
        }
        const dimensions = await page.evaluate(() => ({
          width: document.documentElement.clientWidth,
          scroll: document.documentElement.scrollWidth,
        }));
        expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.width);
        expect(demo.scenario.commands).toEqual([]);
        expect(demo.blocked.filter((reason) => reason !== "websocket")).toEqual([]);
        if (iteration === 0) {
          const previous = demo.context;
          await demo.context.addCookies([
            { name: "synthetic-reset-witness", value: "old", url: page.url() },
          ]);
          await page.evaluate(() => {
            localStorage.setItem("synthetic-reset-witness", "old");
            sessionStorage.setItem("synthetic-reset-witness", "old");
          });
          await demo.reset();
          expect(demo.context).not.toBe(previous);
          expect(page.isClosed()).toBe(true);
          expect(await demo.context.cookies()).toEqual([]);
          expect(
            await demo.page.evaluate(() => [localStorage.length, sessionStorage.length]),
          ).toEqual([0, 0]);
        }
      }
    });
  });
}

test("visible controls select and reset without keeping old browser storage", async ({ demo }) => {
  const previous = demo.page;
  await previous.getByRole("combobox", { name: "Scenario" }).selectOption("empty");
  await expect.poll(() => demo.page !== previous && previous.isClosed()).toBe(true);
  await expect(demo.page.getByRole("combobox", { name: "Scenario" })).toHaveValue("empty");
  const empty = demo.page;
  await empty.getByRole("button", { name: "Reset session" }).click();
  await expect.poll(() => demo.page !== empty && empty.isClosed()).toBe(true);
  await expect(demo.page.getByRole("combobox", { name: "Scenario" })).toHaveValue("empty");
  await demo.page.getByRole("button", { name: "Close session" }).click();
  await demo.finished;
});

test.describe("synthetic economics and late delivery", () => {
  test.use({ demoScenario: "economics" });
  test("compares retained reports while keeping the causal non-claim", async ({ demo }) => {
    const page = demo.page;
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
      await page.getByRole("button", { name: "Registered runs", exact: true }).click();
    }
    await page.getByRole("button", { name: "Compare 2/2" }).click();
    await expect(page.getByRole("heading", { name: "Report comparison" })).toBeVisible();
    await expect(
      page.getByText("Coverage: not verified / Causal effect: not established"),
    ).toBeVisible();
    expect(demo.scenario.commands).toEqual([]);
  });

  test("an old response actually arrives after navigation and cannot replace the new repository", async ({
    demo,
  }) => {
    await demo.reset("stale-navigation");
    const page = demo.page;
    await demo.scenario.staleRequested;
    await navigate(page, "Repositories");
    await page
      .getByRole("row")
      .filter({ hasText: "synthetic-service" })
      .getByRole("button", { name: "Open", exact: true })
      .click();
    await navigate(page, "CI economics");
    await expect(page.getByRole("button", { name: "Inspect run 9002, attempt 2" })).toBeVisible();
    const target = new URL("/api/v2/economics/repositories/1/1/sources?limit=20", page.url()).href;
    const [delivered] = await Promise.all([
      page.waitForEvent("requestfinished", {
        predicate: (request) => request.url() === target,
        timeout: 5_000,
      }),
      page.getByRole("button", { name: "Release old response" }).click(),
    ]);
    const response = delivered.existingResponse();
    expect(response?.status()).toBe(200);
    expect(await response?.json()).toMatchObject({
      installationId: 1,
      repositoryId: 1,
      items: [
        { source: { attempt: { workflowRunId: 4202 } } },
        { source: { attempt: { workflowRunId: 4201 } } },
      ],
    });
    await page.evaluate(
      () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    );
    await expect(page.getByRole("button", { name: "Inspect run 4201, attempt 2" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Inspect run 4202, attempt 2" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Inspect run 9002, attempt 2" })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("repositoryId")).toBe("2");
  });
});
