import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "interactive.playwright.ts",
  outputDir: "../test-results/demo",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 0,
  reporter: "line",
  use: { headless: false, browserName: "chromium", trace: "off", screenshot: "off", video: "off" },
});
