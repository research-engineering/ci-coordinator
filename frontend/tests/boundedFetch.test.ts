import { afterEach, describe, expect, test, vi } from "vitest";
import {
  admitRequestTimeout,
  boundedFetch,
  MAX_REQUEST_TIMEOUT_MS,
  MAX_RESPONSE_BYTES,
  ResponseLimitError,
} from "../src/api/shared/boundedFetch";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("bounded fetch", () => {
  test.each([Number.NaN, Number.POSITIVE_INFINITY, -1, 0, 1.5, MAX_REQUEST_TIMEOUT_MS + 1])(
    "rejects non-platform timeout %s",
    (timeoutMs) => {
      expect(() => admitRequestTimeout(timeoutMs)).toThrow(RangeError);
    },
  );

  test.each([Number.NaN, Number.POSITIVE_INFINITY, -1, 0, 1.5, MAX_RESPONSE_BYTES + 1])(
    "rejects non-platform response bound %s",
    async (maximumBytes) => {
      const transport = vi.fn();
      vi.stubGlobal("fetch", transport);

      await expect(boundedFetch(new Request("https://example.test"), maximumBytes)).rejects.toThrow(
        RangeError,
      );
      expect(transport).not.toHaveBeenCalled();
    },
  );

  test("admits a response whose decoded body matches the canonical declared length", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("body", { headers: { "content-length": "4" } })),
    );

    const response = await boundedFetch(new Request("https://example.test"), 4);

    await expect(response.text()).resolves.toBe("body");
  });

  test.each([
    ["3", "body"],
    ["5", "body"],
  ])("rejects declared length %s for the actual body", async (declared, body) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(body, { headers: { "content-length": declared } })),
    );

    await expect(boundedFetch(new Request("https://example.test"), 8)).rejects.toBeInstanceOf(
      ResponseLimitError,
    );
  });

  test.each(["04", "4, 4", "-1", "4.0", "9007199254740992"])(
    "rejects invalid or unrepresentable declared length %s",
    async (declared) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response("body", { headers: { "content-length": declared } })),
      );

      await expect(boundedFetch(new Request("https://example.test"), 8)).rejects.toBeInstanceOf(
        ResponseLimitError,
      );
    },
  );

  test("does not compare decoded bytes with an encoded wire length", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("decoded", {
            headers: { "content-encoding": "gzip", "content-length": "3" },
          }),
      ),
    );

    const response = await boundedFetch(new Request("https://example.test"), 16);

    await expect(response.text()).resolves.toBe("decoded");
    expect(response.headers.has("content-encoding")).toBe(false);
    expect(response.headers.has("content-length")).toBe(false);
  });

  test.each([
    ["0", false],
    ["1", true],
  ] as const)("validates bodyless response length %s", async (declared, rejected) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { headers: { "content-length": declared } })),
    );

    const result = boundedFetch(new Request("https://example.test"), 8);

    if (rejected) await expect(result).rejects.toBeInstanceOf(ResponseLimitError);
    else await expect(result).resolves.toBeInstanceOf(Response);
  });

  test.each(["declared", "streamed"] as const)(
    "does not let a non-settling %s cleanup delay the bounded failure",
    async (failurePoint) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          if (failurePoint === "streamed") controller.enqueue(new Uint8Array(9));
        },
        cancel: () => new Promise<void>(() => undefined),
      });
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response(
              body,
              failurePoint === "declared" ? { headers: { "content-length": "9" } } : {},
            ),
        ),
      );

      await expect(boundedFetch(new Request("https://example.test"), 8)).rejects.toBeInstanceOf(
        ResponseLimitError,
      );
    },
  );

  test.each(["declared", "streamed"] as const)(
    "does not let a rejected %s cleanup replace the bounded failure",
    async (failurePoint) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          if (failurePoint === "streamed") controller.enqueue(new Uint8Array(9));
        },
        cancel: () => Promise.reject(new Error("cleanup failed")),
      });
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response(
              body,
              failurePoint === "declared" ? { headers: { "content-length": "9" } } : {},
            ),
        ),
      );

      await expect(boundedFetch(new Request("https://example.test"), 8)).rejects.toBeInstanceOf(
        ResponseLimitError,
      );
      await Promise.resolve();
    },
  );
});
