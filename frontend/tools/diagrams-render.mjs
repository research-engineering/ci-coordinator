import { constants } from "node:fs";
import { lstat, mkdir, open, realpath } from "node:fs/promises";
import path from "node:path";
import { performance } from "node:perf_hooks";

export const CLEANUP_TIMEOUT_MS = 2000;

export class DeadlineExceeded extends Error {}
export class DiagramLifecycleError extends Error {}

export function remainingMilliseconds(deadline) {
  const remaining = deadline - performance.now();
  if (remaining <= 0) throw new DeadlineExceeded("diagram deadline exceeded");
  return remaining;
}

export function installCancellationHandlers() {
  // Playwright owns detached Chromium through process-exit cleanup. Normal exit
  // runs that cleanup before the Python supervisor escalates to SIGKILL.
  const interrupt = () => process.exit(130);
  const terminate = () => process.exit(143);
  process.on("SIGINT", interrupt);
  process.on("SIGTERM", terminate);
  return () => {
    process.off("SIGINT", interrupt);
    process.off("SIGTERM", terminate);
  };
}

export async function withDeadline(operation, milliseconds, label) {
  let timer;
  try {
    return await Promise.race([
      operation,
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new DeadlineExceeded(`${label} exceeded ${milliseconds} ms`)),
          milliseconds,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export async function prepareArtifacts(directory) {
  if (directory === undefined) return undefined;
  const absolute = path.resolve(directory);
  await mkdir(absolute, { mode: 0o700 }).catch((error) => {
    if (error.code !== "EEXIST") throw error;
  });
  const metadata = await lstat(absolute);
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) {
    throw new Error("artifact destination must be a directory, not a symlink");
  }
  if (typeof process.getuid !== "function" || metadata.uid !== process.getuid()) {
    throw new Error("artifact directory must be owned by the current user");
  }
  return {
    directory: await realpath(absolute),
    device: metadata.dev,
    inode: metadata.ino,
    bytes: 0,
  };
}

export async function writeArtifact(artifacts, id, svg, maximumBytes) {
  if (artifacts === undefined) return;
  if (!/^[a-f0-9]{64}$/.test(id)) throw new Error("unsafe diagram artifact identity");
  const size = Buffer.byteLength(svg, "utf8");
  if (artifacts.bytes + size > maximumBytes) throw new Error("SVG artifact byte budget exceeded");
  const metadata = await lstat(artifacts.directory);
  if (
    !metadata.isDirectory() ||
    metadata.isSymbolicLink() ||
    metadata.dev !== artifacts.device ||
    metadata.ino !== artifacts.inode
  ) {
    throw new Error("artifact directory identity changed");
  }
  const handle = await open(
    path.join(artifacts.directory, `${id}.svg`),
    constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
    0o600,
  );
  try {
    await handle.writeFile(svg, "utf8");
    artifacts.bytes += size;
  } finally {
    await handle.close();
  }
}

export async function renderDiagram(browser, bundlePath, diagram, profile) {
  let cancelled = false;
  const assertActive = () => {
    if (cancelled) throw new DeadlineExceeded("diagram render cancelled");
  };
  const operation = performRender(browser, bundlePath, diagram, profile, assertActive);
  try {
    return await withDeadline(operation, profile.diagramTimeoutMs, "diagram render");
  } catch (error) {
    if (!(error instanceof DeadlineExceeded) && !(error instanceof DiagramLifecycleError))
      throw error;
    cancelled = true;
    try {
      const [closed] = await withDeadline(
        Promise.allSettled([browser.close(), operation]),
        CLEANUP_TIMEOUT_MS,
        "renderer cancellation and join",
      );
      if (closed.status === "rejected") throw closed.reason;
    } catch (cleanupError) {
      throw new DiagramLifecycleError(`renderer did not drain: ${cleanupError.message}`, {
        cause: error,
      });
    }
    throw new DiagramLifecycleError(error.message, { cause: error });
  }
}

async function performRender(browser, bundlePath, diagram, profile, assertActive) {
  const context = await browser.newContext({ serviceWorkers: "block" });
  const externalRequests = new Set();
  const browserErrors = new Set();
  let renderedSvg;
  let failure;
  try {
    assertActive();
    await context.route("**/*", async (route) => {
      if (externalRequests.size < 5) externalRequests.add(route.request().url().slice(0, 500));
      await route.abort("blockedbyclient");
    });
    assertActive();
    const page = await context.newPage();
    assertActive();
    page.setDefaultTimeout(profile.diagramTimeoutMs);
    page.on("pageerror", (error) => {
      if (browserErrors.size < 5) browserErrors.add(error.message.slice(0, 1000));
    });
    const svg = await (async () => {
      await page.setContent(
        '<!doctype html><html lang="en"><body><div id="diagram"></div></body></html>',
      );
      assertActive();
      await page.addScriptTag({ path: bundlePath });
      assertActive();
      const rendered = await page.evaluate(
        async ({ body, id, configuration }) => {
          await document.fonts.ready;
          const container = document.getElementById("diagram");
          const mermaid = globalThis.mermaid;
          if (!mermaid?.render) throw new Error("official Mermaid browser bundle did not load");
          mermaid.initialize(configuration);
          const maximumTextSize = mermaid.mermaidAPI.getConfig().maxTextSize;
          if (!Number.isSafeInteger(maximumTextSize) || maximumTextSize <= 0) {
            throw new Error("renderer text limit must be a positive safe integer");
          }
          if (body.length > maximumTextSize) {
            throw new Error("diagram exceeds the renderer text limit");
          }
          const result = await mermaid.render(`diagram-${id}`, body, container);
          if (typeof result.svg !== "string" || !result.svg.trim()) {
            throw new Error("renderer returned no SVG");
          }
          container.innerHTML = result.svg;
          await document.fonts.ready;
          const svg = container.querySelector("svg");
          if (svg?.namespaceURI !== "http://www.w3.org/2000/svg") {
            throw new Error("renderer output contains no SVG element");
          }
          if (result.diagramType === "error") {
            throw new Error("renderer returned an error diagram");
          }
          const box = svg.getBBox();
          const viewBox = svg.viewBox.baseVal;
          for (const [label, rectangle] of [
            ["content", box],
            ["viewBox", viewBox],
          ]) {
            if (
              ![rectangle.x, rectangle.y, rectangle.width, rectangle.height].every(
                Number.isFinite,
              ) ||
              rectangle.width <= 0 ||
              rectangle.height <= 0
            ) {
              throw new Error(`SVG ${label} has empty or non-finite geometry`);
            }
          }
          return result.svg;
        },
        { body: diagram.body, id: diagram.id, configuration: profile.rendererConfig },
      );
      await page.waitForLoadState("networkidle");
      return rendered;
    })();
    if (externalRequests.size) {
      throw new Error(`external requests are forbidden: ${[...externalRequests].join(", ")}`);
    }
    if (browserErrors.size) throw new Error(`browser error: ${[...browserErrors].join("; ")}`);
    if (Buffer.byteLength(svg, "utf8") > profile.maxOutputBytes) {
      throw new Error("rendered SVG exceeds the output byte budget");
    }
    renderedSvg = svg;
  } catch (error) {
    failure = externalRequests.size
      ? new Error(`external requests are forbidden: ${[...externalRequests].join(", ")}`, {
          cause: error,
        })
      : error;
  }
  try {
    await context.close();
  } catch (error) {
    throw new DiagramLifecycleError(`context cleanup failed: ${error.message}`, {
      cause: failure ? new AggregateError([failure, error]) : error,
    });
  }
  if (failure) throw failure;
  return renderedSvg;
}
