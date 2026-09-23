import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  expect: { timeout: 5_000 },
  failOnFlakyTests: true,
  forbidOnly: true,
  fullyParallel: true,
  globalSetup: "./tests/browser/globalSetup.ts",
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
