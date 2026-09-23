import { z } from "zod";
import type { components } from "../generated";

export type ControlPlaneSession = components["schemas"]["ControlPlaneSessionResponse"];
export type ControlPlaneLogout = components["schemas"]["ControlPlaneLogoutResponse"];
type ControlPlaneIdentityError = components["schemas"]["ControlPlaneIdentityErrorResponse"];

export const CONTROL_PLANE_ROLES = ["activate", "audit", "configure", "override", "read"] as const;
export type ControlPlaneRole = (typeof CONTROL_PLANE_ROLES)[number];

const controlPlaneRole = z.enum(CONTROL_PLANE_ROLES);

export const controlPlaneIdentityErrorSchema: z.ZodType<ControlPlaneIdentityError> = z.strictObject(
  {
    error: z.enum([
      "forbidden",
      "invalid_login",
      "invalid_logout",
      "overloaded",
      "rate_limited",
      "unauthenticated",
      "unavailable",
    ]),
    ok: z.literal(false),
  },
);

export const controlPlaneSessionSchema: z.ZodType<ControlPlaneSession> = z.strictObject({
  csrfToken: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
  expiresAt: z.iso.datetime({ offset: true }),
  ok: z.literal(true),
  roles: z
    .array(controlPlaneRole)
    .max(CONTROL_PLANE_ROLES.length)
    .refine(
      (roles) =>
        roles.every((role, index) => {
          const previous = roles[index - 1];
          return previous === undefined || previous < role;
        }),
      "roles are not canonical",
    ),
  user: z.strictObject({
    actorId: z.string().regex(/^keycloak-human:v1:[0-9a-f]{64}$/),
    displayName: z.string().max(512).nullable(),
    preferredUsername: z.string().max(256).nullable(),
  }),
});

export const controlPlaneLogoutSchema: z.ZodType<ControlPlaneLogout> = z.strictObject({
  ok: z.literal(true),
  redirectUrl: z
    .string()
    .max(2_048)
    .refine((value) => value === "/workbench" || admittedHttpsUrl(value), "redirect is unsafe"),
});

function admittedHttpsUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password;
  } catch {
    return false;
  }
}
