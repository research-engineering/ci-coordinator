import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import {
  configEpoch,
  configHeaders,
  configStatus,
  configurationSource,
  configValidation,
  sourceResponse,
} from "../configurationFixture";
import {
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "../fixture";

for (const width of [1440, 390]) {
  test(`configuration source, retained epochs and confirmed rollback at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const commands: { readonly path: string; readonly body: string }[] = [];
    const unexpected: string[] = [];
    let registrations = 0;
    let statusReads = 0;
    let holdSource = false;
    const sourceStarted = Promise.withResolvers<void>();
    const sourceReleased = Promise.withResolvers<void>();
    const sourceFinished = Promise.withResolvers<void>();
    await page.route("**/api/**", async (route) => {
      unexpected.push(route.request().url());
      await route.fulfill({ status: 404, json: { ok: false } });
    });
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({
        json: controlPlaneSessionFixture({ roles: ["activate", "configure", "read"] }),
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
    await page.route("**/api/v1/workbench/repositories/1/1/workflow-discovery*", (route) =>
      route.fulfill({ json: workflowDiscoveryFixture() }),
    );
    await page.route("**/api/v1/config/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "POST") commands.push({ path, body: request.postData() ?? "" });
      if (path.endsWith("/validations")) {
        expect(request.postDataJSON()).toEqual({
          schemaVersion: "ci-config-epoch-validation/v1",
          source: configurationSource,
          sourceFormat: "json",
        });
        await route.fulfill({ json: configValidation(), headers: configHeaders });
      } else if (path.endsWith("/epochs")) {
        registrations += 1;
        if (registrations === 1) await route.abort("failed");
        else
          await route.fulfill({
            headers: configHeaders,
            json: {
              schemaVersion: "ci-config-epoch-registration-result/v1",
              ok: true,
              epochId: configEpoch().epochId,
              duplicate: true,
            },
          });
      } else if (path.endsWith("/status")) {
        statusReads += 1;
        expect([...new URL(request.url()).searchParams]).toEqual([["limit", "50"]]);
        await route.fulfill({ json: configStatus(), headers: configHeaders });
      } else if (path.endsWith("/source")) {
        if (holdSource) {
          sourceStarted.resolve();
          await sourceReleased.promise;
          if (request.failure()) {
            sourceFinished.resolve();
            return;
          }
        }
        const response = sourceResponse();
        await route.fulfill({
          body: Buffer.from(await response.arrayBuffer()),
          headers: Object.fromEntries(response.headers),
        });
        if (holdSource) sourceFinished.resolve();
      } else if (path.endsWith("/rollbacks")) {
        expect(request.postDataJSON()).toMatchObject({
          schemaVersion: "ci-config-epoch-rollback/v1",
          expectedRevision: 4,
          targetEpochId: configEpoch().epochId,
          reason: "Restore reviewed source",
        });
        await route.fulfill({
          headers: configHeaders,
          json: {
            schemaVersion: "ci-config-epoch-activation-result/v1",
            ok: true,
            epochId: configEpoch().epochId,
            duplicate: false,
            revision: 5,
          },
        });
      } else {
        unexpected.push(request.url());
        await route.fulfill({ status: 404, json: { ok: false } });
      }
    });
    const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
    if (!base) throw new Error("Browser server unavailable");
    await page.goto(
      new URL("/workbench?installationId=1&repositoryId=1&limit=10&view=configuration", base).href,
    );
    await expect(page.getByRole("heading", { name: "Configuration", level: 1 })).toBeVisible();
    await page.getByRole("tab", { name: "Retained epochs" }).click();
    await page
      .getByRole("button", { name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}` })
      .click();
    expect(statusReads).toBeGreaterThan(0);
    expect(registrations).toBe(0);
    const unexpectedDownloads: string[] = [];
    const recordDownload = (download: { suggestedFilename(): string }) =>
      unexpectedDownloads.push(download.suggestedFilename());
    page.on("download", recordDownload);
    holdSource = true;
    await page.getByRole("button", { name: "Download source" }).click();
    await sourceStarted.promise;
    const cancelledSource = page.waitForEvent("requestfailed", {
      predicate: (request) => new URL(request.url()).pathname.endsWith("/source"),
    });
    await navigate(page, "Repositories");
    expect((await cancelledSource).failure()).not.toBeNull();
    sourceReleased.resolve();
    await sourceFinished.promise;
    expect(unexpectedDownloads).toEqual([]);
    page.off("download", recordDownload);
    holdSource = false;
    await navigate(page, "Configuration");
    await page.getByRole("tab", { name: "Source" }).click();
    await page.getByLabel("Source format").selectOption("json");
    await page.getByLabel("Upload configuration file").setInputFiles({
      name: "configuration.json",
      mimeType: "application/json",
      buffer: Buffer.from(configurationSource),
    });
    await expect(page.getByRole("textbox", { name: "Source", exact: true })).toHaveValue(
      configurationSource,
    );
    await page.getByRole("button", { name: "Validate source" }).click();
    await expect(page.getByText("Source validated for repository 1.")).toBeVisible();
    await page.getByRole("button", { name: "Review registration" }).click();
    await expect(page.getByRole("button", { name: "Confirm registration" })).toBeFocused();
    expect(registrations).toBe(0);
    await expectAccessibleLayout(page);
    await page.screenshot({ path: testInfo.outputPath(`source-${width}.png`), fullPage: true });
    await page.getByRole("button", { name: "Confirm registration" }).click();
    await expect(page.getByText(/Registration outcome unknown/)).toBeVisible();
    await page.getByRole("tab", { name: "Retained epochs" }).click();
    await navigate(page, "Repositories");
    expect(registrations).toBe(1);
    await navigate(page, "Configuration");
    await page.getByRole("button", { name: "Retry same command" }).click();
    await expect(page.getByText(/Source registration confirmed/)).toBeVisible();
    const registrationBodies = commands
      .filter((command) => command.path.endsWith("/epochs"))
      .map((command) => command.body);
    expect(registrationBodies).toHaveLength(2);
    expect(registrationBodies[1]).toBe(registrationBodies[0]);
    await page
      .getByRole("button", { name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}` })
      .click();
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download source" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe(`configuration-${configEpoch().epochId}.json`);
    const stream = await download.createReadStream();
    if (!stream) throw new Error("Verified download is unavailable");
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(Buffer.from(chunk));
    expect(Buffer.concat(chunks)).toEqual(Buffer.from(configurationSource));
    await expect(page.getByText("Verified source download started.")).toBeVisible();
    await page.getByLabel("Reason", { exact: true }).fill("Restore reviewed source");
    await page.getByRole("button", { name: "Review rollback" }).click();
    expect(commands.filter((command) => command.path.endsWith("/rollbacks"))).toHaveLength(0);
    await expect(page.getByRole("button", { name: "Confirm rollback" })).toBeFocused();
    await expectAccessibleLayout(page);
    await page.screenshot({ path: testInfo.outputPath(`rollback-${width}.png`), fullPage: true });
    await page.keyboard.press("Enter");
    await expect(page.getByText("Rollback confirmed at revision 5.")).toBeVisible();
    expect(commands.filter((command) => command.path.endsWith("/rollbacks"))).toHaveLength(1);
    await page.getByRole("tab", { name: "Retained epochs" }).focus();
    await page.keyboard.press("Home");
    await expect(page.getByRole("tab", { name: "Source" })).toBeFocused();
    await page.getByRole("link", { name: "Review workflow proposal" }).click();
    await expect(page.getByRole("heading", { name: "Workflows", level: 1 })).toBeFocused();
    expect(commands.some((command) => command.path.endsWith("/activations"))).toBe(false);
    expect(unexpected).toEqual([]);
  });
}

async function navigate(page: Page, name: string) {
  const link = page.getByRole("link", { name, exact: true });
  if (!(await link.isVisible()))
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  await link.click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
}

async function expectAccessibleLayout(page: Page) {
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const dimensions = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.width);
}
