import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { installationCatalogFixture, installationFixture, repositoryPageFixture } from "../fixture";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: { error: "unauthenticated", ok: false }, status: 401 }),
  );
});

function catalogUrl() {
  const base = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!base) throw new Error("browser test server URL is unavailable");
  return new URL("/workbench", base).href;
}

async function witness(page: Page, name: string) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: test.info().outputPath(`${name}.png`), fullPage: true });
}

test("single-page long-label native selector reserves chevron geometry and restores forced colors", async ({
  page,
}) => {
  const longLogin = `organization-${"long-name-".repeat(20)}end`;
  const other = installationFixture({ installationId: 2, accountLogin: longLogin });
  const reads: string[] = [];
  await page.route("**/api/v1/workbench/installations?*", (route) => {
    reads.push(route.request().url());
    return route.fulfill({
      json: installationCatalogFixture({ installations: [installationFixture(), other] }),
    });
  });
  await page.route("**/api/v1/workbench/installations/*/repositories?*", (route) => {
    reads.push(route.request().url());
    return route.fulfill({
      json: repositoryPageFixture({
        installation: route.request().url().includes("/2/repositories")
          ? other
          : installationFixture(),
        repositories: [],
        totalCount: 0,
      }),
    });
  });
  await page.goto(catalogUrl());
  const select = page.getByRole("combobox", { name: "Organization" });
  await expect(select).toHaveValue("1");
  await expect(page.getByText("No repositories.", { exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: /pages/ })).toHaveCount(0);
  const before = reads.length;
  await select.selectOption("2");
  await expect(select).toHaveValue("2");
  await expect(page.getByText("No repositories.", { exact: true })).toBeVisible();
  expect(reads.slice(before).map((url) => new URL(url).pathname)).toEqual([
    "/api/v1/workbench/installations/2/repositories",
  ]);
  await page.getByRole("searchbox", { name: "Search loaded page" }).fill("kept");
  const refreshStart = reads.length;
  await page.getByRole("button", { name: "Refresh repositories" }).click();
  await expect(select).toHaveValue("2");
  await expect(
    page.getByText("No matching repositories on this page.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("searchbox", { name: "Search loaded page" })).toHaveValue("kept");
  const refreshed = reads.slice(refreshStart).map((url) => new URL(url));
  expect(refreshed.some((url) => url.pathname.endsWith("/installations"))).toBe(true);
  const repositoryReads = refreshed.filter((url) => url.pathname.endsWith("/repositories"));
  expect(repositoryReads.length).toBeGreaterThan(0);
  expect(repositoryReads.every((url) => url.pathname.includes("/2/repositories"))).toBe(true);
  expect(reads.every((url) => new URL(url).searchParams.get("page") === "1")).toBe(true);
  const icon = select.locator("..").locator("svg");
  await expect(icon).toHaveCount(1);
  await expect(icon).toHaveAttribute("aria-hidden", "true");
  await expect(icon).toHaveAttribute("focusable", "false");
  await expect(select).toHaveCSS("appearance", "none");
  const geometry = await select.evaluate((element) => {
    const chevron = element.parentElement?.querySelector("svg");
    if (!chevron) throw new Error("Selector chevron is missing");
    const control = element.getBoundingClientRect();
    const arrow = chevron.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      tag: element.tagName,
      centerDelta: Math.abs(control.y + control.height / 2 - arrow.y - arrow.height / 2),
      endInset: control.right - arrow.right,
      textGap: arrow.left - (control.right - Number.parseFloat(style.paddingRight)),
      width: arrow.width,
      height: arrow.height,
      pointerEvents: getComputedStyle(chevron).pointerEvents,
      hitIsSelect:
        document.elementFromPoint(arrow.x + arrow.width / 2, arrow.y + arrow.height / 2) ===
        element,
      selectedText: (element as HTMLSelectElement).selectedOptions[0]?.text,
    };
  });
  expect(geometry.tag).toBe("SELECT");
  expect(geometry.centerDelta).toBeLessThanOrEqual(0.5);
  expect(geometry.endInset).toBeCloseTo(12, 0);
  expect(geometry.textGap).toBeGreaterThanOrEqual(8);
  expect(geometry.width).toBe(16);
  expect(geometry.height).toBe(16);
  expect(geometry.pointerEvents).toBe("none");
  expect(geometry.hitIsSelect).toBe(true);
  expect(geometry.selectedText).toBe(longLogin);
  await select.focus();
  await select.press("Tab");
  await expect(page.getByRole("searchbox", { name: "Search loaded page" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(select).toBeFocused();
  await expect(select).toHaveCSS("outline-style", "solid");
  await witness(page, "catalog-single-long-label-focus");
  await page.emulateMedia({ forcedColors: "active" });
  await expect(select).toHaveCSS("appearance", "auto");
  await expect(icon).toBeHidden();
  await expect(select).toBeEnabled();
  await expect(select).toBeFocused();
  await expect(select).toHaveCSS("outline-style", "solid");
  await select.press("ArrowUp");
  await select.press("Enter");
  await expect(select).toHaveValue("1");
  await expect(page.getByText("No repositories.", { exact: true })).toBeVisible();
  await witness(page, "catalog-forced-colors-native-selection");
});

for (const kind of ["organization", "repository"] as const) {
  test(`${kind} first, middle and empty last pages retain contextual back navigation`, async ({
    page,
  }) => {
    const pages: number[] = [];
    await page.route("**/api/v1/workbench/installations?*", (route) => {
      const current = Number(new URL(route.request().url()).searchParams.get("page"));
      if (kind === "organization") pages.push(current);
      return route.fulfill({
        json: installationCatalogFixture({
          page: current,
          hasNextPage: kind === "organization" && current < 3,
          installations: current === 3 ? [] : [installationFixture()],
        }),
      });
    });
    await page.route("**/api/v1/workbench/installations/1/repositories?*", (route) => {
      const current = Number(new URL(route.request().url()).searchParams.get("page"));
      if (kind === "repository") pages.push(current);
      return route.fulfill({
        json: repositoryPageFixture({
          page: current,
          hasNextPage: kind === "repository" && current < 3,
          totalCount: kind === "repository" ? (current === 3 ? 200 : 201) : 1,
          ...(current === 3 ? { repositories: [] } : {}),
        }),
      });
    });
    await page.goto(catalogUrl());
    await expect(page.getByRole("button", { name: "Open" })).toBeVisible();
    const nav = page.getByRole("navigation", {
      name: kind === "organization" ? "Organization pages" : "Repository pages",
    });
    const next = nav.getByRole("button", { name: /Next/ });
    const previous = nav.getByRole("button", { name: /Previous/ });
    for (const current of [1, 2, 3]) {
      await expect(nav).toHaveText(`Page ${current}`);
      if (current === 1) await expect(previous).toBeDisabled();
      else await expect(previous).toBeEnabled();
      if (current === 3) await expect(next).toBeDisabled();
      else await expect(next).toBeEnabled();
      expect(pages).toEqual(Array.from({ length: current }, (_, index) => index + 1));
      if (kind === "organization") {
        const placement = await nav.evaluate((element) => {
          const group = element.parentElement;
          const select = group?.querySelector("select");
          if (!group || !select) throw new Error("Pagination must belong to the selector group");
          const box = element.getBoundingClientRect();
          const control = select.getBoundingClientRect();
          return {
            insideToolbar: Boolean(group.closest(".catalog-toolbar")),
            leftDelta: Math.abs(box.left - control.left),
            gap: box.top - control.bottom,
            fits: box.right <= group.getBoundingClientRect().right + 1,
          };
        });
        expect(placement.insideToolbar).toBe(true);
        expect(placement.leftDelta).toBeLessThanOrEqual(1);
        expect(placement.gap).toBeGreaterThanOrEqual(0);
        expect(placement.gap).toBeLessThanOrEqual(8);
        expect(placement.fits).toBe(true);
      }
      await witness(page, `${kind}-page-${current}`);
      if (current < 3) await next.click();
    }
    await expect(page.getByRole("button", { name: "Open" })).toHaveCount(0);
    await previous.focus();
    await page.keyboard.press("Enter");
    await expect(nav).toHaveText("Page 2");
    expect(pages).toEqual([1, 2, 3, 2]);
    await previous.click();
    await expect(nav).toHaveText("Page 1");
    expect(pages).toEqual([1, 2, 3, 2, 1]);
  });

  test(`${kind} pending, rate-limit failure and retry preserve page lifetime`, async ({ page }) => {
    const pending = Promise.withResolvers<void>();
    let recover = false;
    const pages: number[] = [];
    await page.route(
      /\/api\/v1\/workbench\/installations(?:\/1\/repositories)?\?/,
      async (route) => {
        const url = new URL(route.request().url());
        const current = Number(url.searchParams.get("page"));
        const organization = url.pathname.endsWith("/installations");
        if (organization === (kind === "organization")) {
          pages.push(current);
          if (current === 2 && !recover) {
            await pending.promise;
            return route.fulfill({
              status: 429,
              json: { error: "rate_limited", ok: false, retryAfterSeconds: 30 },
            });
          }
        }
        return route.fulfill({
          json: organization
            ? installationCatalogFixture({
                page: current,
                hasNextPage: current === 1,
                complete: false,
                failures: [{ installationId: 2, reason: "unavailable", retryAfterSeconds: null }],
              })
            : repositoryPageFixture({ page: current, hasNextPage: current === 1, totalCount: 101 }),
        });
      },
    );
    await page.goto(catalogUrl());
    await expect(page.getByRole("button", { name: "Open" })).toBeVisible();
    await expect(page.getByText("Partial catalog: 1 installation unavailable.")).toBeVisible();
    await page.getByRole("button", { name: `Next ${kind} page` }).click();
    const nav = page.getByRole("navigation", {
      name: kind === "organization" ? "Organization pages" : "Repository pages",
    });
    try {
      await expect(
        page.getByRole("progressbar", {
          name: `Loading ${kind === "organization" ? "organizations" : "repositories"}`,
        }),
      ).toBeVisible();
      await expect(nav).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Open" })).toHaveCount(0);
      await witness(page, `${kind}-loading`);
    } finally {
      pending.resolve();
    }
    await expect(
      page.getByText("GitHub rate limit reached. Retry after 30 seconds."),
    ).toBeVisible();
    await expect(nav).toHaveCount(0);
    expect(pages).toEqual([1, 2]);
    await witness(page, `${kind}-rate-limit`);
    recover = true;
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(nav).toHaveText("Page 2");
    await expect(nav.getByRole("button", { name: /Previous/ })).toBeEnabled();
    await expect(nav.getByRole("button", { name: /Next/ })).toBeDisabled();
    await expect(page.getByText("Partial catalog: 1 installation unavailable.")).toBeVisible();
    expect(pages).toEqual([1, 2, 2]);
    await witness(page, `${kind}-recovered`);
  });
}
