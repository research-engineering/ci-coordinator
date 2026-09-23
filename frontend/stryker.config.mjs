import { createRequire } from "node:module";

export default {
  mutate: ["src/api/shared/operationId.ts"],
  plugins: [createRequire(import.meta.url).resolve("@stryker-mutator/vitest-runner")],
  testRunner: "vitest",
  vitest: { configFile: "vitest.mutation.config.ts" },
  concurrency: 2,
  timeoutMS: 3000,
  incremental: false,
  thresholds: { high: 90, low: 80, break: 0 },
  reporters: ["clear-text", "json", "html"],
  jsonReporter: { fileName: "reports/mutation/mutation.json" },
  htmlReporter: { fileName: "reports/mutation/mutation.html" },
};
