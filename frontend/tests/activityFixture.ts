import {
  type ActivityPage,
  type ActivityQuery,
  activityQuerySchema,
} from "../src/api/activity/schema";

export const ACTIVITY_ACTOR = `keycloak-human:v1:${"a".repeat(64)}`;
export const ACTIVITY_ISSUER = "https://auth.example.test/realms/coordinator";
export const ACTIVITY_CURSOR = "opaque-activity-page-2";

export function activityRequest(url: URL, scope = { installationId: 7, repositoryId: 31 }) {
  for (const key of url.searchParams.keys()) {
    if (url.searchParams.getAll(key).length !== 1) throw new Error(`Duplicate parameter: ${key}`);
  }
  const { cursor, ...fields } = Object.fromEntries(url.searchParams);
  if (cursor !== undefined && cursor !== ACTIVITY_CURSOR) throw new Error("Unexpected cursor");
  const security = url.pathname.includes("/security");
  return {
    cursor: cursor ?? null,
    query: activityQuerySchema.parse({
      ...fields,
      source: security ? "security" : "business",
      limit: Number(url.searchParams.get("limit")),
      ...(security ? {} : scope),
    }),
  };
}

export function activityContinuation(query: ActivityQuery, cursor: string | null): ActivityPage {
  const page = activityPage(query);
  if (cursor === null) return { ...page, nextCursor: ACTIVITY_CURSOR };
  if (cursor !== ACTIVITY_CURSOR) throw new Error("Unexpected cursor");
  return {
    ...page,
    items: page.items.map((row) => ({
      ...row,
      sequence: 7,
      operationRef:
        query.source === "security"
          ? "22222222-2222-4222-8222-222222222222"
          : `sha256:${"d".repeat(64)}`,
      auditEventId: query.source === "security" ? null : `audit_${"c".repeat(32)}`,
      eventHash: query.source === "security" ? null : "c".repeat(64),
    })),
  };
}
export function activityQuery(source: "security" | "business" = "security"): ActivityQuery {
  return activityQuerySchema.parse({
    source,
    since: "2026-09-10T00:00:00Z",
    until: "2026-09-13T00:00:00Z",
    ...(source === "business" ? { installationId: 7, repositoryId: 31 } : {}),
  });
}
export function activityPage(query = activityQuery()): ActivityPage {
  const security = query.source === "security";
  return {
    context: {
      source: query.source,
      issuer: security ? ACTIVITY_ISSUER : null,
      installationId: query.installationId,
      repositoryId: query.repositoryId,
    },
    items: [
      {
        sequence: 11,
        source: query.source,
        action: query.action ?? (security ? "login" : "config-epoch-registration/v1"),
        outcome:
          query.action === "role_denied"
            ? "denied"
            : query.action === "export"
              ? "attempted"
              : "committed",
        occurredAt: new Date(Date.parse(query.since) + 3600000).toISOString(),
        actor: query.actor ?? ACTIVITY_ACTOR,
        issuer: security ? ACTIVITY_ISSUER : null,
        subject: security ? "administrator" : null,
        operationRef: security
          ? "11111111-1111-4111-8111-111111111111"
          : `sha256:${"c".repeat(64)}`,
        auditEventId: security ? null : `audit_${"b".repeat(32)}`,
        eventHash: security ? null : "b".repeat(64),
      },
    ],
    nextCursor: null,
    observedAt: query.until,
    retentionSeconds: security ? 2592000 : null,
    integrity: security ? "journal_transaction" : "audit_reference_only",
  };
}
