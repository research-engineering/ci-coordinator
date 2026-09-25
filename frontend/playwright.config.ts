import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  expect: { timeout: 5_000 },
  failOnFlakyTests: true,
  forbidOnly: true,
  fullyParallel: true,
  globalSetup: "./tests/browser/globalSetup.ts",
  webServer: {
    command:
      "/usr/bin/env -i PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend/src " +
      "backend/.venv/bin/python -B scripts/operator_ui_browser_server.py frontend/dist",
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    name: "Operator ASGI transport",
    reuseExistingServer: false,
    stdout: "ignore",
    stderr: "pipe",
    timeout: 15_000,
    wait: {
      stdout:
        /^\{"origin": "(?<ci_coordinator_playwright_base_url>http:\/\/127\.0\.0\.1:[1-9]\d{0,4})"\}\r?$/m,
    },
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
    {
      name: "narrow-chromium",
      use: { ...devices["Desktop Chrome"], viewport: { height: 800, width: 320 } },
    },
  ],
  reporter: "line",
  retries: 0,
  testDir: "./tests/browser",
  use: {
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
