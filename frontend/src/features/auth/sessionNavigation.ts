import { z } from "zod";
import { consoleHref, readConsoleRoute } from "../workbench/navigation";

export const LOGIN_PATH = "/api/v1/auth/keycloak/start";
const STORAGE_KEY = "ci-coordinator.session-return.v1";
const HINT_TTL_MS = 600_000;
const AUTOMATIC_COOLDOWN_MS = 120_000;
const MAX_HINT_LENGTH = 1024;
const hintSchema = z.strictObject({
  version: z.literal(1),
  attemptedAt: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
  returnTo: z
    .string()
    .max(MAX_HINT_LENGTH)
    .refine((value) => canonicalDestination(value) !== undefined)
    .nullable(),
});

function canonicalDestination(value: string): string | undefined {
  if (!value.startsWith("/workbench") || value.length > MAX_HINT_LENGTH) return;
  const url = new URL(value, globalThis.location.origin);
  if (url.origin !== globalThis.location.origin || url.pathname !== "/workbench" || url.hash)
    return;
  const canonical = consoleHref(readConsoleRoute(url.search));
  return canonical === value ? canonical : undefined;
}

export function beginSessionLogin(returnTo: string, automatic: boolean): boolean {
  const destination = canonicalDestination(returnTo);
  if (!destination) return false;
  try {
    const now = Date.now();
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (automatic && raw !== null) {
      if (raw.length > MAX_HINT_LENGTH) return false;
      const previous = hintSchema.safeParse(JSON.parse(raw));
      if (!previous.success || now - previous.data.attemptedAt < AUTOMATIC_COOLDOWN_MS)
        return false;
    }
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: 1, attemptedAt: now, returnTo: destination }),
    );
  } catch {
    if (automatic) return false;
  }
  globalThis.location.assign(LOGIN_PATH);
  return true;
}

export function consumeSessionReturn(): string | undefined {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw === null) return;
    if (raw.length > MAX_HINT_LENGTH) return;
    const parsed = hintSchema.safeParse(JSON.parse(raw));
    if (!parsed.success) return;
    const hint = parsed.data;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ ...hint, returnTo: null }));
    const age = Date.now() - hint.attemptedAt;
    if (age < 0 || age > HINT_TTL_MS || hint.returnTo === null) return;
    return hint.returnTo;
  } catch {
    return;
  }
}

export function clearSessionReturn(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    return;
  }
}
