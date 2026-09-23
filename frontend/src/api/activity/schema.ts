import { z } from "zod";
import type { components } from "../generated";

export const SECURITY_ACTIONS = [
  "login",
  "logout",
  "expired",
  "revoked",
  "replaced",
  "role_denied",
  "export",
] as const;
export const BUSINESS_ACTIONS = [
  "config-epoch-registration/v1",
  "config-epoch-activation/v1",
  "config-epoch-rollback/v1",
  "ci-economics-history-configured/v1",
  "ci-economics-history-gaps-requeued/v1",
  "ci-economics-history-retention-applied/v1",
  "operator_override_applied",
  "governance-baseline-approved/v1",
] as const;

const id = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const bounded = (maximum: number) =>
  z
    .string()
    .min(1)
    .max(maximum * 2)
    .refine(
      (value) =>
        value.isWellFormed() && Array.from(value).length <= maximum && !value.includes("\u0000"),
    );
const instant = z.iso
  .datetime({ offset: true })
  .refine((value) => /:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value))
  .transform((value) => {
    const utc = value.replace(/\+00:00$/, "Z");
    const [seconds = "", fraction = ""] = utc.slice(0, -1).split(".");
    return `${seconds}.${fraction.padEnd(6, "0")}Z`;
  });
const issuer = bounded(2048).refine((value) => {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.search && !url.hash;
  } catch {
    return false;
  }
});
const actor = bounded(128).regex(
  /^(?:keycloak-(?:human|workload):v1:[0-9a-f]{64}|break-glass:v1:[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?)$/,
);
const action = z.enum([...SECURITY_ACTIONS, ...BUSINESS_ACTIONS]);
const digest = z.string().regex(/^[0-9a-f]{64}$/);

export const activityQuerySchema = z
  .strictObject({
    source: z.enum(["security", "business"]),
    since: instant,
    until: instant,
    issuer: issuer.nullable().default(null),
    installationId: id.nullable().default(null),
    repositoryId: id.nullable().default(null),
    actor: actor.nullable().default(null),
    action: action.nullable().default(null),
    limit: id.max(100).default(50),
  })
  .refine((query) => {
    const width = Date.parse(query.until) - Date.parse(query.since);
    return (
      width > 0 &&
      width <= 31 * 86400000 &&
      /\.\d{3}000Z$/.test(query.since) &&
      /\.\d{3}000Z$/.test(query.until) &&
      (query.source === "security"
        ? query.installationId === null && query.repositoryId === null
        : query.installationId !== null && query.repositoryId !== null && query.issuer === null) &&
      (query.action === null ||
        (query.source === "security" ? SECURITY_ACTIONS : BUSINESS_ACTIONS).some(
          (candidate) => candidate === query.action,
        ))
    );
  });

const item = z
  .strictObject({
    sequence: id,
    source: z.enum(["security", "business"]),
    action,
    outcome: z.enum(["committed", "denied", "attempted"]),
    occurredAt: instant,
    actor: actor.nullable(),
    issuer: issuer.nullable(),
    subject: bounded(512)
      .refine((value) => new TextEncoder().encode(value).length <= 512)
      .nullable(),
    operationRef: bounded(128),
    auditEventId: z
      .string()
      .regex(/^audit_[0-9a-f]{32}$/)
      .nullable(),
    eventHash: digest.nullable(),
  })
  .refine((row) =>
    row.source === "security"
      ? SECURITY_ACTIONS.some((value) => value === row.action) &&
        row.outcome ===
          (row.action === "role_denied"
            ? "denied"
            : row.action === "export"
              ? "attempted"
              : "committed") &&
        row.actor?.startsWith("keycloak-") === true &&
        row.issuer !== null &&
        row.subject !== null &&
        row.auditEventId === null &&
        row.eventHash === null &&
        /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(row.operationRef)
      : BUSINESS_ACTIONS.some((value) => value === row.action) &&
        row.outcome === "committed" &&
        row.issuer === null &&
        row.subject === null &&
        /^sha256:[0-9a-f]{64}$/.test(row.operationRef) &&
        row.eventHash !== null &&
        row.auditEventId === `audit_${row.eventHash.slice(0, 32)}`,
  );

export const activityPageSchema = z
  .strictObject({
    context: z.strictObject({
      source: z.enum(["security", "business"]),
      issuer: issuer.nullable(),
      installationId: id.nullable(),
      repositoryId: id.nullable(),
    }),
    items: z.array(item).max(100),
    nextCursor: bounded(1024).nullable(),
    observedAt: instant,
    retentionSeconds: id.nullable(),
    integrity: z.enum(["journal_transaction", "audit_reference_only"]),
  })
  .refine((page) => {
    const context = page.context;
    return (
      (context.source === "security"
        ? context.issuer !== null &&
          context.installationId === null &&
          context.repositoryId === null &&
          page.integrity === "journal_transaction" &&
          page.retentionSeconds === 30 * 86400
        : context.issuer === null &&
          context.installationId !== null &&
          context.repositoryId !== null &&
          page.integrity === "audit_reference_only" &&
          page.retentionSeconds === null) &&
      (page.nextCursor === null || page.items.length > 0) &&
      page.items.every(
        (row, index) =>
          row.source === context.source &&
          row.issuer === context.issuer &&
          row.occurredAt <= page.observedAt &&
          (index === 0 || row.sequence < (page.items[index - 1]?.sequence ?? 0)),
      )
    );
  }) satisfies z.ZodType<components["schemas"]["ActivityPageResponse"]>;

export type ActivityQuery = z.output<typeof activityQuerySchema>;
export type ActivityPage = z.output<typeof activityPageSchema>;

export function activityPageMatches(page: ActivityPage, query: ActivityQuery): boolean {
  const context = page.context;
  return (
    context.source === query.source &&
    context.installationId === query.installationId &&
    context.repositoryId === query.repositoryId &&
    (query.issuer === null || query.issuer === context.issuer) &&
    page.items.length <= query.limit &&
    page.items.every(
      (row) =>
        row.occurredAt >= query.since &&
        row.occurredAt < query.until &&
        (query.actor === null || row.actor === query.actor) &&
        (query.action === null || row.action === query.action),
    )
  );
}
