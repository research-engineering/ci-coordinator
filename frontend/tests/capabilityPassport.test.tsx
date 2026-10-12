import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";
import { installationSchema } from "../src/api/providerInventory/schema";
import { fetchCapabilityPassport } from "../src/api/workbench/capabilityPassportClient";
import {
  type CapabilityPassport,
  capabilityPassportSchema,
} from "../src/api/workbench/capabilityPassportSchema";
import {
  CapabilityPassportPanel,
  capabilityRows,
} from "../src/features/workbench/CapabilityPassportPanel";
import { capabilityPassportFixture } from "./capabilityPassportFixture";
import { installationFixture } from "./fixture";

afterEach(() => vi.unstubAllGlobals());

const SCOPE = { installationId: 1, repositoryId: 1 };
const NEXT_ACTIONS = [
  "request_access",
  "repair_webhook",
  "refresh_evidence",
  "complete_configuration",
  "none",
] as const;
const base = capabilityPassportFixture();
const deniedGrant: CapabilityPassport["installationGrant"] = {
  ...base.installationGrant,
  installation: null,
  reason: "inventory_grant_absent",
  status: "denied",
};
const rateLimitedGrant: CapabilityPassport["installationGrant"] = {
  ...base.installationGrant,
  installation: null,
  reason: "rate_limited",
  status: "unavailable",
};
const observedLimit: CapabilityPassport["providerLimits"] = {
  ...base.providerLimits,
  reason: "rate_limited",
  retryAfterSeconds: 60,
  status: "known",
};
const unreadReconciliation: CapabilityPassport["reconciliation"] = {
  ...base.reconciliation,
  enabled: null,
  gapsRecorded: null,
  lanes: [],
  observedAt: null,
};

function keys(
  status: CapabilityPassport["actionsOidcKeys"]["status"],
  reason: CapabilityPassport["actionsOidcKeys"]["reason"],
): CapabilityPassport["actionsOidcKeys"] {
  const cached = status !== "unknown";
  return {
    ...base.actionsOidcKeys,
    ageSeconds: cached ? 400 : null,
    keyCount: cached ? 2 : 0,
    reason,
    status,
  };
}

describe("capability passport admission", () => {
  test("admits a bounded passport with exact App grants", () => {
    expect(capabilityPassportSchema.parse(capabilityPassportFixture()).nextAction).toBe("none");
  });

  // Each fact set admits exactly the backend-derived action and rejects every other one.
  test.each<[string, Partial<CapabilityPassport>, CapabilityPassport["nextAction"]]>([
    ["current evidence", {}, "none"],
    ["a denied grant", { installationGrant: deniedGrant }, "request_access"],
    [
      "denied observation access",
      {
        reconciliation: { ...unreadReconciliation, reason: "access_denied", status: "denied" },
      },
      "request_access",
    ],
    [
      "a suspended installation",
      {
        installationGrant: {
          ...base.installationGrant,
          installation: installationFixture({ state: "suspended" }),
        },
      },
      "request_access",
    ],
    [
      "a rate-limited grant read",
      { installationGrant: rateLimitedGrant, providerLimits: observedLimit },
      "refresh_evidence",
    ],
    [
      "an unavailable observation store",
      {
        reconciliation: {
          ...unreadReconciliation,
          reason: "store_unavailable",
          status: "unavailable",
        },
      },
      "refresh_evidence",
    ],
    [
      "unconfigured observation",
      {
        reconciliation: {
          ...unreadReconciliation,
          observedAt: base.observedAt,
          reason: "observation_not_configured",
          status: "unknown",
        },
      },
      "complete_configuration",
    ],
    [
      "paused observation",
      {
        reconciliation: { ...base.reconciliation, enabled: false, reason: "observation_paused" },
      },
      "complete_configuration",
    ],
    [
      "recorded gaps",
      { reconciliation: { ...base.reconciliation, gapsRecorded: true } },
      "refresh_evidence",
    ],
    [
      "expired trust keys",
      { actionsOidcKeys: keys("expired", "cache_expired") },
      "refresh_evidence",
    ],
    ["absent trust keys", { actionsOidcKeys: keys("unknown", "cache_absent") }, "refresh_evidence"],
    ["a closed key cache", { actionsOidcKeys: keys("unknown", "cache_closed") }, "none"],
    ["no configured key cache", { actionsOidcKeys: keys("unknown", "not_configured") }, "none"],
  ])("derives the next action for %s", (_name, facts, expected) => {
    // Kills: a next action that is accepted without matching the facts that derive it.
    expect(
      capabilityPassportSchema.parse(capabilityPassportFixture({ ...facts, nextAction: expected }))
        .nextAction,
    ).toBe(expected);
    for (const action of NEXT_ACTIONS.filter((candidate) => candidate !== expected)) {
      expect(
        capabilityPassportSchema.safeParse(
          capabilityPassportFixture({ ...facts, nextAction: action }),
        ).success,
      ).toBe(false);
    }
  });

  test.each<[string, Partial<CapabilityPassport>]>([
    ["a repair derived from unknown webhook evidence", { nextAction: "repair_webhook" }],
    ["a denied grant with no derived action", { installationGrant: deniedGrant }],
    [
      "a denied grant that still carries an installation",
      {
        installationGrant: {
          ...capabilityPassportFixture().installationGrant,
          reason: "inventory_grant_absent",
          status: "denied",
        },
      },
    ],
    [
      "an installation outside the passport scope",
      {
        installationGrant: {
          ...capabilityPassportFixture().installationGrant,
          installation: installationFixture({ installationId: 2 }),
        },
      },
    ],
    [
      "expired trust keys reported without a reason",
      { actionsOidcKeys: { ...base.actionsOidcKeys, status: "expired" } },
    ],
    [
      "expired trust keys explained by an absent cache",
      { actionsOidcKeys: { ...keys("expired", "cache_expired"), reason: "cache_absent" } },
    ],
    [
      "absent trust keys that still report cached keys",
      { actionsOidcKeys: { ...keys("unknown", "cache_absent"), keyCount: 2 } },
    ],
    [
      "absent trust keys that still report a cache age",
      { actionsOidcKeys: { ...keys("unknown", "cache_absent"), ageSeconds: 3 } },
    ],
    [
      "enabled observation reported as paused",
      { reconciliation: { ...base.reconciliation, reason: "observation_paused" } },
    ],
    [
      "observation denial explained as an unavailable store",
      {
        nextAction: "request_access",
        reconciliation: { ...unreadReconciliation, reason: "store_unavailable", status: "denied" },
      },
    ],
    [
      "unread observation with an observation time",
      {
        nextAction: "refresh_evidence",
        reconciliation: {
          ...unreadReconciliation,
          observedAt: base.observedAt,
          reason: "store_unavailable",
          status: "unavailable",
        },
      },
    ],
    [
      "known observation without gap evidence",
      { reconciliation: { ...base.reconciliation, gapsRecorded: null } },
    ],
    [
      "unconfigured observation with an enabled flag",
      {
        nextAction: "complete_configuration",
        reconciliation: {
          ...unreadReconciliation,
          enabled: true,
          observedAt: base.observedAt,
          reason: "observation_not_configured",
          status: "unknown",
        },
      },
    ],
    [
      "unread observation with scan lanes",
      {
        nextAction: "request_access",
        reconciliation: {
          ...unreadReconciliation,
          lanes: base.reconciliation.lanes,
          reason: "access_denied",
          status: "denied",
        },
      },
    ],
    [
      "unread observation with a truncation horizon",
      {
        nextAction: "request_access",
        reconciliation: {
          ...unreadReconciliation,
          detailTruncatedUntil: base.observedAt,
          reason: "access_denied",
          status: "denied",
        },
      },
    ],
    ["a provider limit that the grant read did not observe", { providerLimits: observedLimit }],
    [
      "a rate-limited grant read without a provider limit",
      { installationGrant: rateLimitedGrant, nextAction: "refresh_evidence" },
    ],
    [
      "an unknown provider limit with a retry delay",
      { providerLimits: { ...base.providerLimits, retryAfterSeconds: 30 } },
    ],
    [
      "a known provider limit without a rate limit",
      { providerLimits: { ...observedLimit, reason: "no_retained_limit_observation" } },
    ],
    [
      "an unavailable grant without a failure reason",
      {
        installationGrant: { ...rateLimitedGrant, reason: null },
        nextAction: "refresh_evidence",
      },
    ],
    [
      "a known grant without an installation",
      { installationGrant: { ...base.installationGrant, installation: null } },
    ],
    [
      "webhook silence explained by another source's reason",
      { webhookObservation: { ...base.webhookObservation, reason: "not_projected" } },
    ],
    [
      "production-admission keys attributed to the delivery inbox",
      {
        productionAdmissionKeys: {
          ...base.productionAdmissionKeys,
          source: "history_delivery_inbox",
        },
      },
    ],
  ])("rejects %s", (_name, overrides) => {
    // Kills: browser admission that displays contradictory or invented capability evidence.
    expect(capabilityPassportSchema.safeParse(capabilityPassportFixture(overrides)).success).toBe(
      false,
    );
  });

  test("keeps unknown grants distinct from empty grants and rejects noncanonical names", () => {
    expect(installationSchema.parse(installationFixture()).permissions).toBeNull();
    expect(
      installationSchema.parse(installationFixture({ events: [], permissions: [] })).events,
    ).toEqual([]);
    expect(
      installationSchema.safeParse(installationFixture({ events: ["workflow_run", "check_run"] }))
        .success,
    ).toBe(false);
  });
});

describe("capability passport client", () => {
  test.each([
    [403, "forbidden", "forbidden"],
    [401, "unauthenticated", "unauthenticated"],
    [503, "unavailable", "unavailable"],
    [403, "unauthenticated", "invalid-response"],
  ] as const)("maps %s %s to %s", async (status, error, expected) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error, ok: false }, { status })),
    );
    await expect(fetchCapabilityPassport(SCOPE)).resolves.toEqual({ kind: expected });
  });

  test.each([
    ["a non-integer installation", { installationId: 1.5, repositoryId: 1 }],
    ["a zero installation", { installationId: 0, repositoryId: 1 }],
    ["an unsafe repository", { installationId: 1, repositoryId: Number.MAX_SAFE_INTEGER + 1 }],
    ["a zero repository", { installationId: 1, repositoryId: 0 }],
  ])("refuses %s before any request", async (_name, scope) => {
    // Kills: an unbounded path identity forwarded to the passport route.
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(fetchCapabilityPassport(scope)).resolves.toEqual({ kind: "invalid-response" });
    expect(fetch).not.toHaveBeenCalled();
  });

  test.each([
    [
      "a transport failure",
      async () => Promise.reject(new TypeError("offline")),
      "network-failure",
    ],
    [
      "a malformed error body",
      async () => Response.json({ ok: false }, { status: 503 }),
      "invalid-response",
    ],
    [
      "an error status with a passport body",
      async () => Response.json(capabilityPassportFixture(), { status: 500 }),
      "invalid-response",
    ],
    [
      "a contradictory passport",
      async () => Response.json(capabilityPassportFixture({ nextAction: "request_access" })),
      "invalid-response",
    ],
  ] as const)("maps %s to %s", async (_name, respond, expected) => {
    // Kills: failures surfaced as a successful or empty passport.
    vi.stubGlobal("fetch", vi.fn(respond));
    await expect(fetchCapabilityPassport(SCOPE)).resolves.toEqual({ kind: expected });
  });

  test("rejects a passport for another repository", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(capabilityPassportFixture({ scope: { installationId: 1, repositoryId: 2 } })),
      ),
    );
    await expect(fetchCapabilityPassport(SCOPE)).resolves.toEqual({ kind: "invalid-response" });
  });
});

describe("capability passport panel", () => {
  test("reads evidence only on explicit request and labels unknown webhook evidence", async () => {
    const fetch = vi.fn(async () => Response.json(capabilityPassportFixture()));
    vi.stubGlobal("fetch", fetch);
    render(<CapabilityPassportPanel authorityRevision={0} scope={SCOPE} />);

    expect(fetch).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Check capabilities" }));

    expect(await screen.findByText("No action derived")).toBeVisible();
    const webhook = screen.getByRole("row", { name: /Webhook observation/ });
    expect(within(webhook).getByText("unknown")).toBeVisible();
    expect(
      within(webhook).getByText("History delivery inbox (history-enabled repositories only)"),
    ).toBeVisible();
    expect(screen.getByRole("row", { name: /actions: read/ })).toBeVisible();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  test("renders denial as an explicit forbidden state, not an empty passport", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: "forbidden", ok: false }, { status: 403 })),
    );
    render(<CapabilityPassportPanel authorityRevision={0} scope={SCOPE} />);

    await userEvent.click(screen.getByRole("button", { name: "Check capabilities" }));

    expect(await screen.findByText("Repository access denied")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
  });

  test("a late response for a replaced repository is never shown", async () => {
    // Kills: a stale passport displayed after the selected repository changed.
    const pending = Promise.withResolvers<Response>();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => pending.promise),
    );
    const { rerender } = render(<CapabilityPassportPanel authorityRevision={0} scope={SCOPE} />);
    await userEvent.click(screen.getByRole("button", { name: "Check capabilities" }));
    rerender(
      <CapabilityPassportPanel
        authorityRevision={0}
        scope={{ installationId: 1, repositoryId: 2 }}
      />,
    );

    await act(async () => pending.resolve(Response.json(capabilityPassportFixture())));

    expect(screen.queryByText("No action derived")).toBeNull();
    expect(screen.getByRole("button", { name: "Check capabilities" })).toBeEnabled();
  });

  test("projects denial, limits and expired keys as distinct rows", () => {
    const rows = capabilityRows(
      capabilityPassportFixture({
        actionsOidcKeys: {
          ...capabilityPassportFixture().actionsOidcKeys,
          reason: "cache_expired",
          status: "expired",
        },
        installationGrant: {
          installation: null,
          observedAt: "2026-10-11T12:00:00Z",
          reason: "inventory_grant_absent",
          source: "github_app_installation",
          status: "denied",
        },
        nextAction: "request_access",
      }),
    );

    expect(rows.map((row) => [row.key, row.status])).toEqual([
      ["grant", "denied"],
      ["limits", "unknown"],
      ["webhook", "unknown"],
      ["reconciliation", "known"],
      ["oidc-keys", "expired"],
      ["admission-keys", "unknown"],
    ]);
  });
});
