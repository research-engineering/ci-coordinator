import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    coverage: {
      exclude: ["src/api/generated.ts", "src/domain/**", "src/main.tsx"],
      include: [
        "src/api/**/*.ts",
        "src/features/auth/useControlPlaneSession.ts",
        "src/features/auth/sessionNavigation.ts",
        "src/features/ciEconomics/use*.ts",
        "src/features/ciEconomics/{History,Observation,BudgetPolicy}Editor.tsx",
        "src/features/workbench/use*.ts",
        "src/features/workbench/navigation.ts",
      ],
      provider: "v8",
      reporter: ["text", "json-summary"],
      reportsDirectory: "coverage",
      thresholds: {
        branches: 70,
        functions: 80,
        lines: 85,
        statements: 80,
        "src/api/{configActivation,controlPlaneIdentity,repositoryAttestation}/{client,schema}.ts":
          {
            branches: 70,
            functions: 100,
            lines: 85,
            statements: 80,
          },
        "src/api/development/*.ts": {
          branches: 93,
          functions: 100,
          lines: 96,
          statements: 93,
        },
        "src/api/governanceBaseline/*.ts": {
          branches: 86,
          functions: 100,
          lines: 90,
          statements: 87,
        },
        "src/api/governanceComparison/*.ts": {
          branches: 85,
          functions: 100,
          lines: 87,
          statements: 85,
        },
        "src/api/governanceObservation/*.ts": {
          branches: 83,
          functions: 100,
          lines: 94,
          statements: 92,
        },
        "src/api/providerInventory/*.ts": {
          branches: 83,
          functions: 100,
          lines: 90,
          statements: 90,
        },
        "src/api/shared/boundedFetch.ts": {
          branches: 90,
          functions: 100,
          lines: 95,
          statements: 90,
        },
        "src/api/workbench/*.ts": {
          branches: 98,
          functions: 100,
          lines: 100,
          statements: 98,
        },
        "src/api/workflowDiscovery/{client,manifestIdentity,policyProof,schema}.ts": {
          branches: 80,
          functions: 90,
          lines: 89,
          statements: 88,
        },
        "src/features/auth/useControlPlaneSession.ts": {
          branches: 75,
          functions: 75,
          lines: 90,
          statements: 85,
        },
        "src/features/workbench/use*.ts": {
          branches: 65,
          functions: 75,
          lines: 88,
          statements: 82,
        },
        "src/features/ciEconomics/use*.ts": {
          branches: 65,
          functions: 75,
          lines: 88,
          statements: 82,
        },
      },
    },
    environment: "jsdom",
    exclude: ["**/node_modules/**", "**/dist/**", "tests/browser/**", "tests/connected/**"],
    globals: true,
    setupFiles: ["./tests/setup.ts"],
  },
});
