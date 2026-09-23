import * as matchers from "@testing-library/jest-dom/matchers";
import { afterEach, beforeEach, expect, vi } from "vitest";

expect.extend(matchers);

let unexpectedConsoleCalls: string[] = [];

beforeEach(() => {
  unexpectedConsoleCalls = [];
  for (const method of ["error", "warn"] as const) {
    vi.spyOn(console, method).mockImplementation((...values: unknown[]) => {
      unexpectedConsoleCalls.push(`${method}: ${values.map(String).join(" ")}`);
    });
  }
});

afterEach(() => {
  const calls = unexpectedConsoleCalls;
  vi.restoreAllMocks();
  if (calls.length > 0) {
    throw new Error(`Unexpected console output:\n${calls.join("\n")}`);
  }
});
