import { afterEach, describe, expect, test, vi } from "vitest";
import { activateConfig } from "../src/api/configActivation/client";
import {
  fetchControlPlaneSession,
  logoutControlPlaneSession,
} from "../src/api/controlPlaneIdentity/client";
import { proxyRequestIsAdmitted } from "../src/api/development/proxyPolicy";
import { fetchGovernanceObservation } from "../src/api/governanceObservation/client";
import {
  fetchInstallationCatalog,
  fetchRepositoryCatalogPage,
} from "../src/api/providerInventory/client";
import { startRepositoryAttestation } from "../src/api/repositoryAttestation/client";
import { fetchWorkbenchSnapshot } from "../src/api/workbench/client";
import { fetchWorkflowDiscovery } from "../src/api/workflowDiscovery/client";
import {
  configActivationFixture,
  controlPlaneSessionFixture,
  governanceObservationFixture,
  installationCatalogFixture,
  repositoryFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "./fixture";

const scope = { installationId: 1, limit: 10, repositoryId: 1 } as const;
const activationCommand = {
  expectedRevision: null,
  operationId: "00000000-0000-4000-8000-000000000000",
  proposalManifestId: "proposal:c0169591134297170c6402e83fc6b67f",
  scope,
  targetEpochId: "4".repeat(64),
} as const;
const attestationCommand = {
  expectedActive: null,
  expectedManifestId: "proposal:c0169591134297170c6402e83fc6b67f",
  operationId: "00000000-0000-4000-8000-000000000000",
  scope,
} as const;
const csrfToken = "c".repeat(43);

afterEach(() => vi.unstubAllGlobals());

describe("control-plane identity, repository attestation, and activation clients", () => {
  test("admits a bounded Keycloak session and exact GitHub authorization URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(controlPlaneSessionFixture())),
    );

    await expect(fetchControlPlaneSession()).resolves.toMatchObject({
      kind: "authenticated",
      session: { user: { preferredUsername: "bart.simpson" } },
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          authorizationUrl: "https://github.com/login/oauth/authorize?client_id=reviewer",
          ok: true,
        }),
      ),
    );
    await expect(startRepositoryAttestation(attestationCommand, csrfToken)).resolves.toMatchObject({
      authorizationUrl: "https://github.com/login/oauth/authorize?client_id=reviewer",
      kind: "ready",
    });
  });

  test.each([
    [409, "already_reviewed", "already_reviewed"],
    [409, "baseline_conflict", "baseline_conflict"],
    [409, "blocked", "blocked"],
    [409, "epoch_conflict", "epoch_conflict"],
    [400, "invalid_callback", "invalid_callback"],
    [409, "operation_conflict", "operation_conflict"],
    [429, "rate_limited", "rate_limited"],
    [409, "replayed", "replayed"],
    [401, "unauthenticated", "unauthenticated"],
    [403, "forbidden", "forbidden"],
    [409, "stale", "stale"],
    [422, "diff_limit", "diff_limit"],
    [503, "overloaded", "overloaded"],
    [503, "unavailable", "unavailable"],
  ] as const)("maps attestation HTTP %s %s to %s", async (status, error, kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error, ok: false }, { status })),
    );

    await expect(startRepositoryAttestation(attestationCommand, csrfToken)).resolves.toMatchObject({
      kind,
    });
  });

  test("rejects a response whose HTTP status contradicts its attestation state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: "stale", ok: false })),
    );

    await expect(startRepositoryAttestation(attestationCommand, csrfToken)).resolves.toEqual({
      kind: "invalid-response",
    });
  });

  test.each([
    [401, { ok: false }, "anonymous"],
    [503, { error: "overloaded", ok: false }, "overloaded"],
    [503, { error: "unavailable", ok: false }, "unavailable"],
    [503, { error: "forbidden", ok: false }, "invalid-response"],
    [418, { ok: false }, "invalid-response"],
  ] as const)("maps session HTTP %s to %s", async (status, body, kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(body, { status })),
    );

    await expect(fetchControlPlaneSession()).resolves.toEqual({ kind });
  });

  test.each([
    [200, { ok: true, redirectUrl: "/workbench" }, "complete"],
    [200, { ok: true, redirectUrl: "https://apps.example.test/workbench" }, "complete"],
    [200, { ok: true, redirectUrl: "http://apps.example.test/workbench" }, "invalid-response"],
    [200, { ok: true, redirectUrl: "https://user:secret@apps.example.test" }, "invalid-response"],
    [200, { ok: true, redirectUrl: "not a URL" }, "invalid-response"],
    [401, { ok: false }, "anonymous"],
    [403, { ok: false }, "forbidden"],
    [503, { error: "overloaded", ok: false }, "overloaded"],
    [503, { error: "unavailable", ok: false }, "unavailable"],
    [503, { error: "forbidden", ok: false }, "invalid-response"],
    [418, { ok: false }, "invalid-response"],
  ] as const)("maps logout HTTP %s to %s", async (status, body, kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(body, { status })),
    );

    const result = await logoutControlPlaneSession(csrfToken);
    expect(result.kind).toBe(kind);
  });

  test("rejects a non-scalar operation identity before transport", async () => {
    const transport = vi.fn();
    vi.stubGlobal("fetch", transport);
    await expect(
      startRepositoryAttestation({ ...attestationCommand, operationId: "\ud800" }, csrfToken),
    ).resolves.toEqual({ kind: "invalid-response" });
    expect(transport).not.toHaveBeenCalled();
  });

  test("activates an exact reviewed epoch", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(configActivationFixture())),
    );

    await expect(activateConfig(activationCommand, csrfToken)).resolves.toMatchObject({
      kind: "complete",
      activation: { revision: 1 },
    });
  });

  test.each([
    [413, "invalid_config"],
    [401, "unauthenticated"],
    [403, "forbidden"],
    [404, "target_unavailable"],
    [409, "attestation_invalid"],
    [409, "conflict"],
    [409, "coverage_reducing"],
    [409, "coverage_unproven"],
    [409, "revision_conflict"],
    [503, "overloaded"],
    [503, "unavailable"],
  ] as const)("maps activation HTTP %s to %s", async (status, error) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ diagnostics: [], error, ok: false }, { status })),
    );

    await expect(activateConfig(activationCommand, csrfToken)).resolves.toEqual({ kind: error });
  });

  test("rejects malformed activation outcomes and invalid commands before transport", async () => {
    const transport = vi.fn(async () =>
      Response.json({ error: "unknown", ok: false }, { status: 409 }),
    );
    vi.stubGlobal("fetch", transport);

    await expect(activateConfig(activationCommand, csrfToken)).resolves.toEqual({
      kind: "invalid-response",
    });
    await expect(
      activateConfig({ ...activationCommand, expectedRevision: 0 }, csrfToken),
    ).resolves.toEqual({ kind: "invalid-response" });
    expect(transport).toHaveBeenCalledTimes(1);
  });

  test("keeps mutation transport failures distinct", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("offline"))),
    );

    await expect(activateConfig(activationCommand, csrfToken)).resolves.toEqual({
      kind: "network-failure",
    });
    await expect(startRepositoryAttestation(attestationCommand, csrfToken)).resolves.toEqual({
      kind: "network-failure",
    });
    await expect(fetchControlPlaneSession()).resolves.toEqual({ kind: "network-failure" });
    await expect(logoutControlPlaneSession(csrfToken)).resolves.toEqual({
      kind: "network-failure",
    });
  });

  test.each([
    ["activation", (signal: AbortSignal) => activateConfig(activationCommand, csrfToken, signal)],
    [
      "attestation",
      (signal: AbortSignal) => startRepositoryAttestation(attestationCommand, csrfToken, signal),
    ],
    ["session", (signal: AbortSignal) => fetchControlPlaneSession(signal)],
    ["logout", (signal: AbortSignal) => logoutControlPlaneSession(csrfToken, signal)],
  ] as const)("propagates caller cancellation for %s", async (_name, execute) => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (request: Request) =>
          new Promise<Response>((_resolve, reject) => {
            request.signal.addEventListener(
              "abort",
              () => reject(new DOMException("caller cancelled", "AbortError")),
              { once: true },
            );
          }),
      ),
    );

    const result = execute(controller.signal);
    controller.abort();

    await expect(result).rejects.toThrow("caller cancelled");
  });
});

describe("workbench client", () => {
  test.each([
    [401, "unauthenticated"],
    [403, "forbidden"],
    [503, "unavailable"],
  ] as const)("maps HTTP %s to %s", async (status, kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ok: false }, { status })),
    );
    await expect(fetchWorkbenchSnapshot(scope)).resolves.toEqual({ kind });
  });

  test("admits an exact scoped response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(workbenchFixture())),
    );
    const result = await fetchWorkbenchSnapshot(scope);
    expect(result.kind).toBe("ready");
  });

  test("rejects an unexpected HTTP status even with a valid snapshot", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(workbenchFixture(), { status: 500 })),
    );

    await expect(fetchWorkbenchSnapshot(scope)).resolves.toEqual({ kind: "invalid-response" });
  });

  test("rejects a cross-scope response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(workbenchFixture({ scope: { installationId: 1, repositoryId: 2 } })),
      ),
    );
    await expect(fetchWorkbenchSnapshot(scope)).resolves.toEqual({ kind: "invalid-response" });
  });

  test("rejects a response larger than the byte bound", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      cancel() {
        cancelled = true;
      },
      start(controller) {
        controller.enqueue(new TextEncoder().encode("{}"));
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(body, { headers: { "content-length": String(2 * 1024 * 1024 + 1) } }),
      ),
    );
    await expect(fetchWorkbenchSnapshot(scope)).resolves.toEqual({ kind: "invalid-response" });
    expect(cancelled).toBe(true);
  });

  test("cancels a streamed response as soon as its admitted byte bound is exceeded", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      cancel() {
        cancelled = true;
      },
      start(controller) {
        controller.enqueue(new Uint8Array(2 * 1024 * 1024 + 1));
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(body)),
    );

    await expect(fetchWorkbenchSnapshot(scope)).resolves.toEqual({ kind: "invalid-response" });
    expect(cancelled).toBe(true);
  });

  test("keeps transport failure distinct", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("offline"))),
    );
    await expect(fetchWorkbenchSnapshot(scope)).resolves.toEqual({ kind: "network-failure" });
  });

  test.each([Number.NaN, 0, 1.5, 2_147_483_648])(
    "maps invalid timeout %s without starting transport",
    async (timeoutMs) => {
      const transport = vi.fn();
      vi.stubGlobal("fetch", transport);

      await expect(fetchWorkbenchSnapshot(scope, undefined, timeoutMs)).resolves.toEqual({
        kind: "network-failure",
      });
      expect(transport).not.toHaveBeenCalled();
    },
  );

  test("bounds an unresolved transport", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (request: Request) =>
          new Promise<Response>((_resolve, reject) => {
            request.signal.addEventListener(
              "abort",
              () => reject(new DOMException("request timed out", "AbortError")),
              { once: true },
            );
          }),
      ),
    );

    await expect(fetchWorkbenchSnapshot(scope, undefined, 1)).resolves.toEqual({
      kind: "network-failure",
    });
  });

  test("propagates caller cancellation", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (request: Request) =>
          new Promise<Response>((_resolve, reject) => {
            request.signal.addEventListener(
              "abort",
              () => reject(new DOMException("caller cancelled", "AbortError")),
              { once: true },
            );
          }),
      ),
    );

    const result = fetchWorkbenchSnapshot(scope, controller.signal);
    controller.abort();

    await expect(result).rejects.toThrow("caller cancelled");
  });
});

describe("provider inventory client", () => {
  describe.each([
    ["installations", (signal?: AbortSignal) => fetchInstallationCatalog(signal)],
    ["repositories", (signal?: AbortSignal) => fetchRepositoryCatalogPage(1, 1, signal)],
  ] as const)("%s failure admission", (_name, execute) => {
    test.each([
      ["schema", 200, "{}"],
      ["syntax", 200, "{"],
      ["error envelope", 503, "{}"],
      [
        "status mismatch",
        403,
        JSON.stringify({ error: "unauthenticated", ok: false, retryAfterSeconds: null }),
      ],
    ] as const)("rejects invalid %s", async (_kind, status, body) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response(body, { status })),
      );
      await expect(execute()).resolves.toEqual({ kind: "invalid-response" });
    });

    test("returns transport failure without caller cancellation", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          throw new TypeError("connection closed");
        }),
      );
      await expect(execute()).resolves.toEqual({ kind: "network-failure" });
    });

    test("preserves the caller's cancellation outcome", async () => {
      const controller = new AbortController();
      const failure = new DOMException("caller cancelled", "AbortError");
      vi.stubGlobal(
        "fetch",
        vi.fn(
          (request: Request) =>
            new Promise<Response>((_resolve, reject) => {
              request.signal.addEventListener("abort", () => reject(failure), { once: true });
            }),
        ),
      );
      const pending = execute(controller.signal);
      controller.abort();
      await expect(pending).rejects.toBe(failure);
    });
  });

  test("admits installation and exact-scope repository projections", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(installationCatalogFixture()))
      .mockResolvedValueOnce(Response.json(repositoryPageFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchInstallationCatalog()).resolves.toMatchObject({ kind: "ready" });
    await expect(fetchRepositoryCatalogPage(1, 1)).resolves.toMatchObject({ kind: "ready" });
  });

  test("rejects a repository page for another installation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          repositoryPageFixture({
            installation: {
              ...repositoryPageFixture().installation,
              installationId: 2,
            },
            repositories: [
              {
                ...repositoryFixture(),
                scope: { installationId: 2, repositoryId: 1 },
              },
            ],
          }),
        ),
      ),
    );

    await expect(fetchRepositoryCatalogPage(1, 1)).resolves.toEqual({
      kind: "invalid-response",
    });
  });

  test.each([{ page: 1 }, { page: 2, perPage: 100 }])(
    "rejects an installation page outside the exact request %j",
    async (substitution) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => Response.json(installationCatalogFixture(substitution))),
      );
      await expect(fetchInstallationCatalog(undefined, 15_000, 2)).resolves.toEqual({
        kind: "invalid-response",
      });
    },
  );

  test("requests the canonical bounded organization page", async () => {
    const fetchMock = vi.fn(async (_request: Request) =>
      Response.json(installationCatalogFixture({ page: 2 })),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchInstallationCatalog(undefined, 15_000, 2)).resolves.toMatchObject({
      kind: "ready",
    });
    expect(fetchMock.mock.calls[0]?.[0].url).toContain("installations?page=2&perPage=30");
  });

  test.each([
    [401, "unauthenticated", "unauthenticated"],
    [403, "forbidden", "forbidden"],
    [404, "not_found", "not-found"],
    [409, "suspended", "suspended"],
    [429, "rate_limited", "rate-limited"],
    [503, "unavailable", "unavailable"],
  ] as const)("maps provider HTTP %s %s to %s", async (status, error, kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { error, ok: false, retryAfterSeconds: error === "rate_limited" ? 30 : null },
          { status },
        ),
      ),
    );

    await expect(fetchInstallationCatalog()).resolves.toMatchObject({ kind });
  });
});

describe("workflow discovery client", () => {
  test("admits a provenance-bound report at the current default head", async () => {
    const fetchMock = vi.fn(async (_request: Request) => Response.json(workflowDiscoveryFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchWorkflowDiscovery(scope)).resolves.toMatchObject({
      kind: "ready",
    });
    const [request] = fetchMock.mock.calls[0] ?? [];
    expect(request).toBeInstanceOf(Request);
    expect(request?.url).not.toContain("revision=");
  });

  test("rejects a reviewable proposal for an explicit historical revision", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(workflowDiscoveryFixture())),
    );

    await expect(fetchWorkflowDiscovery(scope, "a".repeat(40))).resolves.toEqual({
      kind: "invalid-response",
    });
  });

  test("rejects a proposal whose manifest identity does not bind its fields", async () => {
    const value = workflowDiscoveryFixture();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          ...value,
          proposal: { ...value.proposal, manifestId: `proposal:${"5".repeat(32)}` },
        }),
      ),
    );

    await expect(fetchWorkflowDiscovery(scope)).resolves.toEqual({ kind: "invalid-response" });
  });

  test("rejects invalid revisions before transport", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchWorkflowDiscovery(scope, "main")).resolves.toEqual({
      kind: "invalid-revision",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("rejects a cross-scope discovery report", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          workflowDiscoveryFixture({
            repository: {
              ...workflowDiscoveryFixture().repository,
              scope: { installationId: 1, repositoryId: 2 },
            },
          }),
        ),
      ),
    );

    await expect(fetchWorkflowDiscovery(scope)).resolves.toEqual({ kind: "invalid-response" });
  });

  test.each([
    [401, "unauthenticated", "unauthenticated"],
    [403, "forbidden", "forbidden"],
    [404, "not_found", "not-found"],
    [422, "invalid_revision", "invalid-revision"],
    [429, "rate_limited", "rate-limited"],
    [503, "source_tree_limit_exceeded", "unavailable"],
  ] as const)("maps discovery HTTP %s %s to %s", async (status, error, kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error, ok: false }, { status })),
    );

    await expect(fetchWorkflowDiscovery(scope)).resolves.toMatchObject({ kind });
  });
});

describe("governance observation client", () => {
  test("admits an exact-scope content-bound observation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(governanceObservationFixture())),
    );

    await expect(fetchGovernanceObservation(scope)).resolves.toMatchObject({
      kind: "ready",
      observation: {
        baselineState: "unbaselined",
        consistency: "best_effort",
      },
    });
  });

  test.each([
    [3 * 1024 * 1024, "ready"],
    [8 * 1024 * 1024 + 1, "invalid-response"],
  ] as const)("enforces the outer response bound at %s declared bytes", async (size, kind) => {
    const source = JSON.stringify(governanceObservationFixture());
    const body = size <= 8 * 1024 * 1024 ? source.padEnd(size, " ") : source;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(body, {
            headers: { "content-length": String(size) },
          }),
      ),
    );

    await expect(fetchGovernanceObservation(scope)).resolves.toMatchObject({ kind });
  });

  test.each([
    [
      "another repository scope",
      governanceObservationFixture({
        repository: {
          ...governanceObservationFixture().repository,
          scope: { installationId: 1, repositoryId: 2 },
        },
      }),
    ],
    ["a false state digest", governanceObservationFixture({ stateDigest: "f".repeat(64) })],
  ])("rejects %s", async (_label, response) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(response)),
    );

    await expect(fetchGovernanceObservation(scope)).resolves.toEqual({
      kind: "invalid-response",
    });
  });

  test.each([
    [401, "unauthenticated", "unauthenticated"],
    [403, "forbidden", "forbidden"],
    [404, "not_found", "not-found"],
    [429, "rate_limited", "rate-limited"],
    [503, "unavailable", "unavailable"],
    [503, "malformed_provider_response", "unavailable"],
    [503, "provider_binding_mismatch", "unavailable"],
    [503, "observation_limit_exceeded", "unavailable"],
  ] as const)("maps governance HTTP %s %s to %s", async (status, error, kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { error, ok: false, retryAfterSeconds: error === "rate_limited" ? 30 : null },
          { status },
        ),
      ),
    );

    await expect(fetchGovernanceObservation(scope)).resolves.toMatchObject({ kind });
  });
});

describe("development proxy authority", () => {
  test.each([
    ["GET", "/api/v1/auth/keycloak/start", true],
    ["GET", "/api/v1/auth/session", true],
    ["POST", "/api/v1/auth/keycloak/logout", true],
    ["GET", "/api/v1/auth/keycloak/logout", false],
    ["POST", "/api/v1/auth/keycloak/backchannel-logout", true],
    ["GET", `/api/v1/auth/keycloak/callback?code=provider&state=${"s".repeat(43)}`, true],
    ["GET", "/api/v1/auth/keycloak/callback?code=provider", false],
    ["GET", "/api/v1/auth/keycloak/callback?code=a&code=b&state=c", false],
    ["POST", "/api/v1/repository-attestations/github/start", true],
    [
      "GET",
      `/api/v1/repository-attestations/github/callback?code=provider&state=${"s".repeat(43)}`,
      true,
    ],
    ["POST", "/api/v1/config/activations", true],
    ["GET", "/api/v1/config/activations", false],
    ["GET", "/api/v1/workbench/installations", true],
    ["GET", "/api/v1/workbench/installations?page=2&perPage=30", true],
    ["GET", "/api/v1/workbench/installations?page=10001", false],
    ["GET", "/api/v1/workbench/installations?page=0", false],
    ["GET", "/api/v1/workbench/installations?perPage=101", false],
    ["GET", "/api/v1/workbench/installations?page=1&page=2", false],
    ["GET", "/api/v1/workbench/installations?unknown=true", false],
    ["GET", "/api/v1/workbench/installations?unknown=1", false],
    ["GET", "/api/v1/workbench/installations/1/repositories?page=1&perPage=100", true],
    ["GET", "/api/v1/workbench/installations/1/repositories?page=0&perPage=100", false],
    ["GET", "/api/v1/workbench/installations/1/repositories?page=1e2&perPage=100", false],
    ["GET", "/api/v1/workbench/installations/1/repositories?page=01&perPage=100", false],
    ["GET", "/api/v1/workbench/installations/1/repositories?page=1&perPage=101", false],
    ["GET", "/api/v1/workbench/repositories/1/1?limit=20", true],
    ["GET", "/api/v1/workbench/repositories/1/1?limit=21", false],
    ["GET", "/api/v1/workbench/repositories/1/1?limit=1e1", false],
    ["GET", "/api/v1/workbench/repositories/1/1?limit=10&limit=10", false],
    ["GET", "/api/v1/workbench/repositories/1/1/workflow-discovery", true],
    ["GET", "/api/v1/workbench/repositories/1/1/governance-observation", true],
    ["GET", "/api/v1/workbench/repositories/1/1/governance-observation?x=1", false],
    ["POST", "/api/v1/workbench/repositories/1/1/governance-observation", false],
    ["GET", "/api/v1/workbench/repositories/1/1/governance-baselines", true],
    ["POST", "/api/v1/workbench/repositories/1/1/governance-baselines", true],
    ["GET", "/api/v1/workbench/repositories/1/1/governance-baselines?x=1", false],
    ["GET", "/api/v1/workbench/repositories/1/1/governance-comparison", true],
    ["POST", "/api/v1/workbench/repositories/1/1/governance-comparison", false],
    ["GET", "/api/v1/workbench/repositories/1/1/governance-comparison?x=1", false],
    [
      "GET",
      `/api/v1/workbench/repositories/1/1/workflow-discovery?revision=${"a".repeat(40)}`,
      true,
    ],
    ["GET", "/api/v1/workbench/repositories/1/1/workflow-discovery?revision=main", false],
    [
      "GET",
      `/api/v1/workbench/repositories/1/1/workflow-discovery?revision=${"a".repeat(40)}&x=1`,
      false,
    ],
    ["POST", "/api/v1/workbench/repositories/1/1?limit=10", false],
    ["GET", "/api/v1/operator/overrides", false],
  ] as const)("admits only the bounded operator UI route", (method, url, expected) => {
    expect(proxyRequestIsAdmitted(method, url)).toBe(expected);
  });
});
