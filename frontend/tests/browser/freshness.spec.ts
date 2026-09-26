import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import type { ExpectedActiveEpoch } from "../../src/api/repositoryAttestation/client";
import { archiveDetailPage, archivePage, archiveQuery, retentionPreview } from "../archiveFixture";
import { configEpoch, configHeaders, configStatus } from "../configurationFixture";
import {
  configActivationFixture,
  controlPlaneSessionFixture,
  installationCatalogFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "../fixture";
import { historyDataset, historyStatus } from "../historyFixture";

// API evidence is controlled; the existing server serves the real ASGI console shell.
// Every case uses the configured desktop, Pixel 7 and 320px Chromium projects.
const A1 = { epochId: "a".repeat(64), revision: 1 };
const proposalTarget = workflowDiscoveryFixture().proposal.admittedEpochId;
if (proposalTarget === null) throw new Error("The reviewable fixture must have a target epoch");
const T2 = { epochId: proposalTarget, revision: 2 };
const C3 = { epochId: "c".repeat(64), revision: 3 };
function snapshot(active: ExpectedActiveEpoch) {
  return workbenchFixture({
    configEpochs: [
      {
        active: true,
        activeRevision: active.revision,
        epochId: active.epochId,
        sourceFormat: "json",
        sourceHash: "d".repeat(64),
        documentHash: "e".repeat(64),
        epochHash: "f".repeat(64),
        documentSchemaId: "ci-repository-policy/v1",
        documentProfileId: "ci-policy-document/v1",
        semanticProfileId: "ci-repository-policy-semantics/v1",
        compiledSchemaId: "ci-compiled-repository-policy/v1",
      },
    ],
  });
}
function url(view: string, repository = 1) {
  const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!base) throw new Error("Real browser shell server unavailable");
  return new URL(
    `/workbench?installationId=1&repositoryId=${repository}&limit=10&view=${view}`,
    base,
  ).href;
}
async function shell(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({
      json: controlPlaneSessionFixture({ roles: ["activate", "audit", "configure", "read"] }),
    }),
  );
  await page.route("**/api/v1/workbench/installations?*", (route) =>
    route.fulfill({ json: installationCatalogFixture() }),
  );
  await page.route("**/api/v1/workbench/installations/1/repositories?*", (route) =>
    route.fulfill({ json: repositoryPageFixture() }),
  );
  await page.route("**/api/v1/workbench/repositories/1/1/workflow-discovery*", (route) =>
    route.fulfill({ json: workflowDiscoveryFixture() }),
  );
  return errors;
}
async function navigate(page: Page, name: string) {
  const link = page.getByRole("link", { name, exact: true });
  if (!(await link.isVisible()))
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  await link.click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
}
async function layout(page: Page, errors: string[], artifact: string) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(errors).toEqual([]);
  await page.screenshot({ path: test.info().outputPath(artifact), fullPage: true });
}

test("uncertain activation survives a display-limit change; duplicate T/2 is not current C/3", async ({
  page,
}) => {
  const errors = await shell(page);
  let active = A1;
  const bodies: string[] = [];
  let discoveryReads = 0;
  const releaseDiscovery = Promise.withResolvers<void>();
  await page.route("**/api/v1/workbench/repositories/1/1/workflow-discovery*", async (route) => {
    discoveryReads++;
    if (discoveryReads > 1) await releaseDiscovery.promise;
    await route.fulfill({ json: workflowDiscoveryFixture() });
  });
  await page.route("**/api/v1/workbench/repositories/1/1?*", (route) =>
    route.fulfill({ json: snapshot(active) }),
  );
  await page.route("**/api/v1/repository-attestations/github/start", (route) =>
    route.fulfill({ status: 409, json: { ok: false, error: "already_reviewed" } }),
  );
  await page.route("**/api/v1/config/activations", async (route) => {
    bodies.push(route.request().postData() ?? "");
    if (bodies.length === 1) await route.abort("failed");
    else
      await route.fulfill({
        json: configActivationFixture({ duplicate: true, epochId: T2.epochId, revision: 2 }),
      });
  });
  try {
    await page.goto(url("workflows"));
    await page.getByRole("button", { name: "Verify authority", exact: true }).click();
    await page.getByRole("button", { name: "Activate proposal" }).click();
    await expect(page.getByText(/The outcome is unconfirmed/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Refresh workflow discovery" })).toBeDisabled();
    active = C3;
    await navigate(page, "Repositories");
    await page.getByText("Advanced degraded access", { exact: true }).click();
    await expect(page.getByRole("spinbutton", { name: "Installation", exact: true })).toHaveValue(
      "1",
    );
    await expect(page.getByRole("spinbutton", { name: "Repository", exact: true })).toHaveValue(
      "1",
    );
    await page.getByRole("spinbutton", { name: "Items per section" }).fill("20");
    const refreshed = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/workbench/repositories/1/1" &&
        new URL(response.url()).searchParams.get("limit") === "20",
    );
    await page.getByRole("button", { name: "Load snapshot" }).click();
    await refreshed;
    await expect(page.getByRole("button", { name: "Refresh repository snapshot" })).toBeEnabled();
    await navigate(page, "Workflows");
    expect(discoveryReads).toBe(1);
    expect(bodies).toHaveLength(1);
    await page.getByRole("button", { name: "Retry configuration activation" }).click();
    await expect(page.getByText("Activation already recorded")).toBeVisible();
    await expect(page.getByText("Activation recorded at revision 2")).toBeVisible();
    await expect(page.getByText(/Current observed revision 3/)).toBeVisible();
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toBe(bodies[0]);
    expect(JSON.parse(bodies[1] ?? "")).toMatchObject({
      expectedRevision: 1,
      targetEpochId: "4".repeat(64),
    });
    await expect(page.getByText(/now authoritative/)).toHaveCount(0);
    await layout(page, errors, "historical-receipt-current-read.png");
  } finally {
    releaseDiscovery.resolve();
  }
});

test("a real rollback callback refreshes the shared baseline before fresh authority verification", async ({
  page,
}) => {
  const errors = await shell(page);
  const target = configEpoch();
  const original = configStatus();
  let applied = false;
  const starts: unknown[] = [];
  await page.route("**/api/v1/workbench/repositories/1/1?*", (route) =>
    route.fulfill({
      json: snapshot(applied ? { epochId: target.epochId, revision: 5 } : (original.active ?? A1)),
    }),
  );
  await page.route("**/api/v1/config/repositories/1/1/status?*", (route) =>
    route.fulfill({
      headers: configHeaders,
      json: {
        ...original,
        active: applied ? { epochId: target.epochId, revision: 5 } : original.active,
      },
    }),
  );
  await page.route("**/api/v1/config/rollbacks", async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      expectedRevision: 4,
      targetEpochId: target.epochId,
    });
    applied = true;
    await route.fulfill({
      headers: configHeaders,
      json: configActivationFixture({ epochId: target.epochId, revision: 5 }),
    });
  });
  await page.route("**/api/v1/repository-attestations/github/start", async (route) => {
    starts.push(route.request().postDataJSON());
    await route.fulfill({ status: 409, json: { ok: false, error: "already_reviewed" } });
  });
  await page.goto(url("configuration"));
  await page.getByRole("tab", { name: "Retained epochs" }).click();
  await page.getByRole("button", { name: `Inspect epoch ${target.epochId.slice(0, 12)}` }).click();
  await page.getByLabel("Reason", { exact: true }).fill("Restore reviewed source");
  await page.getByRole("button", { name: "Review rollback" }).click();
  await page.getByRole("button", { name: "Confirm rollback" }).click();
  await expect(page.getByText("Rollback confirmed at revision 5.")).toBeVisible();
  await expect(page.getByText("Active revision 5", { exact: true })).toBeVisible();
  await navigate(page, "Workflows");
  expect(starts).toEqual([]);
  await page.getByRole("button", { name: "Verify authority", exact: true }).click();
  await expect(page.getByRole("button", { name: "Activate proposal" })).toBeVisible();
  expect(starts).toEqual([
    expect.objectContaining({ expectedActive: { epochId: target.epochId, revision: 5 } }),
  ]);
  await layout(page, errors, "rollback-fresh-verification.png");
});

test("manual and confirmed retention refresh both selected evidence surfaces without losing permanent statistics", async ({
  page,
}) => {
  const errors = await shell(page);
  let revision = 5;
  let jobReads = 0;
  let detailReads = 0;
  const reviewed = retentionPreview();
  const before = archiveDetailPage().detail;
  const after = { ...before, state: "expired" as const, content: "expired" as const };
  reviewed.preview.effects = [
    {
      key: { workflowRunId: 101, runAttempt: 1 },
      before,
      after,
      payloadBytes: 128,
      deletePayload: true,
    },
  ];
  reviewed.preview.deletedDetails = 1;
  reviewed.preview.releasedBytes = 128;
  await page.route("**/api/v1/workbench/repositories/1/2?*", (route) =>
    route.fulfill({ json: workbenchFixture({ scope: { installationId: 1, repositoryId: 2 } }) }),
  );
  await page.route("**/api/v2/economics/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/history"))
      return route.fulfill({
        json: historyStatus({
          ...historyDataset(),
          repositoryId: 2,
          generation: 3,
          configurationRevision: 4,
          dataRevision: revision,
        }),
      });
    if (path.endsWith("/preview")) {
      expect(route.request().postDataJSON()).toEqual(reviewed.preview.selection);
      return route.fulfill({ json: reviewed });
    }
    if (path.endsWith("/apply")) {
      const command = route.request().postDataJSON() as { operationId: string };
      revision = 6;
      return route.fulfill({
        json: {
          outcome: "committed",
          operationId: command.operationId,
          dataRevision: 6,
          preview: reviewed.preview,
        },
      });
    }
    if (path.includes("/history/attempts/")) {
      detailReads++;
      const value = archiveDetailPage();
      value.dataRevision = revision;
      if (revision === 6) {
        value.detail = { ...value.detail, state: "expired", content: "expired" };
        value.record.detail = value.detail;
        value.detailPayload = null;
        value.observedAt = "2026-10-12T10:00:00Z";
      }
      return route.fulfill({ json: value });
    }
    const value = archivePage(archiveQuery(path.endsWith("/jobs") ? "jobs" : "records"));
    if (path.endsWith("/jobs")) jobReads++;
    value.dataRevision = revision;
    const record = value.records[0];
    if (record) record.detail = revision === 6 ? after : before;
    return route.fulfill({ json: value });
  });
  await page.goto(`${url("economics", 2)}&economicsTab=history`);
  await page.getByRole("button", { name: /#101/ }).click();
  await expect(page.getByText(/Step 1: success/)).toBeVisible();
  const attempt = page.getByRole("region", { name: "Retained attempt", exact: true });
  await attempt.getByRole("button", { name: "Refresh evidence" }).click();
  await expect.poll(() => [jobReads, detailReads]).toEqual([2, 2]);
  await expect(page.getByText(/Step 1: success/)).toBeVisible();
  await page.getByRole("button", { name: "Manage detail retention" }).click();
  await page.getByRole("button", { name: "Preview detail deletion" }).click();
  await page.getByRole("button", { name: "Confirm reviewed change" }).click();
  await expect.poll(() => [jobReads, detailReads]).toEqual([3, 3]);
  await expect(
    page.getByText("Numbered steps expired; retained statistics remain available."),
  ).toBeVisible();
  await expect(page.getByText(/Step 1: success/)).toHaveCount(0);
  await expect(page.getByRole("cell", { name: "Ruff #301", exact: true })).toBeVisible();
  await expect(page.getByText(/Recorded data revision 6/)).toBeVisible();
  await layout(page, errors, "retention-current-evidence.png");
});
