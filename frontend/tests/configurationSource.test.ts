import { Blob as NativeBlob } from "node:buffer";
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MAX_SOURCE_BYTES } from "../src/api/configLifecycle/schema";
import { exportConfigSource, readSourceFile } from "../src/api/configLifecycle/source";
import {
  configEpoch,
  configurationScope,
  configurationSource,
  sourceResponse,
} from "./configurationFixture";

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("Blob", NativeBlob);
});
function asFile(bytes: Uint8Array | string): File {
  const contents =
    typeof bytes === "string" ? new TextEncoder().encode(bytes) : new Uint8Array(bytes);
  const file = new File([contents], "configuration.json");
  Object.defineProperty(file, "arrayBuffer", {
    configurable: true,
    value: async () => contents.slice().buffer,
  });
  return file;
}

test("file input preserves BOM, CRLF and multi-byte Unicode without normalization", async () => {
  const source = `\uFEFF${configurationSource}\r\n# \u00e9`;
  expect(await readSourceFile(asFile(source))).toBe(source);
});

test("file input rejects empty, oversized and malformed UTF-8 sources", async () => {
  await expect(readSourceFile(asFile(""))).rejects.toThrow();
  const oversized = asFile("a".repeat(MAX_SOURCE_BYTES + 1));
  const read = vi.spyOn(oversized, "arrayBuffer");
  await expect(readSourceFile(oversized)).rejects.toThrow();
  expect(read).not.toHaveBeenCalled();
  await expect(readSourceFile(asFile(new Uint8Array([0xc3, 0x28])))).rejects.toThrow();
});

test.each(["json", "yaml-1.2"] as const)(
  "export preserves exact %s bytes and independently computed hashes",
  async (format) => {
    const source = `${configurationSource}\r\n`;
    const epoch = configEpoch(source, format);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        expect(request.method).toBe("GET");
        expect(new URL(request.url).pathname).toBe(
          `/api/v1/config/repositories/1/1/epochs/${epoch.epochId}/source`,
        );
        return sourceResponse(source, format);
      }),
    );
    const result = await exportConfigSource(configurationScope, epoch);
    expect(result.kind).toBe("ready");
    if (result.kind !== "ready") throw new Error("Source export was not admitted");
    const buffer = await new Response(result.value).arrayBuffer();
    expect(Array.from(new Uint8Array(buffer))).toEqual(
      Array.from(new TextEncoder().encode(source)),
    );
  },
);

test.each([
  ["cache-control", "max-age=60"],
  ["content-type", "text/html"],
  ["content-type", "application/json; charset=iso-8859-1"],
  ["etag", `W/"${configEpoch().sourceHash}"`],
  ["etag", `"${"a".repeat(64)}"`],
  ["x-ci-config-epoch-id", "f".repeat(64)],
  ["content-digest", "sha-256=:wrong:"],
])("export rejects %s drift", async (header, value) => {
  const response = sourceResponse();
  response.headers.set(header, value);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response),
  );
  expect((await exportConfigSource(configurationScope, configEpoch())).kind).toBe(
    "invalid-response",
  );
});

test("export rejects changed bytes, byte count and a foreign scope", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(sourceResponse(`${configurationSource} `))
    .mockResolvedValueOnce(sourceResponse());
  vi.stubGlobal("fetch", fetch);
  expect((await exportConfigSource(configurationScope, configEpoch())).kind).toBe(
    "invalid-response",
  );
  expect(
    (await exportConfigSource(configurationScope, { ...configEpoch(), sourceByteCount: 1 })).kind,
  ).toBe("invalid-response");
  expect(
    (await exportConfigSource({ ...configurationScope, repositoryId: 2 }, configEpoch())).kind,
  ).toBe("invalid-request");
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("unchanged metadata cannot hide a same-size body mutation", async () => {
  const response = sourceResponse();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(configurationSource.replace("main", "fake"), { headers: response.headers }),
    ),
  );
  expect((await exportConfigSource(configurationScope, configEpoch())).kind).toBe(
    "invalid-response",
  );
});
