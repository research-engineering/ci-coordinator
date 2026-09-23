import assert from "node:assert/strict";
import { resolve } from "node:path";
import { ESLint } from "eslint";

const root = resolve(import.meta.dirname, "..");
const eslint = new ESLint({ cwd: root });
const rules = new Set([
  "no-restricted-globals",
  "no-restricted-properties",
  "no-restricted-syntax",
]);
const rejected = [
  [
    "global shorthand escape",
    'const box = { globalThis }; const key = "fetch"; void box.globalThis[key]("/api/v1/workbench");',
  ],
  ["direct fetch", 'void fetch("/api/v1/workbench");'],
  ["fetch alias", 'const send = fetch; void send("/api/v1/workbench");'],
  ["global fetch", 'void globalThis.fetch("/api/v1/workbench");'],
  ["global member alias", 'const send = globalThis.fetch; void send("/api/v1/workbench");'],
  ["window fetch", 'void window.fetch("/api/v1/workbench");'],
  ["self fetch", 'void self.fetch("/api/v1/workbench");'],
  ["literal computed fetch", 'void globalThis["fetch"]("/api/v1/workbench");'],
  ["dynamic computed fetch", 'const key = "fetch"; void globalThis[key]("/api/v1/workbench");'],
  ["global destructuring", 'const { fetch: send } = globalThis; void send("/api/v1/workbench");'],
  [
    "global alias escape",
    'const browser = globalThis; const key = "fetch"; void browser[key]("/api/v1/workbench");',
  ],
  [
    "global argument escape",
    'const send = Reflect.get(globalThis, "fetch"); void send("/api/v1/workbench");',
  ],
  ["global cast", 'void (globalThis as typeof globalThis).fetch("/api/v1/workbench");'],
  ["nested global alias", 'void globalThis.window.fetch("/api/v1/workbench");'],
  ["XMLHttpRequest", "new XMLHttpRequest();"],
  ["WebSocket", 'new WebSocket("wss://example.invalid");'],
  ["EventSource", 'new EventSource("/api/v1/events");'],
  ["beacon", 'navigator.sendBeacon("/api/v1/workbench", "payload");'],
];
const admitted = [
  [
    "bounded owner call",
    'import { boundedFetch } from "./api/shared/boundedFetch"; void boundedFetch(new Request("https://example.invalid"));',
  ],
  ["static origin", "const origin = globalThis.location.origin;"],
  ["local window value", 'const window = { createdFrom: "now" }; void window.createdFrom;'],
  ["local fetch value", "const fetch = () => 1; void fetch();"],
  ["type-only global", "type Browser = typeof globalThis;"],
];

async function check(name, source, filePath, reject) {
  const results = await eslint.lintText(source, { filePath: resolve(root, filePath) });
  assert.equal(results.length, 1, `${name}: exactly one lint result`);
  const messages = results[0].messages;
  assert.ok(
    messages.every(({ fatal }) => !fatal),
    `${name}: valid admitted syntax`,
  );
  if (reject) {
    assert.ok(
      messages.some(({ ruleId }) => rules.has(ruleId)),
      `${name}: transport rejected`,
    );
    assert.ok(
      messages.every(({ ruleId }) => rules.has(ruleId)),
      `${name}: no unrelated rejection`,
    );
  } else {
    assert.deepEqual(messages, [], `${name}: legitimate use admitted`);
  }
}

for (const [name, source] of rejected) await check(name, source, "src/App.tsx", true);
for (const [name, source] of admitted) await check(name, source, "src/App.tsx", false);
for (const path of ["src/App.tsx", "src/new.ts", "src/new.mts", "src/new.cts"]) {
  const config = await eslint.calculateConfigForFile(resolve(root, path));
  for (const rule of rules) assert.equal(config?.rules[rule]?.[0], 2, `${path}: ${rule} required`);
}
await check(
  "transport owner",
  'void globalThis.fetch("/api/v1/workbench");',
  "src/api/shared/boundedFetch.ts",
  false,
);
await check(
  "owner cannot add WebSocket",
  'new WebSocket("wss://example.invalid");',
  "src/api/shared/boundedFetch.ts",
  true,
);
await check("test mock", 'void globalThis.fetch("/fixture");', "tests/historyRead.test.tsx", false);
assert.equal(await eslint.isPathIgnored(resolve(root, "src/api/generated.ts")), true);

console.log(
  JSON.stringify({ state: "passed", staticCases: rejected.length + admitted.length + 3 }),
);
