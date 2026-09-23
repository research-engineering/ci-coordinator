import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  beginSessionLogin,
  clearSessionReturn,
  consumeSessionReturn,
  LOGIN_PATH,
} from "../src/features/auth/sessionNavigation";

const KEY = "ci-coordinator.session-return.v1";
const DESTINATION = "/workbench?installationId=1&repositoryId=1&limit=10&view=economics";
const ACTIVITY = "/workbench?installationId=1&repositoryId=1&limit=10&view=activity";
beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("location", { origin: location.origin, assign: vi.fn() });
});
afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

test("fresh SSO returns once to a canonical scope and keeps its automatic loop budget", () => {
  expect(beginSessionLogin(DESTINATION, true)).toBe(true);
  expect(location.assign).toHaveBeenCalledWith(LOGIN_PATH);
  expect(consumeSessionReturn()).toBe(DESTINATION);
  expect(consumeSessionReturn()).toBeUndefined();
  expect(beginSessionLogin(DESTINATION, true)).toBe(false);
  expect(beginSessionLogin(DESTINATION, false)).toBe(true);
  clearSessionReturn();
  expect(consumeSessionReturn()).toBeUndefined();
});

test.each([
  ...["discover", "reconciled", "observation", "history", "analytics", "budgets", "signals"].map(
    (tab) => `${DESTINATION}&economicsTab=${tab}`,
  ),
  `${ACTIVITY}&activitySource=business`,
])("restores only the admitted subview once: %s", (destination) => {
  expect(beginSessionLogin(destination, true)).toBe(true);
  expect(consumeSessionReturn()).toBe(destination);
  expect(consumeSessionReturn()).toBeUndefined();
  expect(beginSessionLogin(destination, true)).toBe(false);
});

test.each([
  "https://foreign.example",
  "//foreign.example",
  "/workbench/else",
  "/workbench#token",
  "/workbench?code=private",
  `${DESTINATION}&state=private`,
  `${DESTINATION}&economicsTab=registered`,
  `${DESTINATION}&economicsTab=unknown`,
  `${DESTINATION}&economicsTab=history&economicsTab=history`,
  `${DESTINATION}&activitySource=business`,
  `${ACTIVITY}&economicsTab=history`,
  `${ACTIVITY}&activitySource=security`,
  `${ACTIVITY}&activitySource=business&activitySource=business`,
  "/workbench?view=activity&activitySource=business",
  "/workbench?installationId=1&repositoryId=2",
  "/workbench?view=economics",
  `/workbench?${"x".repeat(1024)}`,
])("rejects unsafe or noncanonical destination %s", (value) => {
  expect(beginSessionLogin(value, false)).toBe(false);
  expect(location.assign).not.toHaveBeenCalled();
  expect(sessionStorage.length).toBe(0);
});

test.each([
  "{}",
  "null",
  "invalid",
  "x".repeat(1025),
  JSON.stringify({ version: 2, attemptedAt: 0, returnTo: "/workbench" }),
  JSON.stringify({ version: 1, attemptedAt: "0", returnTo: "/workbench" }),
  JSON.stringify({ version: 1, attemptedAt: 0, returnTo: "/workbench", csrfToken: "private" }),
])("malformed storage never becomes automatic recovery authority: %s", (raw) => {
  sessionStorage.setItem(KEY, raw);
  expect(beginSessionLogin(DESTINATION, true)).toBe(false);
  expect(consumeSessionReturn()).toBeUndefined();
  expect(beginSessionLogin(DESTINATION, true)).toBe(false);
  expect(location.assign).not.toHaveBeenCalled();
  expect(beginSessionLogin(DESTINATION, false)).toBe(true);
  expect(location.assign).toHaveBeenCalledOnce();
});

test.each([-1, 0, 119_999, 120_000, 600_000, 600_001])("time boundary %i", (age) => {
  const now = Date.now();
  vi.spyOn(Date, "now").mockReturnValue(now);
  sessionStorage.setItem(
    KEY,
    JSON.stringify({ version: 1, attemptedAt: now - age, returnTo: DESTINATION }),
  );
  expect(consumeSessionReturn()).toBe(age >= 0 && age <= 600_000 ? DESTINATION : undefined);
  expect(beginSessionLogin(DESTINATION, true)).toBe(age >= 120_000);
});

test.each(
  ["https://foreign.example", "/workbench#token", `${DESTINATION}&state=private`].flatMap(
    (returnTo) => [120_000, 180_000, 600_000].map((age) => ({ returnTo, age })),
  ),
)("invalid stored destination retains its veto at age $age: $returnTo", ({ returnTo, age }) => {
  const raw = JSON.stringify({ version: 1, attemptedAt: Date.now() - age, returnTo });
  sessionStorage.setItem(KEY, raw);
  expect(beginSessionLogin(DESTINATION, true)).toBe(false);
  expect(sessionStorage.getItem(KEY)).toBe(raw);
  expect(consumeSessionReturn()).toBeUndefined();
  expect(sessionStorage.getItem(KEY)).toBe(raw);
  expect(beginSessionLogin(DESTINATION, true)).toBe(false);
  expect(location.assign).not.toHaveBeenCalled();
  expect(beginSessionLogin(DESTINATION, false)).toBe(true);
  expect(location.assign).toHaveBeenCalledOnce();
  expect(consumeSessionReturn()).toBe(DESTINATION);
});

test.each(["getItem", "setItem"] as const)("blocked %s permits only explicit sign-in", (method) => {
  vi.spyOn(Storage.prototype, method).mockImplementation(() => {
    throw new Error("blocked");
  });
  expect(beginSessionLogin(DESTINATION, true)).toBe(false);
  expect(beginSessionLogin(DESTINATION, false)).toBe(true);
  expect(consumeSessionReturn()).toBeUndefined();
  expect(location.assign).toHaveBeenCalledOnce();
});
