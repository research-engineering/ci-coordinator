import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { SCENARIOS, scenarioName } from "./scenarioNames.ts";

const supplied = process.argv.slice(2);
const { values, positionals } = parseArgs({
  args: supplied[1] === "--" ? [supplied[0], ...supplied.slice(2)] : supplied,
  allowPositionals: true,
  options: { scenario: { type: "string", default: "populated" }, help: { type: "boolean" } },
});
const [mode] = positionals;
if (values.help) {
  console.log(
    `Usage: node dev/cli.mjs demo|ui|debug|record [--scenario NAME]\nScenarios: ${Object.keys(SCENARIOS).join(", ")}\nRequires locked frontend dependencies and explicit browser:prepare. Record uses the Inspector in the routed context. WebSocket/HMR is blocked; reload or reset to view edits.`,
  );
} else {
  if (positionals.length !== 1 || !["demo", "ui", "debug", "record"].includes(mode ?? ""))
    throw new Error("Expected demo, ui, debug or record; use --help");
  const selected = scenarioName(values.scenario);
  const flags = mode === "ui" ? ["--ui"] : mode === "demo" ? ["--headed"] : ["--debug"];
  const child = spawn(
    process.execPath,
    [
      createRequire(import.meta.url).resolve("@playwright/test/cli"),
      "test",
      "--config",
      "dev/playwright.config.ts",
      ...flags,
    ],
    {
      cwd: fileURLToPath(new URL("..", import.meta.url)),
      stdio: "inherit",
      env: {
        ...process.env,
        CI_COORDINATOR_DEMO_MODE: mode,
        CI_COORDINATOR_DEMO_SCENARIO: selected,
      },
    },
  );
  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"])
    process.once(signal, () => child.kill(signal));
  child.once("error", () => {
    console.error("Could not launch the installed Playwright CLI");
    process.exitCode = 1;
  });
  child.once("exit", (code) => {
    process.exitCode = code ?? 1;
  });
}
