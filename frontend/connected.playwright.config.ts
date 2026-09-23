import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/connected",
  testMatch: "administratorBudget.spec.ts",
  forbidOnly: true,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: "line",
  outputDir: "/tmp/connected-playwright-results",
  projects: [{ name: "connected-chromium", use: { ...devices["Desktop Chrome"] } }],
  use: {
    baseURL: "https://coordinator.test",
    ignoreHTTPSErrors: false,
    launchOptions: {
      downloadsPath: "/tmp/connected-playwright-downloads",
      args: ["--disable-crash-reporter", "--crash-dumps-dir=/tmp/connected-chromium-crashes"],
    },
    screenshot: "off",
    trace: "off",
    video: "off",
  },
});
