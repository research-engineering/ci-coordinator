import { describe, expect, test } from "vitest";
import { admitProxyTarget } from "../src/api/development/proxyTarget";

describe("development proxy target admission", () => {
  test.each([
    [undefined, "http://127.0.0.1:3000"],
    ["http://127.0.0.1:49151", "http://127.0.0.1:49151"],
    ["http://[::1]:49151", "http://[::1]:49151"],
    ["http://backend:3000", "http://backend:3000"],
  ])("admits %s", (input, expected) => {
    expect(admitProxyTarget(input)).toBe(expected);
  });

  test.each([
    "https://127.0.0.1:3000",
    "http://localhost:3000",
    "http://backend:3001",
    "http://ambient.invalid:3000",
    "http://operator:secret@127.0.0.1:3000",
    "http://127.0.0.1:3000/path",
    "http://127.0.0.1:3000?query=1",
    "not-a-url",
  ])("rejects %s", (input) => {
    expect(() => admitProxyTarget(input)).toThrow(/proxy target/);
  });
});
