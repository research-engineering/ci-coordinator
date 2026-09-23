import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import ts from "typescript";
import { build } from "vite";
import {
  controlPlaneSessionFixture,
  repositoryPageFixture,
  workbenchFixture,
} from "../../dev/fixtures";
import { assertProductionAssets, productionBoundary } from "../../dev/productionBoundary";
import { response } from "../../dev/response";
import { FRONTEND_ROOT } from "../../dev/server";
import { controlPlaneSessionSchema } from "../../src/api/controlPlaneIdentity/schema";
import { repositoryPageSchema } from "../../src/api/providerInventory/schema";
import { workbenchSnapshotSchema } from "../../src/api/workbench/schema";

async function files(root: string): Promise<string[]> {
  const entries = await readdir(root, { withFileTypes: true });
  return (
    await Promise.all(
      entries.map((entry) =>
        entry.isDirectory() ? files(join(root, entry.name)) : [join(root, entry.name)],
      ),
    )
  ).flat();
}

test("runtime schemas reject malformed synthetic identity, scope and state", () => {
  expect(response(controlPlaneSessionSchema, controlPlaneSessionFixture()).status).toBe(200);
  expect(() =>
    response(controlPlaneSessionSchema, controlPlaneSessionFixture({ roles: ["read", "read"] })),
  ).toThrow();
  const validPage = repositoryPageFixture();
  expect(response(repositoryPageSchema, validPage).status).toBe(200);
  expect(() =>
    response(repositoryPageSchema, {
      ...validPage,
      installation: { ...validPage.installation, installationId: 2 },
    }),
  ).toThrow();
  const snapshot = workbenchFixture();
  expect(response(workbenchSnapshotSchema, snapshot).status).toBe(200);
  expect(() =>
    response(workbenchSnapshotSchema, {
      ...snapshot,
      scope: { installationId: 1, repositoryId: -1 },
    }),
  ).toThrow();
});

test("production modules and built assets exclude the development harness", async () => {
  const production = (await files(join(FRONTEND_ROOT, "src"))).filter((file) =>
    /\.[cm]?tsx?$/.test(file),
  );
  expect(production.length).toBeGreaterThan(0);
  for (const file of production) {
    const source = ts.createSourceFile(
      file,
      await readFile(file, "utf8"),
      ts.ScriptTarget.Latest,
      true,
    );
    const specifiers: string[] = [];
    const visit = (node: ts.Node) => {
      if (
        (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
        node.moduleSpecifier &&
        ts.isStringLiteral(node.moduleSpecifier)
      )
        specifiers.push(node.moduleSpecifier.text);
      if (
        ts.isCallExpression(node) &&
        (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
          node.expression.getText(source) === "require")
      ) {
        const specifier = node.arguments[0];
        expect(
          specifier && ts.isStringLiteral(specifier),
          `Nonliteral module dependency in ${file}`,
        ).toBe(true);
        if (specifier && ts.isStringLiteral(specifier)) specifiers.push(specifier.text);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
    for (const specifier of specifiers)
      expect(specifier, file).not.toMatch(/(^|\/)(dev|tests)(\/|$)/);
  }
  await assertProductionAssets(join(FRONTEND_ROOT, "dist"));
});

test("native build rejects aliased fixtures and copied synthetic assets", async () => {
  test.skip(test.info().project.name !== "desktop-chromium");
  const root = await mkdtemp(join(tmpdir(), "ci-production-boundary-"));
  try {
    await mkdir(join(root, "src"));
    await mkdir(join(root, "dev"));
    await writeFile(
      join(root, "index.html"),
      '<html lang="en"><body><script type="module" src="/src/main.tsx"></script></body></html>',
    );
    await writeFile(
      join(root, "src/main.tsx"),
      'document.body.textContent = "https://github.com/example-org/ci-coordinator/blob/master/docs/how-to/discover-installed-organizations.md";',
    );
    const options = {
      configFile: false as const,
      logLevel: "silent" as const,
      plugins: [productionBoundary()],
      root,
    };
    await build(options);
    await assertProductionAssets(join(root, "dist"));
    await mkdir(join(root, "public"));
    const copiedFixture = join(root, "public/copied-fixture.json");
    await writeFile(copiedFixture, JSON.stringify({ marker: "Synthetic session" }));
    await expect(build({ ...options, plugins: [productionBoundary()] })).rejects.toThrow(
      /Development data entered production asset/,
    );
    await rm(copiedFixture);
    // The innocuous label defeats a string-only oracle. Vite resolves the alias
    // to an owned dev module and the graph guard must reject that provenance.
    await writeFile(join(root, "dev/fixture.ts"), 'export const label = "Ordinary UI";');
    await writeFile(
      join(root, "src/main.tsx"),
      'import { label } from "hidden-fixture"; document.body.textContent = label;',
    );
    await expect(
      build({
        ...options,
        plugins: [productionBoundary()],
        resolve: { alias: { "hidden-fixture": join(root, "dev/fixture.ts") } },
      }),
    ).rejects.toThrow(/Development module entered production graph/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

for (const leaked of ["CI_COORDINATOR_DEMO_MODE", "Synthetic session", "bart.simpson"]) {
  test(`built assets reject copied development data: ${leaked}`, async () => {
    test.skip(test.info().project.name !== "desktop-chromium");
    const directory = await mkdtemp(join(tmpdir(), "ci-production-assets-"));
    try {
      await writeFile(join(directory, "index.html"), '<html lang="en"></html>');
      await writeFile(join(directory, "main.js"), 'document.title = "Ordinary UI";');
      await assertProductionAssets(directory);
      await writeFile(join(directory, "copied.json"), JSON.stringify({ leaked }));
      await expect(assertProductionAssets(directory)).rejects.toThrow(
        /Development data entered production asset/,
      );
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
}

test("ordinary built UI keeps its anonymous sign-in path", async ({ page }) => {
  const origin = process.env["CI_COORDINATOR_PLAYWRIGHT_BASE_URL"];
  if (!origin) throw new Error("Independent browser preview is unavailable");
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ status: 401, json: { ok: false, error: "unauthenticated" } }),
  );
  await page.route("**/api/v1/workbench/installations", (route) =>
    route.fulfill({
      status: 401,
      json: { ok: false, error: "unauthenticated", retryAfterSeconds: null },
    }),
  );
  await page.goto(new URL("/workbench", origin).href);
  await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Synthetic session" })).toHaveCount(0);
  expect(await page.evaluate(() => Reflect.has(window, "__ciDemoControl"))).toBe(false);
});
