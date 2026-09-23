import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { expect, test } from "vitest";

const frontendRoot = process.cwd();
const frontendRequire = createRequire(import.meta.url);

test.each([
  { focused: false, guardOff: false, expected: ["focused", "other"] },
  { focused: true, guardOff: false, expected: [] },
  { focused: true, guardOff: true, expected: ["focused"] },
])(
  "blocking Playwright admission $focused/$guardOff",
  ({ focused, guardOff, expected }) => {
    const directory = mkdtempSync(join(tmpdir(), "ci-browser-policy-"));
    try {
      const corpus = join(directory, "corpus");
      mkdirSync(corpus);
      const marker = join(directory, "executed.txt");
      const config = join(directory, "playwright.config.ts");
      writeFileSync(
        config,
        `import base from ${JSON.stringify(join(frontendRoot, "playwright.config.ts"))};
export default { ...base, globalSetup: undefined, testDir: ${JSON.stringify(corpus)},
  projects: [{ name: "policy" }], outputDir: ${JSON.stringify(join(directory, "results"))},
  reporter: "line", workers: 1, retries: 0, ${guardOff ? "forbidOnly: false," : ""} };
`,
      );
      writeFileSync(
        join(corpus, "policy.spec.ts"),
        `import { test } from ${JSON.stringify(frontendRequire.resolve("@playwright/test"))};
import { appendFileSync } from "node:fs";
const mark = (name) => appendFileSync(${JSON.stringify(marker)}, name + "\\n");
test${focused ? ".only" : ""}("focused", () => mark("focused"));
test("other", () => mark("other"));
`,
      );
      const result = runNodeCli(
        [frontendRequire.resolve("@playwright/test/cli"), "test", "--config", config],
        directory,
      );
      expect(result.error).toBeUndefined();
      expect(result.signal).toBeNull();
      expect(result.status).toBe(expected.length ? 0 : 1);
      if (!expected.length) {
        expect(result.stdout + result.stderr).toContain("item focused with '.only' is not allowed");
      }
      const executed = existsSync(marker) ? readFileSync(marker, "utf8").trim().split("\n") : [];
      expect(executed.sort()).toEqual(expected);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  },
  20_000,
);

test.each([
  { focused: false, severity: null, status: 0, diagnostic: false },
  { focused: true, severity: null, status: 1, diagnostic: true },
  { focused: true, severity: "warn", status: 0, diagnostic: true },
  { focused: true, severity: "off", status: 0, diagnostic: false },
])(
  "Biome focused-test policy $focused/$severity",
  ({ focused, severity, status, diagnostic }) => {
    const directory = mkdtempSync(join(tmpdir(), "ci-lint-policy-"));
    try {
      const biome = join(
        dirname(frontendRequire.resolve("@biomejs/biome/package.json")),
        "bin/biome",
      );
      const source = join(directory, "policy.ts");
      writeFileSync(
        source,
        `import { test } from "@playwright/test";\n\ntest${focused ? ".only" : ""}("policy", () => {});\n`,
      );
      let config = frontendRoot;
      if (severity !== null) {
        const settings = JSON.parse(readFileSync(join(frontendRoot, "biome.json"), "utf8"));
        settings.linter.rules.suspicious.noFocusedTests = severity;
        writeFileSync(join(directory, "biome.json"), JSON.stringify(settings));
        config = directory;
      }
      const result = runNodeCli([biome, "lint", `--config-path=${config}`, source], directory);
      expect(result.error).toBeUndefined();
      expect(result.signal).toBeNull();
      expect(result.status).toBe(status);
      expect((result.stdout + result.stderr).includes("lint/suspicious/noFocusedTests")).toBe(
        diagnostic,
      );
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  },
  20_000,
);

test.each([true, false])(
  "native flaky retry is rejected exactly when the policy is enabled: %s",
  (guard) => {
    const directory = mkdtempSync(join(tmpdir(), "ci-browser-flake-"));
    try {
      const corpus = join(directory, "corpus");
      mkdirSync(corpus);
      const config = join(directory, "playwright.config.ts");
      writeFileSync(
        config,
        `import base from ${JSON.stringify(join(frontendRoot, "playwright.config.ts"))};
export default { ...base, globalSetup: undefined, testDir: ${JSON.stringify(corpus)},
  projects: [{ name: "policy" }], outputDir: ${JSON.stringify(join(directory, "results"))},
  reporter: "line", workers: 1, retries: 1, failOnFlakyTests: ${guard} };
`,
      );
      writeFileSync(
        join(corpus, "flaky.spec.ts"),
        `import { expect, test } from ${JSON.stringify(frontendRequire.resolve("@playwright/test"))};
test("transient", ({}, info) => { expect(info.retry).toBe(1); });
`,
      );
      const result = runNodeCli(
        [frontendRequire.resolve("@playwright/test/cli"), "test", "--config", config],
        directory,
      );
      expect(result.error).toBeUndefined();
      expect(result.signal).toBeNull();
      expect(result.status).toBe(guard ? 1 : 0);
      expect(result.stdout + result.stderr).toContain("1 flaky");
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  },
  20_000,
);

function runNodeCli(args: string[], directory: string) {
  return spawnSync(process.execPath, args, {
    cwd: frontendRoot,
    encoding: "utf8",
    env: {
      CI: "true",
      HOME: directory,
      LANG: "C.UTF-8",
      NO_COLOR: "1",
      PATH: process.env["PATH"] ?? "",
      SystemRoot: process.env["SystemRoot"],
      TEMP: directory,
      TMP: directory,
      TMPDIR: directory,
      TZ: "UTC",
    },
    maxBuffer: 65_536,
    timeout: 15_000,
  });
}
