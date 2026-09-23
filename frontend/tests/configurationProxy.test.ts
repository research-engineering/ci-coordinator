import { expect, test } from "vitest";
import { proxyRequestIsAdmitted } from "../src/api/development/proxyPolicy";

test.each(["validations", "epochs", "rollbacks"])("projects only POST config/%s", (operation) => {
  const path = `/api/v1/config/${operation}`;
  expect(proxyRequestIsAdmitted("POST", path)).toBe(true);
  expect(proxyRequestIsAdmitted("GET", path)).toBe(false);
  expect(proxyRequestIsAdmitted("POST", `${path}?x=1`)).toBe(false);
});

const status = "/api/v1/config/repositories/1/2/status";
test("status proxy uses the literal backend query alias", () => {
  const hash = "a".repeat(64);
  expect(proxyRequestIsAdmitted("GET", status)).toBe(true);
  expect(proxyRequestIsAdmitted("GET", `${status}?limit=100&afterEpochId=${hash}`)).toBe(true);
  for (const query of [
    "limit=0",
    "limit=101",
    "limit=01",
    "limit=1.5",
    "limit=1&limit=2",
    `afterEpochId=${hash}&afterEpochId=${hash}`,
    `after_epoch_id=${hash}`,
    `cursor=${hash}`,
    "afterEpochId=bad",
    "x=1",
  ]) {
    expect(proxyRequestIsAdmitted("GET", `${status}?${query}`)).toBe(false);
  }
  expect(proxyRequestIsAdmitted("POST", status)).toBe(false);
});

test("source proxy admits exact epoch and safe scope without query", () => {
  const path = `/api/v1/config/repositories/1/2/epochs/${"a".repeat(64)}/source`;
  expect(proxyRequestIsAdmitted("GET", path)).toBe(true);
  expect(proxyRequestIsAdmitted("GET", `${path}?download=true`)).toBe(false);
  expect(proxyRequestIsAdmitted("POST", path)).toBe(false);
  for (const invalid of [
    path.replace("/1/2/", "/0/2/"),
    path.replace("/1/2/", "/9007199254740992/2/"),
    path.replace("a".repeat(64), "A".repeat(64)),
    `https://foreign.invalid${path}`,
    path.replace("/source", "/delete"),
  ]) {
    expect(proxyRequestIsAdmitted("GET", invalid)).toBe(false);
  }
  expect(proxyRequestIsAdmitted("POST", "/api/v1/config/activations")).toBe(true);
});
