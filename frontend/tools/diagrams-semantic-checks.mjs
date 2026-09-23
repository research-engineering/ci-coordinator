import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { checkManifest } from "./diagrams.mjs";
import { renderDiagram } from "./diagrams-render.mjs";
import { projectSemanticBody } from "./diagrams-semantic.mjs";

const originalTitle = "Original graph title %% mermaid-lint reference";
const originalAccTitle = "Original accessible title A[Example]";
const originalAccDescription = "Original accessible description B[Example]";
const metadataBody = [
  "---",
  `title: "${originalTitle}"`,
  'accTitle: "Literal %% mermaid-lint-disable"',
  'accDescr: "Literal %%{init: {}}%%"',
  "---",
  "flowchart TD",
  `accTitle: ${originalAccTitle}`,
  `accDescr: ${originalAccDescription}`,
  "A[Actual] --> B[Done]",
].join("\n");
const metadataShadow = `\n\n\n\n\n${metadataBody.split("\n").slice(5).join("\n")}`;
const annotationImage = `data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><title>B[Image]</title><rect width="16" height="16" fill="red"/></svg>',
)
  .replaceAll("%5B", "[")
  .replaceAll("%5D", "]")}`;

async function assertOriginalSvg(browser, svg) {
  const page = await browser.newPage();
  try {
    await page.setContent(svg);
    const observations = await page.locator("svg").evaluate((element) => ({
      title: element.querySelector(".flowchartTitleText")?.textContent,
      accTitle: element.querySelector(":scope > title")?.textContent,
      accDescription: element.querySelector(":scope > desc")?.textContent,
    }));
    assert.deepEqual(
      observations,
      {
        title: originalTitle,
        accTitle: originalAccTitle,
        accDescription: originalAccDescription,
      },
      "original-only SVG title and accessibility text were lost",
    );
  } finally {
    await page.close();
  }
}

export async function qualifySemanticBoundaries({ profile, makeDiagram, makeManifest }) {
  const cases = [
    ["acc-title", "flowchart TD\naccTitle: A[Example]\nA[Actual] --> B"],
    ["acc-description", "flowchart TD\naccDescr: A[Example]\nA[Actual] --> B"],
    ["acc-description-block", "flowchart TD\naccDescr {\nA[Example]\n}\nA[Actual] --> B"],
    ["tight-description", "flowchart TD\naccDescr{A[Example]}\naccDescr[Actual] --> A"],
    ["quoted-multiline", 'flowchart TD\nA["first\nB[Example]"] --> B[Actual]'],
    ["quoted-separator", 'flowchart TD\nA["Before\n---\nAfter"] --> B'],
    ["markdown-multiline", 'flowchart TD\nA["`first\nB[Example]`"] --> B[Actual]'],
    ["same-label-different-shape", "flowchart TD\nA((Same)) --> B\nA[Same]"],
    ["graph-accessibility", "graph TD\naccTitle: A[Example]\nA[Actual] --> B", "graph"],
    ["annotation-label", 'flowchart TD\nA@{ shape: rect, label: "B[Example]" }\nB[Actual]\n'],
    [
      "annotation-multiline-label",
      'flowchart TD\nA@{ shape: rect, label: "first\nB[Example]" }\nB[Actual]\n',
    ],
    [
      "annotation-quoted-braces",
      'flowchart TD\nA@{ shape: rect, label: "} B[Example] {" }\nB[Actual]\n',
    ],
    [
      "annotation-icon",
      'flowchart TD\nA@{ icon: "B[Icon]", label: "Ready", form: square }\nB[Actual]\n',
    ],
    [
      "annotation-image",
      `flowchart TD\nA@{ img: "${annotationImage}", label: "Ready", w: 16, h: 16 }\nB[Actual]\n`,
    ],
    [
      "annotation-multiline-image",
      `flowchart TD\nA@{ img: "${annotationImage}", label: "first\nB[Example]", w: 16, h: 16 }\nB[Actual]\n`,
    ],
    ["annotation-marker-in-label", 'flowchart TD\nA["literal @{ B[Example] }"] --> B[Actual]\n'],
    [
      "invalid-annotation-shape",
      'flowchart TD\nA@{ shape: invalid, label: "B[Example]" }\nB[Actual]\n',
      "flowchart",
      "No such shape: invalid",
    ],
    [
      "genuine-conflict-after-annotation",
      'flowchart TD\nA@{ label: "first\nB[Example]" }\nB[Actual]\nB[Other]\n',
      "flowchart",
      "duplicate-ids",
      5,
    ],
    [
      "genuine-declaration-before-annotation",
      'flowchart TD\nA[Base]@{ label: "B[Example]" }\nA[Other]\n',
      "flowchart",
      "duplicate-ids",
      3,
    ],
    [
      "literal-accessibility-marker",
      "flowchart TD\naccTitle: Documentation %% mermaid-lint reference\nA --> B",
    ],
    [
      "literal-description-marker",
      "flowchart TD\naccDescr: Documentation %% mermaid-lint-disable reference\nA --> B",
    ],
    [
      "literal-description-block-marker",
      "flowchart TD\naccDescr { Documentation %% mermaid-lint reference }\nA --> B",
    ],
    ["literal-label-marker", 'flowchart TD\nA["Documentation %% mermaid-lint reference"] --> B'],
    ["literal-spaced-brace", 'flowchart TD\nA["Documentation %% { reference"] --> B'],
    [
      "inline-accessibility-init",
      "flowchart TD\naccTitle: Before %%{init: {}}%% After\nA --> B",
      "flowchart",
      "initialization directives",
    ],
    [
      "inline-label-init",
      'flowchart TD\nA["Before %%{init: {}}%% After"] --> B',
      "flowchart",
      "initialization directives",
    ],
    [
      "malformed-comment-directive",
      "flowchart TD\n  %% mermaid-lint-disable\nA --> B",
      "flowchart",
      "suppressions",
    ],
    [
      "unknown-comment-directive",
      "flowchart TD\n  %% mermaid-lint-unknown all: hidden\nA --> B",
      "flowchart",
      "suppressions",
    ],
    [
      "comment-directive-within-description",
      "flowchart TD\naccDescr {\n %% mermaid-lint-disable\n}\nA --> B",
      "flowchart",
      "suppressions",
    ],
    [
      "genuine-conflict-after-accessibility",
      "flowchart TD\naccDescr {\nA[Example]\n}\nA[First]\nA[Second]",
      "flowchart",
      "duplicate-ids",
      6,
    ],
    [
      "genuine-multiline-label-conflict",
      'flowchart TD\nA["first\nB[Example]"] --> B[Actual]\nA["second\nB[Example]"]',
      "flowchart",
      "duplicate-ids",
      4,
    ],
    [
      "genuine-following-node-conflict",
      'flowchart TD\nA["first\nB[Example]"] --> B[Actual]\nB[Different]',
      "flowchart",
      "duplicate-ids",
      4,
    ],
    [
      "escape-spelling-cannot-collapse-labels",
      'flowchart TD\nA["first\nsecond"]\nA["first\\u000asecond"]',
      "flowchart",
      "duplicate-ids",
      4,
    ],
    [
      "identical-multiline-labels",
      'flowchart TD\nA["first\nB[Example]"] --> B[Actual]\nA["first\nB[Example]"]',
    ],
    [
      "sequence-description",
      "sequenceDiagram\naccDescr {\nparticipant A as Fake\n}\nparticipant A as Actual\nA->>B: Hello",
      "sequenceDiagram",
    ],
    [
      "genuine-participant-conflict-after-description",
      "sequenceDiagram\naccDescr {\nparticipant A as Fake\n}\nparticipant A as Actual\nparticipant A as Other\nA->>B: Hello",
      "sequenceDiagram",
      "sequence-duplicate-participant",
      6,
    ],
  ];
  for (const type of ["stateDiagram", "stateDiagram-v2"]) {
    cases.push([
      `${type}-description`,
      `${type}\naccDescr {\nstate "Fake" as A\nA --> B\n}\nstate "Actual" as A\nA --> B`,
      type,
    ]);
    cases.push([
      `${type}-real-transition`,
      `${type}\naccDescr {\nA --> B\n}\nA --> B\nA --> B`,
      type,
      null,
      null,
      "state-duplicate-transition",
    ]);
  }
  const diagrams = cases.map(([name, body, type = "flowchart"]) => makeDiagram(name, body, type));
  const boundaryBody =
    'flowchart TD\nA[Base]@{ label: "first\nB[Example]" }\nB[Actual]\nC e1@--> D\n';
  const boundaryProjection = await projectSemanticBody(boundaryBody, "flowchart");
  const annotationStart = boundaryBody.indexOf("@{");
  const annotationEnd = boundaryBody.indexOf("}\n") + 1;
  assert.equal(boundaryProjection.length, boundaryBody.length);
  assert.equal(
    boundaryProjection.slice(0, annotationStart),
    boundaryBody.slice(0, annotationStart),
  );
  assert.equal(boundaryProjection.slice(annotationEnd), boundaryBody.slice(annotationEnd));
  assert.match(boundaryProjection.slice(annotationStart, annotationEnd), /^[ \n]+$/);
  assert.equal(
    await projectSemanticBody("flowchart TD\nA e1@--> B\n", "flowchart"),
    "flowchart TD\nA e1@--> B\n",
  );
  const original = makeDiagram("original-only-metadata", metadataBody, "flowchart", metadataShadow);
  const separatorTitle = makeDiagram(
    "separator-in-title",
    "---\ntitle: |\n  Before\n  ---\n  After\n---\nflowchart TD\nA --> B\n",
    "flowchart",
    "\n\n\n\n\n\nflowchart TD\nA --> B\n",
  );
  diagrams.push(separatorTitle);
  diagrams.push(original);
  const temporary = await mkdtemp(path.join(os.tmpdir(), "diagram-semantic-qualification-"));
  let browser;
  try {
    const artifactsDirectory = path.join(temporary, "artifacts");
    const report = await checkManifest(makeManifest(diagrams), { artifactsDirectory });
    for (let index = 0; index < cases.length; index++) {
      const [name, body, , error, bodyLine, warning] = cases[index];
      const result = report.results[index];
      assert.equal(result.id, diagrams[index].id, `${name}: result identity changed`);
      if (error) {
        const expected = bodyLine
          ? `${diagrams[index].path}:${diagrams[index].line + bodyLine}: ${error}:`
          : error;
        assert.ok(
          result.errors.some((message) => message.includes(expected)),
          `${name}: genuine conflict or source line was lost: ${result.errors.join("; ")}`,
        );
      } else {
        assert.deepEqual(result.errors, [], `${name}: unexpected error`);
      }
      if (warning) {
        assert.ok(
          result.warnings.some((message) => message.includes(warning)),
          name,
        );
      } else if (name.endsWith("-description")) {
        assert.deepEqual(result.warnings, [], `${name}: accessibility text became structure`);
      }
      if (!error || bodyLine) {
        const projected = await projectSemanticBody(body, diagrams[index].type);
        assert.equal(projected.split("\n").length, body.split("\n").length, name);
      }
    }
    assert.deepEqual(report.results.at(-1).errors, [], "literal metadata markers were rejected");
    assert.deepEqual(report.results.at(-2).errors, [], "literal metadata separator was rejected");
    const originalSvg = await readFile(path.join(artifactsDirectory, `${original.id}.svg`), "utf8");
    browser = await chromium.launch({ timeout: profile.diagramTimeoutMs });
    await assertOriginalSvg(browser, originalSvg);
    const separatorPage = await browser.newPage();
    try {
      await separatorPage.setContent(
        await readFile(path.join(artifactsDirectory, `${separatorTitle.id}.svg`), "utf8"),
      );
      assert.equal(
        await separatorPage.locator(".flowchartTitleText").textContent(),
        "Before\n---\nAfter\n",
        "literal separator in the original multiline title was lost",
      );
    } finally {
      await separatorPage.close();
    }
    const annotationLabels = new Map([
      ["annotation-label", ["Actual", "B[Example]"]],
      ["annotation-multiline-label", ["Actual", "firstB[Example]"]],
      ["annotation-icon", ["Actual", "Ready"]],
      ["annotation-image", ["Actual", "Ready"]],
      ["annotation-multiline-image", ["Actual", "firstB[Example]"]],
      ["annotation-marker-in-label", ["Actual", "literal @{ B[Example] }"]],
    ]);
    const annotationPage = await browser.newPage();
    try {
      for (const [name, expectedLabels] of annotationLabels) {
        const diagram = diagrams.find((value) => value.path === `docs/${name}.md`);
        const svg = await readFile(path.join(artifactsDirectory, `${diagram.id}.svg`), "utf8");
        await annotationPage.setContent(svg);
        assert.deepEqual(
          (await annotationPage.locator("svg .nodeLabel").allTextContents()).sort(),
          expectedLabels,
          `${name}: original annotation text was not rendered`,
        );
        if (name.endsWith("image")) assert.ok(svg.includes("data:image/svg+xml"), name);
      }
    } finally {
      await annotationPage.close();
    }
    const bundle = path.join(
      path.dirname(fileURLToPath(import.meta.resolve("mermaid"))),
      "mermaid.min.js",
    );
    const renderingInput = {
      ...original,
      semanticBody: await projectSemanticBody(metadataShadow, "flowchart"),
    };
    await assertOriginalSvg(browser, await renderDiagram(browser, bundle, renderingInput, profile));
    const rendererSource = await readFile(
      new URL("./diagrams-render.mjs", import.meta.url),
      "utf8",
    );
    const operand = "body: diagram.body,";
    assert.equal(rendererSource.split(operand).length, 2, "render mutation operand changed");
    const mutantSource = rendererSource.replace(operand, "body: diagram.semanticBody,");
    const mutant = await import(
      `data:text/javascript;base64,${Buffer.from(mutantSource).toString("base64")}`
    );
    await assert.rejects(
      assertOriginalSvg(
        browser,
        await mutant.renderDiagram(browser, bundle, renderingInput, profile),
      ),
      /original-only SVG title and accessibility text were lost/,
      "original-source oracle did not detect semantic-shadow substitution",
    );
    return cases.length + 2;
  } finally {
    await browser?.close();
    await rm(temporary, { recursive: true, force: true });
  }
}
