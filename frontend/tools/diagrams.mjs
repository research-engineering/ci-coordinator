import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { fileURLToPath, pathToFileURL } from "node:url";
import { isMainThread, parentPort, Worker, workerData } from "node:worker_threads";
import { parseAllDocuments } from "yaml";
import { z } from "zod";
import {
  CLEANUP_TIMEOUT_MS,
  DiagramLifecycleError,
  installCancellationHandlers,
  prepareArtifacts,
  remainingMilliseconds,
  renderDiagram,
  withDeadline,
  writeArtifact,
} from "./diagrams-render.mjs";
import { projectSemanticBody } from "./diagrams-semantic.mjs";

const PROFILE_URL = new URL(
  "../../docs/specs/ci-coordinator-proofkit-adoption/documentation-diagrams-profile.v1.json",
  import.meta.url,
);
const MAX_INPUT_BYTES = 64 * 1024 * 1024;
const digestSchema = z.string().regex(/^[a-f0-9]{64}$/);
const versionSchema = z.string().regex(/^\d+\.\d+\.\d+$/);
const sourceText = z.string().refine((value) => value.isWellFormed(), "invalid Unicode source");
const relativePath = sourceText
  .max(2048)
  .refine(
    (value) =>
      value.length > 0 &&
      !value.startsWith("/") &&
      !value.includes("\\") &&
      Array.from(value).every(
        (character) => character.codePointAt(0) >= 32 && character !== "\x7f",
      ) &&
      value.split("/").every((part) => part !== "" && part !== "." && part !== ".."),
    "unsafe repository-relative path",
  );
const profileSchema = z.strictObject({
  schemaVersion: z.literal(1),
  rendererVersion: versionSchema,
  lintVersion: versionSchema,
  nodeVersion: versionSchema,
  playwrightVersion: versionSchema,
  supportedTypes: z.array(z.string()).min(1).max(5),
  maxDiagrams: z.number().int().positive().max(512),
  maxDiagramUtf16Units: z.number().int().positive().max(50_000),
  diagramTimeoutMs: z.number().int().positive().max(15_000),
  runTimeoutSeconds: z.number().int().positive().max(600),
  maxOutputBytes: z
    .number()
    .int()
    .positive()
    .max(16 * 1024 * 1024),
  rules: z.record(z.string(), z.enum(["error", "warn", "off"])),
  rendererConfig: z.strictObject({
    startOnLoad: z.literal(false),
    securityLevel: z.literal("antiscript"),
    secure: z.array(z.string()).min(5),
    maxTextSize: z.number().int().positive().max(50_000),
    maxEdges: z.number().int().positive().max(500),
    flowchart: z.strictObject({ diagramPadding: z.number().int().nonnegative() }),
    sequence: z.strictObject({ diagramMarginY: z.number().int().nonnegative() }),
    gantt: z.strictObject({ useWidth: z.number().int().positive() }),
    pie: z.strictObject({ useWidth: z.number().int().positive() }),
    theme: z.literal("default"),
    layout: z.literal("dagre"),
    look: z.literal("classic"),
  }),
});
const diagramSchema = z.strictObject({
  id: digestSchema,
  path: relativePath,
  line: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
  endLine: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
  body: sourceText,
  semanticBody: sourceText,
  type: z.string().max(100),
  bodySha256: digestSchema,
});
const manifestSchema = z.strictObject({
  schemaVersion: z.literal(1),
  revision: z
    .string()
    .regex(/^[a-f0-9]{40,64}$/)
    .nullable(),
  digest: digestSchema,
  files: z.record(relativePath, digestSchema),
  diagrams: z.array(diagramSchema).min(1).max(512),
  profile: profileSchema,
});

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value).sort((left, right) =>
      Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8")),
    );
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function sha256(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

export async function readProfile() {
  return profileSchema.parse(JSON.parse(await readFile(PROFILE_URL, "utf8")));
}

function validateShadow(diagram) {
  if (diagram.semanticBody === diagram.body) return;
  const original = diagram.body.split("\n");
  const shadow = diagram.semanticBody.split("\n");
  const closing = original.findIndex(
    (line, index) => index > 0 && line.replace(/\r$/, "") === "---",
  );
  if (
    original[0].replace(/\r$/, "") !== "---" ||
    closing < 1 ||
    original.length !== shadow.length ||
    shadow.some((line, index) => (index <= closing ? line !== "" : line !== original[index]))
  ) {
    throw new Error(`${diagram.path}:${diagram.line}: semantic shadow changed diagram source`);
  }
}

export function admitManifest(input, expectedProfile) {
  const manifest = manifestSchema.parse(input);
  if (canonicalJson(manifest.profile) !== canonicalJson(expectedProfile)) {
    throw new Error("manifest profile does not match the repository-owned profile");
  }
  const { digest, ...payload } = manifest;
  if (sha256(canonicalJson(payload)) !== digest) throw new Error("inventory digest mismatch");
  if (manifest.diagrams.length > manifest.profile.maxDiagrams)
    throw new Error("diagram count limit exceeded");
  if (manifest.profile.rendererConfig.maxTextSize !== manifest.profile.maxDiagramUtf16Units) {
    throw new Error("renderer and input text bounds disagree");
  }
  for (const key of ["secure", "securityLevel", "startOnLoad", "maxTextSize", "maxEdges"]) {
    if (!manifest.profile.rendererConfig.secure.includes(key))
      throw new Error(`unprotected renderer setting: ${key}`);
  }
  const ids = new Set();
  for (const diagram of manifest.diagrams) {
    const identity = {
      path: diagram.path,
      line: diagram.line,
      endLine: diagram.endLine,
      bodySha256: diagram.bodySha256,
    };
    if (!Object.hasOwn(manifest.files, diagram.path))
      throw new Error("diagram source absent from file inventory");
    if (diagram.endLine <= diagram.line) throw new Error("invalid diagram source span");
    if (sha256(diagram.body) !== diagram.bodySha256)
      throw new Error("diagram body digest mismatch");
    if (sha256(canonicalJson(identity)) !== diagram.id)
      throw new Error("diagram identity mismatch");
    if (ids.has(diagram.id)) throw new Error("duplicate diagram identity");
    ids.add(diagram.id);
    validateShadow(diagram);
  }
  return manifest;
}

async function installedPackage(name) {
  let directory = path.dirname(fileURLToPath(import.meta.resolve(name)));
  for (let depth = 0; depth < 8; depth++) {
    try {
      const metadata = JSON.parse(await readFile(path.join(directory, "package.json"), "utf8"));
      if (metadata.name === name) return { directory, version: metadata.version };
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    directory = path.dirname(directory);
  }
  throw new Error(`cannot identify installed package ${name}`);
}

const lockMapping = z.record(z.string(), z.unknown());
const bootstrapDependency = z.strictObject({
  specifier: z.string().min(1),
  version: z.string().min(1),
});
const bootstrapLockSchema = z.strictObject({
  lockfileVersion: z.literal("9.0"),
  importers: z.strictObject({
    ".": z.strictObject({
      configDependencies: z.record(z.string(), bootstrapDependency),
      packageManagerDependencies: z
        .record(z.string(), bootstrapDependency)
        .refine((value) => Object.hasOwn(value, "pnpm")),
    }),
  }),
  packages: lockMapping,
  snapshots: lockMapping,
});
const projectLockSchema = z.looseObject({
  lockfileVersion: z.literal("9.0"),
  importers: z
    .record(z.string(), lockMapping)
    .refine(
      (value) =>
        Object.hasOwn(value, "frontend") &&
        Object.values(value).every(
          (importer) =>
            !Object.hasOwn(importer, "packageManagerDependencies") &&
            !Object.hasOwn(importer, "configDependencies"),
        ),
      "ambiguous project lockfile document",
    ),
  packages: lockMapping,
  snapshots: lockMapping,
});

function projectLockBytes(source, wanted) {
  if (!Buffer.isBuffer(source) || source.length === 0 || source.length > 16 * 1024 * 1024) {
    throw new Error("pnpm lockfile source is empty or exceeds its byte bound");
  }
  const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(source);
  const documents = parseAllDocuments(text, { version: "1.2", strict: true, uniqueKeys: true });
  if (documents.length !== (wanted ? 2 : 1)) {
    throw new Error("unexpected pnpm lockfile document count");
  }
  for (const document of documents) {
    if (
      document.errors.length ||
      document.warnings.length ||
      document.directives.yaml.explicit ||
      document.directives.docEnd ||
      Boolean(document.directives.docStart) !== wanted ||
      (wanted && text.slice(document.range[0], document.range[0] + 4) !== "---\n")
    ) {
      throw new Error("pnpm lockfile has ambiguous YAML or unsupported framing");
    }
  }
  if (documents[0].range[0] !== 0) {
    throw new Error("pnpm lockfile has unsupported leading content");
  }
  if (wanted) bootstrapLockSchema.parse(documents[0].toJS({ maxAliasCount: 0 }));
  const project = documents.at(-1);
  projectLockSchema.parse(project.toJS({ maxAliasCount: 0 }));
  // pnpm 12 writes the current lockfile from the project document alone. The
  // parser selects that document; only its explicit framing marker is removed.
  // All dependency, settings, resolution and snapshot bytes remain compared.
  const offset = project.range[0] + (wanted ? 4 : 0);
  return source.subarray(Buffer.byteLength(text.slice(0, offset), "utf8"));
}

export function admitInstalledLockfile(expected, installed) {
  try {
    if (!projectLockBytes(expected, true).equals(projectLockBytes(installed, false))) {
      throw new Error("installed project dependency bytes differ");
    }
  } catch (cause) {
    throw new Error(
      "installed pnpm lockfile differs from pnpm-lock.yaml; run pnpm install --frozen-lockfile from the repository root",
      { cause },
    );
  }
}

async function admitRuntime(profile) {
  if (process.versions.node !== profile.nodeVersion) {
    throw new Error(`Node ${profile.nodeVersion} is required; found ${process.versions.node}`);
  }
  const [expectedLock, installedLock] = await Promise.all([
    readFile(new URL("../../pnpm-lock.yaml", import.meta.url)),
    readFile(new URL("../../node_modules/.pnpm/lock.yaml", import.meta.url)),
  ]).catch((error) => {
    throw new Error(
      "pnpm lockfile admission is unavailable; run pnpm install --frozen-lockfile from the repository root",
      { cause: error },
    );
  });
  admitInstalledLockfile(expectedLock, installedLock);
  const [renderer, lint, playwright] = await Promise.all([
    installedPackage("mermaid"),
    installedPackage("@mermaid-lint/core"),
    installedPackage("@playwright/test"),
  ]);
  for (const [name, actual, expected] of [
    ["mermaid", renderer.version, profile.rendererVersion],
    ["@mermaid-lint/core", lint.version, profile.lintVersion],
    ["@playwright/test", playwright.version, profile.playwrightVersion],
  ]) {
    if (actual !== expected) throw new Error(`${name} ${expected} is required; found ${actual}`);
  }
  return path.join(renderer.directory, "dist", "mermaid.min.js");
}

function conciseError(error) {
  return String(error instanceof Error ? error.message : error)
    .replace(/\s+/g, " ")
    .slice(0, 2000);
}

async function inspectDiagram(diagram, profile, core) {
  const errors = [];
  const warnings = [];
  if (!diagram.body.trim()) errors.push("empty diagram body");
  if (diagram.body.length > profile.maxDiagramUtf16Units)
    errors.push("diagram exceeds its UTF-16 text limit");
  if (!profile.supportedTypes.includes(diagram.type))
    errors.push(`unsupported diagram type: ${diagram.type}`);
  if (/%%\{/.test(diagram.semanticBody))
    errors.push("Mermaid initialization directives are not admitted");
  const index = core.buildSuppressionIndex(diagram.semanticBody.split("\n"));
  if (index.directives.length || /^[^\S\n]*%%[^\S\n]*mermaid-lint/m.test(diagram.semanticBody))
    errors.push("Mermaid lint suppressions are not admitted");
  if (errors.length) return { errors, warnings, admitted: false };
  const semanticBody = await projectSemanticBody(diagram.semanticBody, diagram.type);
  const block = {
    path: diagram.path,
    line: diagram.line,
    col: 1,
    type: diagram.type,
    body: semanticBody,
  };
  const rules = core.resolveRules({ semantic: false });
  for (const [rule, severity] of Object.entries(profile.rules)) {
    if (!core.isRuleId(rule)) throw new Error(`profile names unknown semantic rule: ${rule}`);
    rules[rule] = severity;
  }
  for (const finding of core.checkSemantics(block, rules, index)) {
    const line =
      finding.line === undefined ? diagram.line : core.bodyLineToFileLine(block, finding.line);
    const message = `${diagram.path}:${line}: ${finding.rule}: ${finding.message}`;
    (finding.severity === "error" ? errors : warnings).push(message);
  }
  return { errors, warnings, admitted: true };
}

export async function runWorker(worker, message, milliseconds) {
  let result;
  let failure;
  try {
    result = await withDeadline(
      new Promise((resolve, reject) => {
        worker.once("message", resolve);
        worker.once("error", reject);
        worker.once("exit", (code) =>
          reject(new Error(`semantic worker exited without a result: ${code}`)),
        );
        worker.postMessage(message);
      }),
      milliseconds,
      "semantic pass",
    );
  } catch (error) {
    failure = error;
  }
  try {
    await withDeadline(worker.terminate(), CLEANUP_TIMEOUT_MS, "semantic worker termination");
  } catch (error) {
    throw new DiagramLifecycleError(error.message, {
      cause: failure ? new AggregateError([failure, error]) : error,
    });
  }
  if (failure) throw failure;
  return result;
}

export async function checkManifest(input, { artifactsDirectory } = {}) {
  const manifest = admitManifest(input, await readProfile());
  const { profile } = manifest;
  const result = {
    schemaVersion: 1,
    inventoryDigest: manifest.digest,
    rendererVersion: profile.rendererVersion,
    lintVersion: profile.lintVersion,
    results: [],
  };
  let browser;
  let fatalError;
  let lifecycleFailure;
  const deadline = performance.now() + profile.runTimeoutSeconds * 1000;
  try {
    const bundlePath = await admitRuntime(profile);
    const { chromium } = await import("@playwright/test");
    const artifacts = await prepareArtifacts(artifactsDirectory);
    browser = await chromium.launch({ timeout: profile.diagramTimeoutMs });
    for (const diagram of manifest.diagrams) {
      const entry = { id: diagram.id, errors: [], warnings: [] };
      result.results.push(entry);
      try {
        const diagramDeadline = Math.min(deadline, performance.now() + profile.diagramTimeoutMs);
        const remaining = () => remainingMilliseconds(diagramDeadline);
        const budget = remaining();
        const worker = new Worker(new URL(import.meta.url), { workerData: "diagram-semantics" });
        const response = await runWorker(worker, { diagram, profile }, budget);
        if (!response.ok) throw new Error(response.error);
        const inspection = response.value;
        entry.errors.push(...inspection.errors);
        entry.warnings.push(...inspection.warnings);
        if (inspection.admitted) {
          const budget = remaining();
          const svg = await renderDiagram(browser, bundlePath, diagram, {
            ...profile,
            diagramTimeoutMs: budget,
          });
          await writeArtifact(artifacts, diagram.id, svg, profile.maxOutputBytes);
        }
      } catch (error) {
        if (error instanceof DiagramLifecycleError) {
          throw new DiagramLifecycleError(
            `${diagram.path}:${diagram.line}: ${conciseError(error)}`,
            {
              cause: error,
            },
          );
        }
        entry.errors.push(conciseError(error));
      }
      if (Buffer.byteLength(JSON.stringify(result), "utf8") > profile.maxOutputBytes) {
        throw new Error("diagram report exceeds its output byte budget");
      }
    }
  } catch (error) {
    if (error instanceof DiagramLifecycleError) lifecycleFailure = error;
    else fatalError = conciseError(error);
  } finally {
    try {
      if (browser) await withDeadline(browser.close(), CLEANUP_TIMEOUT_MS, "browser cleanup");
    } catch (error) {
      lifecycleFailure = new DiagramLifecycleError(
        `browser cleanup failed: ${conciseError(error)}`,
        {
          cause: lifecycleFailure ? new AggregateError([lifecycleFailure, error]) : error,
        },
      );
    }
  }
  if (lifecycleFailure) throw lifecycleFailure;
  if (fatalError) {
    const entries = new Map(result.results.map((entry) => [entry.id, entry]));
    result.results = manifest.diagrams.map(({ id }) => {
      const entry = entries.get(id) ?? { id, errors: [], warnings: [] };
      entry.errors.push(fatalError);
      return entry;
    });
  }
  return result;
}

async function main() {
  const args = process.argv.slice(2);
  if (!(args.length === 0 || (args.length === 2 && args[0] === "--artifacts"))) {
    throw new Error(
      "usage: node frontend/tools/diagrams.mjs [--artifacts DIRECTORY] < manifest.json",
    );
  }
  const chunks = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    bytes += chunk.length;
    if (bytes > MAX_INPUT_BYTES)
      throw new Error("diagram manifest exceeds the 64 MiB transport limit");
    chunks.push(chunk);
  }
  const text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks));
  const input = JSON.parse(text);
  const result = await checkManifest(input, { artifactsDirectory: args[1] });
  const output = `${JSON.stringify(result)}\n`;
  if (Buffer.byteLength(output, "utf8") > input.profile.maxOutputBytes)
    throw new Error("diagram report exceeds its output byte budget");
  process.stdout.write(output);
  const diagrams = new Map(input.diagrams.map((diagram) => [diagram.id, diagram]));
  for (const entry of result.results) {
    const diagram = diagrams.get(entry.id);
    for (const [level, messages] of [
      ["error", entry.errors],
      ["warning", entry.warnings],
    ]) {
      for (const message of messages) {
        const diagnostic = message.startsWith(`${diagram.path}:`)
          ? message
          : `${diagram.path}:${diagram.line}: ${message}`;
        process.stderr.write(`${diagnostic} (${level})\n`);
      }
    }
  }
  if (result.results.some((entry) => entry.errors.length)) process.exitCode = 1;
}

if (!isMainThread && workerData === "diagram-semantics") {
  const core = await import("@mermaid-lint/core");
  parentPort.once("message", async ({ diagram, profile }) => {
    try {
      parentPort.postMessage({ ok: true, value: await inspectDiagram(diagram, profile, core) });
    } catch (error) {
      parentPort.postMessage({ ok: false, error: conciseError(error) });
    }
  });
} else if (
  isMainThread &&
  process.argv[1] &&
  import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href
) {
  const disposeSignals = installCancellationHandlers();
  await main().catch((error) => {
    process.stderr.write(
      `diagram checker: ${conciseError(error)}\nPrepare locked dependencies and Chromium before running this check.\n`,
    );
    process.exit(1);
  });
  disposeSignals();
}
