import { describe, expect, it } from "vitest";
import { operationIdIsAdmitted } from "../src/api/shared/operationId";

describe("operation ID admission", () => {
  it.each([
    ["", false],
    ["operation-1", true],
    ["a".repeat(256), true],
    ["a".repeat(257), false],
    ["\u00e9".repeat(128), true],
    [`${"\u00e9".repeat(128)}a`, false],
    ["\u{1f680}".repeat(64), true],
    [`${"\u{1f680}".repeat(64)}a`, false],
    ["a\0b", false],
    ["\ud800", false],
    ["\udbff", false],
    ["\udc00", false],
    ["\udfff", false],
    ["\ud800a", false],
    ["\ud800\ue000", false],
    ["\ud800\uffff", false],
    ["\ud800\ud800", false],
    ["\ud800\udc00", true],
    ["\udbff\udfff", true],
    ["\ud7ff", true],
    ["\ue000", true],
    ["a\ud800\udc00b", true],
  ])("admits %j: %s", (value, admitted) => {
    expect(operationIdIsAdmitted(value)).toBe(admitted);
  });
});
