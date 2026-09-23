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
  observationSnapshot,
  observationStatus,
  observationWorkflows,
} from "../observationFixture";

for (const pendingCommand of [false, true]) {
  test(`expired session returns without replay: pending command=${pendingCommand}`, async ({
    page,
  }) => {
    const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
    if (!base) throw new Error("Browser server unavailable");
    const destination = "/workbench?installationId=1&repositoryId=1&limit=10&view=economics";
    const now = Date.now();
    await page.clock.install({ time: now });
    let expired = false;
    let renewed = false;
    let loginCount = 0;
    let sourceReads = 0;
    let observationReads = 0;
    let snapshot: ObservationSnapshot | null = null;
    let releaseMutation: (() => void) | undefined;
    let mutationFinished: Promise<void> | undefined;
    const unexpected: string[] = [];
    const writes: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET") writes.push(request.url());
    });
    await page.route("**/api/**", async (route) => {
      unexpected.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
      await route.fulfill({ status: 404, json: { ok: false } });
    });
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
    await page.route("**/api/v2/economics/repositories/1/1/sources?*", (route) => {
      sourceReads += 1;
      return route.fulfill({ json: economicsSourcePage() });
    });
    await page.route("**/api/v2/economics/repositories/1/1/observation", (route) => {
      observationReads += 1;
      return route.fulfill({ json: observationStatus(snapshot) });
    });
    await page.route("**/api/v2/economics/repositories/1/1/observation/workflows?*", (route) =>
      route.fulfill({ json: observationWorkflows() }),
    );
    await page.route("**/api/v2/economics/repositories/1/1/observation/gaps?*", (route) =>
      route.fulfill({ json: observationGaps() }),
    );
    await page.route("**/api/v2/economics/observation", (route) => {
      expect(route.request().method()).toBe("POST");
      const command: ObservationCommand = route.request().postDataJSON();
      const result = observationMutation(command);
      snapshot = result.snapshot;
      const reply = new Promise<void>((resolve) => {
        releaseMutation = resolve;
      });
      mutationFinished = reply.then(() => route.fulfill({ json: result }));
      return mutationFinished;
    });
    await page.goto(new URL(destination, base).href);
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Registered runs", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Inspect run 4201, attempt 2" })).toBeVisible();
    if (pendingCommand) {
      await page.getByRole("tab", { name: "Observation", exact: true }).click();
      await page.getByRole("checkbox", { name: "Observation enabled" }).check();
      await page.getByRole("button", { name: "Save observation" }).click();
      await expect.poll(() => writes.length).toBe(1);
      await expect.poll(() => releaseMutation !== undefined).toBe(true);
      snapshot = observationSnapshot(
        { enabled: false, selector: { kind: "all", workflowIds: null }, backfillDays: 1 },
        2,
      );
    }
    const initialSourceReads = sourceReads;
    const initialObservationReads = observationReads;
    const returnDestination = pendingCommand
      ? `${destination}&economicsTab=observation`
      : destination;
    await expect(page).toHaveURL(new URL(returnDestination, base).href);
    expired = true;
    await page.clock.fastForward(60_000);
    await expect.poll(() => loginCount).toBe(1);
    await expect(page).toHaveURL(new URL(returnDestination, base).href);
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Registered runs", exact: true })).toBeVisible();
    if (pendingCommand) {
      await expect(page.getByRole("tab", { name: "Observation", exact: true })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await expect(page.getByText("Saved revision 2", { exact: true })).toBeVisible();
      await expect(page.getByRole("checkbox", { name: "Observation enabled" })).not.toBeChecked();
      expect(observationReads).toBeGreaterThan(initialObservationReads);
      releaseMutation?.();
      await mutationFinished;
      await expect(page.getByRole("checkbox", { name: "Observation enabled" })).not.toBeChecked();
      await expect(page.getByText("Saved revision 2", { exact: true })).toBeVisible();
      await expect(page.getByText("Saved revision 1", { exact: true })).toHaveCount(0);
    } else {
      await expect(page.getByRole("button", { name: "Inspect run 4201, attempt 2" })).toBeVisible();
      expect(sourceReads).toBeGreaterThan(initialSourceReads);
    }
    expect(writes.map((url) => new URL(url).pathname)).toEqual(
      pendingCommand ? ["/api/v2/economics/observation"] : [],
    );
    expect(unexpected).toEqual([]);
    const stored = await page.evaluate(() => ({ ...sessionStorage }));
    expect(Object.keys(stored)).toEqual(["ci-coordinator.session-return.v1"]);
    expect(JSON.parse(stored["ci-coordinator.session-return.v1"] ?? "null")).toEqual({
      version: 1,
      attemptedAt: expect.any(Number),
      returnTo: null,
    });
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
