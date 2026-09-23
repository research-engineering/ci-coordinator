import { type ExecFileSyncOptionsWithStringEncoding, execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test } from "vitest";

type PackageManifest = Readonly<{
  bin?: Readonly<Record<string, string>>;
  devDependencies?: Readonly<Record<string, string>>;
  name: string;
  version: string;
}>;
type LegacyMinimatch = {
  (candidate: string, pattern: string): boolean;
  braceExpand(pattern: string): string[];
};

const frontendRoot = process.cwd();
const frontendRequire = createRequire(import.meta.url);
const processOptions: ExecFileSyncOptionsWithStringEncoding = {
  cwd: frontendRoot,
  encoding: "utf8",
};

test("binds compiler and compatibility commands to their exact distributions", () => {
  const workspace = readManifest("package.json");
  const native = readManifest("node_modules/@typescript/native/package.json");
  const compatibility = readManifest("node_modules/typescript/package.json");

  expect(workspace.devDependencies).toMatchObject({
    "@typescript/native": "npm:typescript@7.0.2",
    typescript: "npm:@typescript/typescript6@6.0.2",
  });
  expect(native).toMatchObject({
    bin: { tsc: "./bin/tsc" },
    name: "typescript",
    version: "7.0.2",
  });
  expect(compatibility).toMatchObject({
    bin: { tsc6: "./bin/tsc6" },
    name: "@typescript/typescript6",
    version: "6.0.2",
  });
  expect(commandVersion("tsc")).toBe("Version 7.0.2");
  expect(commandVersion("tsc6")).toBe("Version 6.0.3");
});

test("binds the OpenAPI generator to bounded brace expansion", () => {
  const openapiRequire = createRequire(frontendRequire.resolve("openapi-typescript/package.json"));
  const redoclyRequire = createRequire(
    openapiRequire.resolve("@redocly/openapi-core/package.json"),
  );
  const minimatchPath = redoclyRequire.resolve("minimatch");
  const minimatchRequire = createRequire(minimatchPath);
  const minimatch = redoclyRequire("minimatch") as LegacyMinimatch;
  const braceExpansion = readManifest(minimatchRequire.resolve("brace-expansion/package.json"));

  expect(braceExpansion.version).toBe("5.0.12");
  expect(minimatch.braceExpand("service/{api,worker}.yaml")).toEqual([
    "service/api.yaml",
    "service/worker.yaml",
  ]);
  expect(minimatch("service/api.yaml", "service/{api,worker}.yaml")).toBe(true);
});

function readManifest(relativePath: string): PackageManifest {
  return JSON.parse(readFileSync(resolve(frontendRoot, relativePath), "utf8")) as PackageManifest;
}

function commandVersion(command: "tsc" | "tsc6"): string {
  return execFileSync("pnpm", ["exec", command, "--version"], processOptions).trim();
}
