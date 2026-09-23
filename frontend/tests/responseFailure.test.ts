import { expect, test } from "vitest";
import { ZodError } from "zod";
import { validRepositoryScope } from "../src/api/ciEconomics/sourceSchema";
import { ResponseLimitError } from "../src/api/shared/boundedFetch";
import { caughtFailure } from "../src/api/shared/responseFailure";

test.each([new ZodError([]), new SyntaxError(), new ResponseLimitError()])(
  "invalid response %s takes precedence over caller cancellation",
  (error) => {
    expect(caughtFailure(error)).toEqual({ kind: "invalid-response" });
    expect(caughtFailure(error, new AbortController().signal)).toEqual({
      kind: "invalid-response",
    });
    expect(caughtFailure(error, AbortSignal.abort())).toEqual({ kind: "invalid-response" });
  },
);

test.each([new Error(), new RangeError(), new DOMException("cancelled", "AbortError"), null])(
  "ordinary failure %s retains caller cancellation and exception identity",
  (error) => {
    expect(caughtFailure(error)).toEqual({ kind: "network-failure" });
    expect(caughtFailure(error, new AbortController().signal)).toEqual({
      kind: "network-failure",
    });
    let caught = false;
    try {
      caughtFailure(error, AbortSignal.abort());
    } catch (actual) {
      caught = true;
      expect(actual).toBe(error);
    }
    expect(caught).toBe(true);
  },
);

test("repository scope admission preserves extra properties", () => {
  const scope = { installationId: 1, repositoryId: 2, limit: 10 };
  expect(validRepositoryScope(scope)).toBe(true);
});

test.each([0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, Number.NaN, Number.POSITIVE_INFINITY])(
  "repository scope independently rejects invalid coordinate %s",
  (value) => {
    expect(validRepositoryScope({ installationId: value, repositoryId: 1 })).toBe(false);
    expect(validRepositoryScope({ installationId: 1, repositoryId: value })).toBe(false);
  },
);
