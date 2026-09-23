import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { Worker } from "node:worker_threads";
import { chromium } from "@playwright/test";
import {
  admitInstalledLockfile,
  admitManifest,
  canonicalJson,
  checkManifest,
  readProfile,
  runWorker,
  sha256,
} from "./diagrams.mjs";
import {
  CLEANUP_TIMEOUT_MS,
  DeadlineExceeded,
  DiagramLifecycleError,
  prepareArtifacts,
  remainingMilliseconds,
  renderDiagram,
  withDeadline,
  writeArtifact,
} from "./diagrams-render.mjs";
import { qualifySemanticBoundaries } from "./diagrams-semantic-checks.mjs";

const profile = await readProfile();
const started = performance.now();
assert.equal(
  process.versions.node,
  profile.nodeVersion,
  "qualification requires the admitted Node",
);

function makeDiagram(name, body, type = "flowchart", semanticBody = body) {
  const value = {
    path: `docs/${name}.md`,
    line: 2,
    endLine: body.split("\n").length + 3,
    body,
    semanticBody,
    type,
    bodySha256: sha256(body),
  };
  return {
    id: sha256(
      canonicalJson({
        path: value.path,
        line: value.line,
        endLine: value.endLine,
        bodySha256: value.bodySha256,
      }),
    ),
    ...value,
  };
}

function makeManifest(diagrams) {
  const payload = {
    schemaVersion: 1,
    revision: null,
    files: Object.fromEntries(
      diagrams.map((diagram) => [diagram.path, sha256(`document:${diagram.body}`)]),
    ),
    diagrams,
    profile,
  };
  return { ...payload, digest: sha256(canonicalJson(payload)) };
}

function resign(manifest) {
  const { digest: _digest, ...payload } = manifest;
  return { ...payload, digest: sha256(canonicalJson(payload)) };
}

const metadataBody =
  "---\ntitle: A[Title]\naccTitle: Example\n---\nflowchart TD\nA[Actual] --> B[Done]\n";
const sequenceSemicolons =
  "sequenceDiagram\nparticipant API\nparticipant B\nalt forbidden\nAPI-->>B: forbidden; no baseline store or provider read\nelse review\nAPI-->>B: Retained review; activation is separate\nend";
const escapedSemicolons =
  "sequenceDiagram\nparticipant API\nparticipant B\nalt forbidden\nAPI-->>B: forbidden#59; no baseline store or provider read\nelse review\nAPI-->>B: Retained review#59; activation is separate\nend";
const cases = [
  ["flowchart", "flowchart TD\nA[Ready] --> B[Done]", "flowchart", null],
  ["graph-alias", "graph LR\nA[Ready] --> B[Done]", "graph", null],
  [
    "sequence",
    "sequenceDiagram\nparticipant A as Alice\nparticipant B as Bob\nA->>B: Hello\nB-->>A: Done",
    "sequenceDiagram",
    null,
  ],
  [
    "state-v2",
    "stateDiagram-v2\n[*] --> Ready\nReady --> Ready: retry\nReady --> [*]",
    "stateDiagram-v2",
    null,
  ],
  [
    "state-alias",
    "stateDiagram\n[*] --> Ready\nReady --> Ready: retry\nReady --> [*]",
    "stateDiagram",
    null,
  ],
  ["quoted-label", 'flowchart TD\nA["literal B[inner]"] --> B[Actual]', "flowchart", null],
  ["repeated-declaration", "flowchart TD\nA[Ready]\nA[Ready]\nA --> B[Done]", "flowchart", null],
  ["metadata-shadow", metadataBody, "flowchart", null],
  [
    "duplicate-id",
    "flowchart TD\nA[First]\nA[Second]\nA --> B[Done]",
    "flowchart",
    "duplicate-ids",
  ],
  ["missing-direction", "flowchart\nA --> B", "flowchart", "require-direction"],
  [
    "duplicate-participant",
    "sequenceDiagram\nparticipant A as One\nparticipant A as Two\nA->>B: hello",
    "sequenceDiagram",
    "sequence-duplicate-participant",
  ],
  ["invalid-flowchart", "flowchart TD\nA[unclosed", "flowchart", "Parse error"],
  ["invalid-sequence", "sequenceDiagram\nA->>B missing colon", "sequenceDiagram", "Parse error"],
  ["invalid-state", "stateDiagram-v2\nA -->", "stateDiagram-v2", "Parse error"],
  [
    "init-config",
    '%%{init: {"securityLevel":"loose"}}%%\nflowchart TD\nA --> B',
    "flowchart",
    "initialization directives",
  ],
  [
    "suppression",
    "flowchart TD\n%% mermaid-lint-disable-diagram duplicate-ids: witness\nA[One]\nA[Two]",
    "flowchart",
    "suppressions",
  ],
  [
    "malformed-suppression",
    "flowchart TD\n%% mermaid-lint-disable\nA --> B",
    "flowchart",
    "suppressions",
  ],
  ["empty", "", "flowchart", "empty diagram"],
  ["oversize-utf16", `flowchart TD\nA["${"\u{1f600}".repeat(25_000)}"]`, "flowchart", "UTF-16"],
  ["unsupported", 'pie\n"one": 1', "pie", "unsupported diagram type"],
  [
    "external-image",
    'flowchart TD\nA@{ img: "https://diagram-assets.invalid/witness.svg", label: "Remote" }\nA --> B[Local]',
    "flowchart",
    "external requests",
  ],
  ["sequence-header-semicolon", "sequenceDiagram;\nA->>B: Hello\n", "sequenceDiagram", null],
  ...["error-text", "error-icon"].map((className) => [
    `user-class-${className}`,
    `flowchart TD\nA[Ready] --> B[Done]\nclassDef ${className} fill:#fff,stroke:#333;\nclass A ${className};`,
    "flowchart",
    null,
  ]),
  ...["exceeded", "#101;xceeded"].map((ending) => [
    `user-fallback-label-${ending}`,
    `flowchart TD\nA["Maximum text size in diagram ${ending}"]`,
    "flowchart",
    null,
  ]),
];
const diagrams = cases.map(([name, body, type]) =>
  makeDiagram(
    name,
    body,
    type,
    name === "metadata-shadow" ? "\n\n\n\nflowchart TD\nA[Actual] --> B[Done]\n" : body,
  ),
);
cases.push([
  "literal-semicolons",
  sequenceSemicolons,
  "sequenceDiagram",
  /Parse error|Lexical error/,
]);
cases.push(["escaped-semicolons", escapedSemicolons, "sequenceDiagram", null]);
diagrams.push(makeDiagram("literal-semicolons", sequenceSemicolons, "sequenceDiagram"));
diagrams.push(makeDiagram("escaped-semicolons", escapedSemicolons, "sequenceDiagram"));
const manifest = makeManifest(diagrams);
admitManifest(manifest, profile);

const originalWallClock = Date.now;
try {
  const deadline = performance.now() + 1000;
  for (const offset of [-1e12, 1e12]) {
    Date.now = () => originalWallClock() + offset;
    const remaining = remainingMilliseconds(deadline);
    assert.ok(remaining > 0 && remaining <= 1000, "wall-clock jump changed elapsed budget");
    assert.throws(() => remainingMilliseconds(performance.now() - 1), DeadlineExceeded);
  }
  const elapsedStart = performance.now();
  await assert.rejects(
    withDeadline(new Promise(() => {}), 25, "elapsed witness"),
    DeadlineExceeded,
  );
  const elapsed = performance.now() - elapsedStart;
  assert.ok(elapsed >= 15 && elapsed < 1500, "elapsed timeout escaped its independent bound");
} finally {
  Date.now = originalWallClock;
}

for (const stage of ["context", "render", "cleanup"]) {
  let contextClosed = false;
  let browserClosed = false;
  let pageCreated = false;
  let stopRender;
  const page = {
    setDefaultTimeout() {},
    on() {},
    async setContent() {},
    async addScriptTag() {},
    async evaluate() {
      if (stage === "render") {
        await new Promise((_, reject) => {
          stopRender = reject;
        });
      }
      return "<svg/>";
    },
    async waitForLoadState() {},
  };
  const context = {
    async route() {},
    async newPage() {
      pageCreated = true;
      return page;
    },
    async close() {
      if (stage === "cleanup") await delay(80);
      contextClosed = true;
    },
  };
  const browser = {
    async newContext() {
      if (stage === "context") await delay(80);
      return context;
    },
    async close() {
      browserClosed = true;
      stopRender?.(new Error("owned browser closed"));
    },
  };
  await assert.rejects(
    renderDiagram(browser, "unused", diagrams[0], { ...profile, diagramTimeoutMs: 20 }),
    DiagramLifecycleError,
  );
  assert.ok(browserClosed && contextClosed, `${stage}: cancellation did not close and join`);
  if (stage === "context") assert.equal(pageCreated, false, "cancelled setup advanced");
}

const stuckCleanupStarted = performance.now();
await assert.rejects(
  renderDiagram(
    { newContext: () => new Promise(() => {}), close: () => new Promise(() => {}) },
    "unused",
    diagrams[0],
    { ...profile, diagramTimeoutMs: 20 },
  ),
  /renderer did not drain/,
);
assert.ok(performance.now() - stuckCleanupStarted < CLEANUP_TIMEOUT_MS + 1500);

let failedContextBrowserClosed = false;
await assert.rejects(
  renderDiagram(
    {
      async newContext() {
        return {
          async route() {
            throw new Error("controlled setup failure");
          },
          async close() {
            throw new Error("controlled close failure");
          },
        };
      },
      async close() {
        failedContextBrowserClosed = true;
      },
    },
    "unused",
    diagrams[0],
    profile,
  ),
  DiagramLifecycleError,
);
assert.equal(failedContextBrowserClosed, true, "failed context cleanup did not close its browser");

const unresponsiveWorker = new EventEmitter();
unresponsiveWorker.postMessage = () => queueMicrotask(() => unresponsiveWorker.emit("message", {}));
unresponsiveWorker.terminate = () => new Promise(() => {});
await assert.rejects(runWorker(unresponsiveWorker, null, 100), DiagramLifecycleError);

const nativeLaunch = chromium.launch;
let attemptedContexts = 0;
try {
  chromium.launch = async () => ({
    async newContext() {
      attemptedContexts++;
      throw new DeadlineExceeded("controlled context setup timeout");
    },
    async close() {},
  });
  await assert.rejects(checkManifest(makeManifest(diagrams.slice(0, 2))), DiagramLifecycleError);
  assert.equal(attemptedContexts, 1, "batch advanced after a fatal renderer timeout");
} finally {
  chromium.launch = nativeLaunch;
}

let admissions = 0;
const reject = (value, reason) => {
  assert.throws(() => admitManifest(value, profile), undefined, reason);
  admissions++;
};
reject({ ...manifest, digest: "0".repeat(64) }, "wrong inventory hash");
reject(resign({ ...manifest, diagrams: [diagrams[0], diagrams[0]] }), "duplicate identities");
reject(
  resign({ ...manifest, diagrams: [{ ...diagrams[0], body: "flowchart TD\nX --> Y" }] }),
  "changed body",
);
reject(
  resign({ ...manifest, diagrams: [{ ...diagrams[0], id: "1".repeat(64) }] }),
  "foreign identity",
);
reject(
  resign({ ...manifest, diagrams: [{ ...diagrams[0], semanticBody: "flowchart TD\n" }] }),
  "clipped semantic source",
);
reject(resign({ ...manifest, diagrams: [] }), "empty corpus");
reject(resign({ ...manifest, files: {} }), "missing source file");
reject(
  resign({
    ...manifest,
    profile: { ...profile, rendererConfig: { ...profile.rendererConfig, maxTextSize: 10 } },
  }),
  "changed profile",
);
reject({ ...manifest, ignored: true }, "unknown manifest field");
reject(resign({ ...manifest, profile: { ...profile, ignored: true } }), "unknown profile field");
const bootstrapLock = [
  "---",
  "lockfileVersion: '9.0'",
  "importers:",
  "  .:",
  "    configDependencies: {}",
  "    packageManagerDependencies:",
  "      pnpm: {specifier: 12.5.1, version: 12.5.1}",
  "packages: {}",
  "snapshots: {}",
  "",
].join("\n");
const projectLock = [
  "lockfileVersion: '9.0'",
  "settings: {autoInstallPeers: true, excludeLinksFromLockfile: false}",
  "importers:",
  "  .: {}",
  "  frontend:",
  "    dependencies:",
  "      example: {specifier: 1.0.0, version: 1.0.0}",
  "packages:",
  "  example@1.0.0: {resolution: {integrity: sha512-example}}",
  "snapshots:",
  "  example@1.0.0: {}",
  "",
].join("\n");
const wantedLock = Buffer.from(`${bootstrapLock}---\n${projectLock}`);
const installedLock = Buffer.from(projectLock);
admitInstalledLockfile(wantedLock, installedLock);
for (const [wanted, installed] of [
  [installedLock, installedLock],
  [Buffer.from(bootstrapLock), installedLock],
  [Buffer.concat([wantedLock, Buffer.from("---\n{}\n")]), installedLock],
  [wantedLock, wantedLock],
  [Buffer.from(`---\n${projectLock}${bootstrapLock}`), installedLock],
  [
    Buffer.from(`${bootstrapLock}---\n${projectLock.replace("frontend:", "other:")}`),
    installedLock,
  ],
  [wantedLock, Buffer.from(projectLock.replace("sha512-example", "sha512-changed"))],
  [
    wantedLock,
    Buffer.from(projectLock.replace("autoInstallPeers: true", "autoInstallPeers: false")),
  ],
  [wantedLock, Buffer.concat([installedLock, Buffer.from("\n")])],
  [wantedLock, Buffer.from(`---\n${projectLock}`)],
  [wantedLock, Buffer.from(`${projectLock}lockfileVersion: '9.0'\n`)],
  [wantedLock, Buffer.from(`${projectLock}aliases: [&shared {}, *shared]\n`)],
  [Buffer.concat([Buffer.from([0xff]), wantedLock]), installedLock],
  [wantedLock, Buffer.alloc(16 * 1024 * 1024 + 1, "x")],
]) {
  assert.throws(() => admitInstalledLockfile(wanted, installed), /installed pnpm lockfile differs/);
}

const report = await checkManifest(manifest);
assert.equal(report.inventoryDigest, manifest.digest);
assert.equal(report.results.length, cases.length);
assert.deepEqual(
  new Set(report.results.map(({ id }) => id)),
  new Set(diagrams.map(({ id }) => id)),
);
for (let index = 0; index < cases.length; index++) {
  const [name, , , expectedError] = cases[index];
  const result = report.results[index];
  assert.equal(result.id, diagrams[index].id, `${name}: identity changed`);
  if (expectedError === null) {
    assert.deepEqual(result.errors, [], `${name}: unexpected failure`);
  } else {
    assert.ok(
      result.errors.some((message) =>
        expectedError instanceof RegExp
          ? expectedError.test(message)
          : message.includes(expectedError),
      ),
      `${name}: missing ${expectedError}: ${result.errors.join("; ")}`,
    );
  }
}
assert.ok(
  report.results[6].warnings.some((message) => message.includes("no-duplicate-node-declarations")),
);

const semanticCases = await qualifySemanticBoundaries({ profile, makeDiagram, makeManifest });

const temporary = await mkdtemp(path.join(os.tmpdir(), "diagram-qualification-"));
let browser;
try {
  const artifacts = await prepareArtifacts(path.join(temporary, "artifacts"));
  await writeArtifact(artifacts, diagrams[0].id, "<svg/>", profile.maxOutputBytes);
  await assert.rejects(writeArtifact(artifacts, diagrams[0].id, "<svg/>", profile.maxOutputBytes), {
    code: "EEXIST",
  });
  await symlink(artifacts.directory, path.join(temporary, "linked-artifacts"));
  await assert.rejects(prepareArtifacts(path.join(temporary, "linked-artifacts")), /not a symlink/);
  browser = await chromium.launch({ timeout: profile.diagramTimeoutMs });
  const bundle = path.join(
    path.dirname(fileURLToPath(import.meta.resolve("mermaid"))),
    "mermaid.min.js",
  );
  const boundaryDiagram = makeDiagram("renderer-limit", "flowchart TD\nA[BoundaryWitness]");
  const atLimit = {
    ...profile,
    rendererConfig: { ...profile.rendererConfig, maxTextSize: boundaryDiagram.body.length },
  };
  assert.match(await renderDiagram(browser, bundle, boundaryDiagram, atLimit), /BoundaryWitness/);
  const belowLimit = {
    ...profile,
    rendererConfig: { ...profile.rendererConfig, maxTextSize: boundaryDiagram.body.length - 1 },
  };
  const requireSizeRejection = (render) =>
    assert.rejects(render(browser, bundle, boundaryDiagram, belowLimit), /renderer text limit/);
  await requireSizeRejection(renderDiagram);

  const rendererSource = await readFile(new URL("./diagrams-render.mjs", import.meta.url), "utf8");
  const limitGuard = "if (body.length > maximumTextSize)";
  assert.equal(rendererSource.split(limitGuard).length, 2, "renderer limit guard is not unique");
  const mutantPath = path.join(temporary, "renderer-without-limit-guard.mjs");
  await writeFile(mutantPath, rendererSource.replace(limitGuard, "if (false)"));
  const mutant = await import(pathToFileURL(mutantPath));
  const fallbackSvg = await mutant.renderDiagram(browser, bundle, boundaryDiagram, belowLimit);
  assert.match(fallbackSvg, /Maximum text size in diagram exceeded/);
  assert.doesNotMatch(fallbackSvg, /BoundaryWitness/);
  await assert.rejects(requireSizeRejection(mutant.renderDiagram), /Missing expected rejection/);

  await assert.rejects(
    renderDiagram(
      browser,
      bundle,
      makeDiagram("api-parse-error", "flowchart TD\nA[unclosed"),
      profile,
    ),
    /Parse error/,
  );
  await assert.rejects(
    renderDiagram(browser, bundle, makeDiagram("api-error-result", "error"), profile),
    /renderer returned an error diagram/,
  );

  const fixturePath = path.join(temporary, "renderer-result-fixture.js");
  const validSvg =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>';
  for (const [name, value, expected] of [
    ["valid", { diagramType: "flowchart-v2", svg: validSvg }, null],
    ["error", { diagramType: "error", svg: validSvg }, /renderer returned an error diagram/],
    ["empty", { diagramType: "flowchart-v2", svg: "" }, /renderer returned no SVG/],
    ["html", { diagramType: "flowchart-v2", svg: "<div>not an SVG</div>" }, /no SVG element/],
  ]) {
    await writeFile(
      fixturePath,
      `globalThis.mermaid = {initialize(config) {this.config = config;}, mermaidAPI: {getConfig() {return globalThis.mermaid.config;}}, async render() {return ${JSON.stringify(value)};}};`,
    );
    const operation = renderDiagram(browser, fixturePath, boundaryDiagram, profile);
    if (expected === null)
      assert.equal(await operation, validSvg, `${name}: valid result rejected`);
    else await assert.rejects(operation, expected, `${name}: invalid result accepted`);
  }
  for (const maxTextSize of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, "50000", null]) {
    await assert.rejects(
      renderDiagram(browser, fixturePath, boundaryDiagram, {
        ...profile,
        rendererConfig: { ...profile.rendererConfig, maxTextSize },
      }),
      /renderer text limit must be a positive safe integer/,
    );
  }
  const semicolonSvg = await renderDiagram(browser, bundle, diagrams.at(-1), profile);
  const displayPage = await browser.newPage();
  await displayPage.setContent(semicolonSvg);
  const displayedText = await displayPage.locator("svg").textContent();
  assert.ok(displayedText.includes("forbidden; no baseline store or provider read"));
  assert.ok(displayedText.includes("Retained review; activation is separate"));
  await displayPage.close();
  await assert.rejects(
    renderDiagram(browser, bundle, diagrams[0], { ...profile, diagramTimeoutMs: 1 }),
    /exceeded|Timeout/,
  );
  assert.equal(browser.contexts().length, 0, "failed render leaked its browser context");
  const stalled = new Worker("while (true) {}", { eval: true });
  await assert.rejects(runWorker(stalled, null, 20), /semantic pass exceeded/);
  assert.equal(stalled.threadId, -1, "expired semantic worker was not terminated");
} finally {
  await browser?.close();
  await rm(temporary, { recursive: true, force: true });
}
process.stdout.write(
  `Diagram qualification passed: ${cases.length} renderer/policy cases, ${semanticCases} semantic boundary cases, ${admissions} admission rejections, 2 installed-lock rejections, effective renderer limit and error-result controls, limit-guard mutation, wall-clock and renderer cancellation controls, artifact and worker/browser deadline cleanup boundaries; ${Math.round(performance.now() - started)} ms.\n`,
);
