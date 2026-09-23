import { expect, test } from "vitest";
import {
  formatCount,
  formatDateTime,
  formatInteger,
  formatPolicySource,
  shortIdentity,
} from "../src/domain/format";

test.each([
  ["2026-07-18T08:00:00Z", "18 Jul 2026, 08:00:00 UTC"],
  ["2026-07-18T10:00:00+02:00", "18 Jul 2026, 08:00:00 UTC"],
  ["invalid", "invalid"],
] as const)("formats timestamp %s deterministically", (value, expected) => {
  expect(formatDateTime(value)).toBe(expected);
});

test.each([
  [0, "record", "0 records"],
  [1, "assessment", "1 assessment"],
  [1, "workflow", "1 workflow"],
  [1, "record", "1 record"],
  [1, "blob", "1 blob"],
  [1234, "record", "1,234 records"],
] as const)("formats %s %s count", (count, noun, expected) => {
  expect(formatCount(count, noun)).toBe(expected);
});

test.each([
  [
    '{"name":"quoted \\"value\\"","checks":[1,true]}',
    '{\n  "name": "quoted \\"value\\"",\n  "checks": [\n    1,\n    true\n  ]\n}',
  ],
  ['{"id":9007199254740993}', '{"id":9007199254740993}'],
  ['{"ratio":1.00000000000000001}', '{"ratio":1.00000000000000001}'],
  ['{"zero":-0}', '{"zero":-0}'],
  ['{"unfinished":', '{"unfinished":'],
  ["checks:\n  - full-ci", "checks:\n  - full-ci"],
] as const)("formats policy without changing numeric evidence: %s", (source, expected) => {
  expect(formatPolicySource(source)).toBe(expected);
});

test("formats identities and integers for a stable English interface", () => {
  expect(formatInteger(12_345)).toBe("12,345");
  expect(shortIdentity("0123456789abcdef0123456789")).toBe("01234567..456789");
});
