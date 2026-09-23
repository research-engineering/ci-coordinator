import { execFile } from "node:child_process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { expect, test } from "vitest";

test("the Vite native configuration import graph admits archive queries without a bundler", async () => {
  const module = pathToFileURL(resolve("src/api/development/economicsProxyPolicy.ts")).href;
  const source = `import { economicsProxyRequestIsAdmitted as admit } from ${JSON.stringify(module)};
const path = "https://local.test/api/v2/economics/repositories/1/2/history/archive/records?generation=3";
console.log(JSON.stringify([admit("GET", new URL(path)), admit("GET", new URL(path + "&actor=other"))]));`;
  const { stdout } = await promisify(execFile)(
    process.execPath,
    ["--input-type=module", "--eval", source],
    {
      timeout: 10000,
      maxBuffer: 4096,
      env: { PATH: process.env["PATH"] },
    },
  );
  expect(JSON.parse(stdout)).toEqual([true, false]);
});
