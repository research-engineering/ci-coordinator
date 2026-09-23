import { readdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { normalizePath, type Plugin } from "vite";

const FORBIDDEN_ASSET_TEXT = [
  "__ciDemoControl",
  "CI_COORDINATOR_DEMO_MODE",
  "CI_COORDINATOR_DEMO_SCENARIO",
  "Usage: node dev/cli.mjs",
  "Synthetic session",
  "Synthetic operator",
  "synthetic-service",
  "synthetic-run-",
  "workbenchFixture",
  "controlPlaneSessionFixture",
  "Bart Simpson",
  "bart.simpson",
  `keycloak-human:v1:${"a".repeat(64)}`,
];

export function assertProductionModules(moduleIds: Iterable<string>, root: string) {
  const owner = normalizePath(resolve(root));
  const modules = new Set(Array.from(moduleIds, (id) => normalizePath(id.split("?", 1)[0] ?? id)));
  for (const id of modules) {
    if (
      id.startsWith(`${owner}/dev/`) ||
      id.startsWith(`${owner}/tests/`) ||
      /\/node_modules\/(?:@playwright\/|playwright(?:-core)?\/)/.test(id)
    )
      throw new Error(`Development module entered production graph: ${id}`);
  }
  for (const entry of ["index.html", "src/main.tsx"])
    if (!modules.has(`${owner}/${entry}`))
      throw new Error(`Production entry graph is missing ${entry}`);
}

async function* outputFiles(directory: string): AsyncGenerator<string> {
  const entries = await readdir(directory, { withFileTypes: true });
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) yield* outputFiles(path);
    else if (entry.isFile()) yield path;
    else throw new Error(`Unexpected production output entry: ${path}`);
  }
}

export async function assertProductionAssets(directory: string) {
  let foundIndex = false;
  let foundJavaScript = false;
  for await (const path of outputFiles(directory)) {
    if (path === join(directory, "index.html")) foundIndex = true;
    if (path.endsWith(".js")) foundJavaScript = true;
    const contents = await readFile(path);
    for (const marker of FORBIDDEN_ASSET_TEXT)
      if (contents.includes(Buffer.from(marker)))
        throw new Error(`Development data entered production asset ${path}: ${marker}`);
  }
  if (!foundIndex || !foundJavaScript)
    throw new Error("Production output must contain index.html and compiled JavaScript");
}

// Build-only wrapper: no imports from this module enter the client entry graph.
export function productionBoundary(): Plugin {
  let root = "";
  let outputDirectory = "";
  return {
    name: "production-demo-exclusion",
    apply: "build",
    enforce: "post",
    configResolved(config) {
      root = config.root;
      outputDirectory = resolve(root, config.build.outDir);
    },
    generateBundle() {
      // Resolved native graph catches aliases, re-exports and dynamic imports,
      // including demo imports whose code is later removed by tree shaking.
      assertProductionModules(this.getModuleIds(), root);
    },
    writeBundle: {
      order: "post",
      async handler() {
        // Read actual output, including public assets copied outside the graph.
        await assertProductionAssets(outputDirectory);
      },
    },
  };
}
