import { on } from "node:events";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";

function moduleSource(value: string): string {
  return `const value = ${JSON.stringify(value)};
const output = document.querySelector('#watch-value');
if (import.meta.hot) {
  import.meta.hot.accept();
  output.setAttribute('data-hmr-accept', value);
}
output.textContent = value;
`;
}

async function removed(watcher: ViteDevServer["watcher"], path: string): Promise<void> {
  for await (const [file] of on(watcher, "unlink", { signal: AbortSignal.timeout(5_000) })) {
    if (file === path) return;
  }
}

test("the development watcher renders a save made after the previous HMR response", async ({
  page,
}) => {
  const root = await mkdtemp(join(tmpdir(), "ci-development-watch-"));
  const probe = join(root, "probe.js");
  const configFile = fileURLToPath(new URL("../../vite.config.ts", import.meta.url));
  let server: ViteDevServer | undefined;
  let pageErrors = 0;
  let consoleErrors = 0;
  page.on("pageerror", () => pageErrors++);
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors++;
  });

  try {
    await writeFile(join(root, "package.json"), '{"type":"module"}\n');
    await writeFile(
      join(root, "index.html"),
      '<!doctype html><title>Development watch</title><link rel="icon" href="data:,">' +
        '<p id="watch-value"></p><script type="module" src="/probe.js"></script>',
    );
    server = await createServer({
      configFile,
      configLoader: "native",
      logLevel: "silent",
      root,
      server: { host: "127.0.0.1", port: 0 },
    });
    expect(server.config.configFile).toBe(configFile);
    await server.listen();
    const address = server.httpServer?.address();
    if (!address || typeof address === "string") {
      throw new Error("Development watch server did not bind loopback");
    }
    const origin = `http://127.0.0.1:${address.port}`;
    await writeFile(probe, moduleSource("initial"), { flag: "wx" });
    const initialResponse = page.waitForResponse(`${origin}/probe.js`, { timeout: 5_000 });
    const [response] = await Promise.all([initialResponse, page.goto(origin)]);
    expect(response.status()).toBe(200);
    expect(await response.finished()).toBeNull();
    expect(await response.text()).toContain('"initial"');
    const output = page.locator("#watch-value");
    await expect(output).toHaveText("initial");
    await expect(output).toHaveAttribute("data-hmr-accept", "initial");
    await page.locator("html").evaluate((element) => {
      element.setAttribute("data-hmr-document", "original");
    });

    let previousResponse = response.url();
    for (const value of ["created", "updated"]) {
      await test.step(`Render ${value} module`, async () => {
        const received = page.waitForResponse(
          (next) => {
            const url = new URL(next.url());
            return (
              url.origin === origin &&
              url.pathname === "/probe.js" &&
              url.searchParams.has("t") &&
              next.url() !== previousResponse
            );
          },
          { timeout: 5_000 },
        );
        // The next save follows the actual prior body/render, without a quiet delay.
        const [next] = await Promise.all([received, writeFile(probe, moduleSource(value))]);
        expect(await readFile(probe, "utf8")).toBe(moduleSource(value));
        expect(next.status()).toBe(200);
        expect(await next.finished()).toBeNull();
        expect(await next.text()).toContain(JSON.stringify(value));
        await expect(output).toHaveText(value);
        await expect(output).toHaveAttribute("data-hmr-accept", value);
        await expect(page.locator("html")).toHaveAttribute("data-hmr-document", "original");
        previousResponse = next.url();
      });
    }
    expect(pageErrors).toBe(0);
    expect(consoleErrors).toBe(0);
    await page.close();
    await Promise.all([removed(server.watcher, probe), rm(probe)]);
    await expect(readFile(probe)).rejects.toMatchObject({ code: "ENOENT" });
  } finally {
    try {
      await page.close();
    } finally {
      try {
        await server?.close();
      } finally {
        await rm(root, { recursive: true, force: true });
      }
    }
  }
});
