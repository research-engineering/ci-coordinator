import type { z } from "zod";

export interface SyntheticRequest {
  readonly url: URL;
  readonly method: string;
  readonly body: unknown;
  readonly csrf: string | undefined;
}

export interface SyntheticResponse {
  readonly status: number;
  readonly json: unknown;
}

export function response<T>(
  schema: z.ZodType<T>,
  payload: unknown,
  status = 200,
): SyntheticResponse {
  return { status, json: schema.parse(payload) };
}

export function exactQuery(url: URL, expected: Record<string, string>): boolean {
  const entries = [...url.searchParams];
  return (
    entries.length === Object.keys(expected).length &&
    entries.every(([key, value]) => expected[key] === value) &&
    new Set(entries.map(([key]) => key)).size === entries.length
  );
}
