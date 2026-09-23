import { writeFileSync } from "node:fs";
import path from "node:path";

import { chromium } from "@playwright/test";

const [option, directory] = process.argv.slice(2);
if (option !== "--artifacts" || !directory || process.argv.length !== 4) {
  throw new Error("this preload fixture requires the CLI's private artifact directory");
}
const record = (name, value) => {
  writeFileSync(path.join(directory, `${name}.json`), JSON.stringify(value), {
    flag: "wx",
    mode: 0o600,
  });
};

record("node", { pid: process.pid });
process.once("exit", (code) => record("node-exit", { code }));
const launch = chromium.launch.bind(chromium);
chromium.launch = async (options) => {
  const browser = await launch(options);
  const page = await browser.newPage();
  await page.setContent(
    '<!doctype html><html lang="en"><body>Owned lifecycle fixture</body></html>',
  );
  const session = await browser.newBrowserCDPSession();
  const { processInfo } = await session.send("SystemInfo.getProcessInfo");
  const browserProcess = processInfo.find(({ type }) => type === "browser");
  if (!browserProcess) throw new Error("CDP did not identify the owned browser process");
  record("ready", {
    node: process.pid,
    browser: browserProcess.id,
    processes: processInfo.map(({ id }) => id),
  });
  await new Promise((resolve) => setTimeout(resolve, 40000));
  return browser;
};
