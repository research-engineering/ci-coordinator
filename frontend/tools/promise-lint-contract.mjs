import assert from "node:assert/strict";
import { resolve } from "node:path";
import { ESLint } from "eslint";

const root = resolve(import.meta.dirname, "..");
const eslint = new ESLint({ cwd: root });
const rule = "@typescript-eslint/no-floating-promises";
const filePath = resolve(root, "tests/historyRead.test.tsx");
const prelude = `
import { act } from "@testing-library/react";
import type { WebSocketRoute } from "@playwright/test";
declare const socket: WebSocketRoute;
declare const thenable: PromiseLike<void>;
declare function work(): Promise<void>;
declare function report(error: unknown): void;
`;
const cases = [
  [
    "imported React boolean overload",
    'act(() => document.dispatchEvent(new Event("visibilitychange")));',
    1,
  ],
  [
    "awaited React action",
    'await act(async () => document.dispatchEvent(new Event("visibilitychange")));',
    0,
  ],
  [
    "synchronous React void overload",
    'act(() => { document.dispatchEvent(new Event("visibilitychange")); });',
    0,
  ],
  ["unowned promise", "work();", 1],
  ["awaited promise", "await work();", 0],
  ["returned promise", "return work();", 0],
  ["joined promises", "await Promise.all([work(), work()]);", 0],
  ["rejection handler", "work().catch(report);", 0],
  ["fulfillment-only handler", "work().then(() => {});", 1],
  ["explicit supervised detachment", "void work().catch(report);", 0],
  ["explicit void is not supervision proof", "void work();", 0],
  ["unowned thenable", "thenable;", 1],
  ["awaited thenable", "await thenable;", 0],
  ["imported socket close", "socket.close();", 1],
  ["returned socket close with error owner", "return socket.close().catch(report);", 0],
];

for (const path of ["src/App.tsx", "tests/historyRead.test.tsx", "dev/network.ts"]) {
  const config = await eslint.calculateConfigForFile(resolve(root, path));
  assert.equal(config?.rules[rule]?.[0], 2, `${path}: Promise rule must be an error`);
}
assert.equal(await eslint.isPathIgnored(resolve(root, "src/api/generated.ts")), true);

for (const [name, statement, errors] of cases) {
  const results = await eslint.lintText(
    `${prelude}\nasync function scenario() {\n${statement}\n}`,
    {
      filePath,
    },
  );
  assert.equal(results.length, 1, `${name}: exactly one lint result`);
  assert.deepEqual(
    results[0].messages.map(({ ruleId, severity, fatal }) => ({
      ruleId,
      severity,
      fatal: !!fatal,
    })),
    Array.from({ length: errors }, () => ({ ruleId: rule, severity: 2, fatal: false })),
    name,
  );
}

console.log(JSON.stringify({ state: "passed", staticCases: cases.length, rule }));
