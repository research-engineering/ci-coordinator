import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, useEffect } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { isSessionChallenge } from "../src/api/controlPlaneIdentity/client";
import { boundedFetch } from "../src/api/shared/boundedFetch";
import { captureResponseObservers, observeResponses } from "../src/api/shared/responseObservation";
import { ControlPlaneIdentityControl } from "../src/features/auth/ControlPlaneIdentityControl";
import { useControlPlaneSession } from "../src/features/auth/useControlPlaneSession";
import { controlPlaneSessionFixture } from "./fixture";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function request(path = "/api/v2/economics/sources", init?: RequestInit) {
  return new Request(new URL(path, location.origin), { credentials: "same-origin", ...init });
}

test.each([
  ["/api/v1/workbench/installations", 401, {}, true],
  ["/api/v2/economics/sources", 401, {}, true],
  ["/api/v2/economics/sources", 403, {}, false],
  ["/api/v2/economics/sources", 503, {}, false],
  ["/api/v1/auth/session", 401, {}, false],
  ["/api/v1/auth/keycloak/logout", 401, {}, false],
  ["/workbench", 401, {}, false],
  ["https://foreign.example/api/v1/data", 401, {}, false],
  ["/api/v1/data", 401, { credentials: "omit" }, false],
  ["/api/v1/data", 401, { headers: { authorization: "Bearer fixture" } }, false],
  ["/api/v1/data", 401, { signal: AbortSignal.abort() }, false],
] as const)("challenge classification: %s %s %j", (path, status, init, expected) => {
  expect(isSessionChallenge(request(path, init), status)).toBe(expected);
});

test("a removed subscription cannot receive an old response even after resubscribing the same callback", () => {
  const callback = vi.fn();
  const stop = observeResponses(callback);
  const old = captureResponseObservers(request());
  stop();
  const stopNew = observeResponses(callback);
  try {
    old(401);
    expect(callback).not.toHaveBeenCalled();
    captureResponseObservers(request())(401);
    expect(callback).toHaveBeenCalledOnce();
  } finally {
    stopNew();
  }
});

test("bounded transport notifies once without changing the body, and isolates observer failures", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ok: false }, { status: 401 })),
  );
  const failed = observeResponses(() => {
    throw new Error("observer failed");
  });
  const callback = vi.fn();
  const stop = observeResponses(callback);
  try {
    const response = await boundedFetch(request());
    expect(await response.json()).toEqual({ ok: false });
    expect(response.status).toBe(401);
    expect(callback).toHaveBeenCalledOnce();
    const controller = new AbortController();
    const notify = captureResponseObservers(request(undefined, { signal: controller.signal }));
    controller.abort();
    notify(401);
    expect(callback).toHaveBeenCalledOnce();
  } finally {
    failed();
    stop();
  }
});

test.each(["renewed", "anonymous", "unavailable", "invalid", "network"] as const)(
  "concurrent cookie challenges coalesce into one fresh check: %s",
  async (outcome) => {
    const deferred = Promise.withResolvers<Response>();
    const checks: Request[] = [];
    const commands: Request[] = [];
    let initial = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request) => {
        if (new URL(input.url).pathname === "/api/v1/auth/session") {
          checks.push(input);
          if (initial) return Response.json(controlPlaneSessionFixture());
          if (outcome === "network") throw new TypeError("offline");
          return deferred.promise.then((response) => response.clone());
        }
        commands.push(input);
        return Response.json({ ok: false, error: "unauthenticated" }, { status: 401 });
      }),
    );
    const { result } = renderHook(useControlPlaneSession, { wrapper: StrictMode });
    await waitFor(() =>
      expect(result.current.state).toMatchObject({ result: { kind: "authenticated" } }),
    );
    const baseline = checks.length;
    initial = false;
    const oldRevision = result.current.authorityRevision;
    await act(async () => {
      await Promise.all([
        boundedFetch(request(undefined, { method: "POST", body: "{}" })),
        boundedFetch(request()),
      ]);
    });
    expect(checks).toHaveLength(baseline + 1);
    expect(result.current.authorityRevision).toBe(oldRevision + 1);
    await act(async () => {
      const response =
        outcome === "renewed"
          ? Response.json(controlPlaneSessionFixture())
          : outcome === "anonymous"
            ? Response.json({ ok: false }, { status: 401 })
            : outcome === "unavailable"
              ? Response.json({ ok: false, error: "unavailable" }, { status: 503 })
              : Response.json({});
      deferred.resolve(response);
    });
    const expected = {
      renewed: "authenticated",
      anonymous: "anonymous",
      unavailable: "unavailable",
      invalid: "invalid-response",
      network: "network-failure",
    }[outcome];
    await waitFor(() => expect(result.current.state).toMatchObject({ result: { kind: expected } }));
    expect(result.current.recoveryRequired).toBe(outcome === "anonymous");
    expect(commands.map((c) => c.method)).toEqual(["POST", "GET"]);
    expect(checks.every((c) => c.cache === "no-store")).toBe(true);
    if (outcome === "renewed") {
      const revision = result.current.authorityRevision;
      await act(async () => {
        await boundedFetch(request());
      });
      expect(checks).toHaveLength(baseline + 1);
      expect(result.current.authorityRevision).toBe(revision + 1);
      expect(result.current.state.kind).toBe("challenged");
      expect(result.current.recoveryRequired).toBe(false);
      await act(async () => result.current.refresh());
      await waitFor(() =>
        expect(result.current.state).toMatchObject({ result: { kind: "authenticated" } }),
      );
      expect(checks).toHaveLength(baseline + 2);
    }
  },
);

function MountedRead() {
  useEffect(() => {
    const controller = new AbortController();
    void boundedFetch(request(undefined, { signal: controller.signal })).catch(() => undefined);
    return () => controller.abort();
  }, []);
  return <input aria-label="Private draft" defaultValue="" />;
}

function SessionShell({ readOnMount = false }: { readonly readOnMount?: boolean }) {
  const identity = useControlPlaneSession();
  return (
    <>
      <ControlPlaneIdentityControl identity={identity} returnTo="/workbench" />
      {identity.state.kind === "settled" && identity.state.result.kind === "authenticated" ? (
        <div key={identity.authorityRevision}>
          {readOnMount ? <MountedRead /> : <input aria-label="Private draft" defaultValue="" />}
        </div>
      ) : null}
    </>
  );
}

test("the first descendant passive request has a current recovery subscription", async () => {
  let challenged = false;
  let recoveryChecks = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      if (new URL(input.url).pathname === "/api/v1/auth/session") {
        if (!challenged) return Response.json(controlPlaneSessionFixture());
        recoveryChecks += 1;
      } else challenged = true;
      return Response.json({ ok: false }, { status: 401 });
    }),
  );
  render(
    <StrictMode>
      <SessionShell readOnMount />
    </StrictMode>,
  );
  expect(await screen.findByRole("link", { name: "Sign in" })).toBeVisible();
  expect(recoveryChecks).toBe(1);
  expect(screen.queryByRole("textbox", { name: "Private draft" })).not.toBeInTheDocument();
});

test("a repeated current challenge discards mounted drafts and permits only an explicit recheck", async () => {
  let checks = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      if (new URL(input.url).pathname === "/api/v1/auth/session") {
        checks += 1;
        return Response.json(controlPlaneSessionFixture());
      }
      return Response.json({ ok: false }, { status: 401 });
    }),
  );
  const user = userEvent.setup();
  render(<SessionShell />);
  await user.type(await screen.findByRole("textbox", { name: "Private draft" }), "old draft");
  await act(async () => {
    await boundedFetch(request());
  });
  expect(await screen.findByRole("textbox", { name: "Private draft" })).toHaveValue("");
  await user.type(screen.getByRole("textbox", { name: "Private draft" }), "new draft");
  await act(async () => {
    await boundedFetch(request());
  });
  expect(screen.queryByRole("textbox", { name: "Private draft" })).not.toBeInTheDocument();
  expect(checks).toBe(2);
  await user.click(screen.getByRole("button", { name: "Check session" }));
  expect(await screen.findByRole("textbox", { name: "Private draft" })).toHaveValue("");
  expect(checks).toBe(3);
});

test.each([
  [403, "forbidden", "Sign-out request was rejected"],
  [503, "overloaded", "Sign-out service is busy"],
  [503, "unavailable", "Sign-out service unavailable"],
  [200, "malformed", "Sign-out response rejected"],
  [0, "network", "Sign-out request failed"],
] as const)(
  "mounted sign-out preserves its own failure outcome: %s %s",
  async (status, error, message) => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request) => {
        const path = new URL(input.url).pathname;
        calls.push(path);
        if (path.endsWith("/session")) return Response.json(controlPlaneSessionFixture());
        if (status === 0) throw new TypeError("offline");
        return Response.json({ ok: false, error }, { status });
      }),
    );
    render(<SessionShell />);
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.getByText("Remote sign-out is not confirmed.")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
    expect(calls).toEqual(["/api/v1/auth/session", "/api/v1/auth/keycloak/logout"]);
    await userEvent.click(screen.getByRole("button", { name: "Check current session" }));
    expect(await screen.findByRole("button", { name: "Sign out" })).toBeVisible();
  },
);

test("expired cookie is rechecked and an already-expired 200 cannot create a retry loop", async () => {
  const now = Date.now();
  const clock = vi.spyOn(Date, "now").mockReturnValue(now);
  const timers = vi.spyOn(globalThis, "setTimeout");
  const fetch = vi.fn(async () =>
    Response.json(controlPlaneSessionFixture({ expiresAt: new Date(now + 60_000).toISOString() })),
  );
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(useControlPlaneSession);
  await waitFor(() =>
    expect(result.current.state).toMatchObject({ result: { kind: "authenticated" } }),
  );
  const expiry = timers.mock.calls.findLast(([, delay]) => delay === 60_000)?.[0];
  if (typeof expiry !== "function") throw new Error("expiry timer missing");
  clock.mockReturnValue(now + 60_000);
  await act(async () => expiry());
  expect(result.current.state).toMatchObject({ result: { kind: "invalid-response" } });
  expect(result.current.recoveryRequired).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("visibility resumes expiry checks and unmount rejects a late session response", async () => {
  const now = Date.now();
  const clock = vi.spyOn(Date, "now").mockReturnValue(now);
  const deferred = Promise.withResolvers<Response>();
  const checks: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      checks.push(input);
      return checks.length === 1
        ? Response.json(
            controlPlaneSessionFixture({ expiresAt: new Date(now + 60_000).toISOString() }),
          )
        : deferred.promise;
    }),
  );
  const { result, unmount } = renderHook(useControlPlaneSession);
  await waitFor(() =>
    expect(result.current.state).toMatchObject({ result: { kind: "authenticated" } }),
  );
  clock.mockReturnValue(now + 60_000);
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(checks).toHaveLength(2);
  unmount();
  expect(checks[1]?.signal.aborted).toBe(true);
  await act(async () => deferred.resolve(Response.json(controlPlaneSessionFixture())));
  expect(result.current.state.kind).toBe("loading");
});

test.each(["anonymous", "complete", "failure"] as const)(
  "logout fences a stale check and suppresses recovery: %s",
  async (outcome) => {
    const checks: Request[] = [];
    const stale = Promise.withResolvers<Response>();
    const logout = Promise.withResolvers<Response>();
    let initial = true;
    vi.stubGlobal("location", { origin: location.origin, assign: vi.fn() });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request) => {
        if (new URL(input.url).pathname.endsWith("/logout")) return logout.promise;
        checks.push(input);
        return initial ? Response.json(controlPlaneSessionFixture()) : stale.promise;
      }),
    );
    const { result } = renderHook(useControlPlaneSession);
    await waitFor(() =>
      expect(result.current.state).toMatchObject({ result: { kind: "authenticated" } }),
    );
    const oldLogout = result.current.logout;
    initial = false;
    act(() => {
      result.current.refresh();
      oldLogout();
    });
    expect(checks[1]?.signal.aborted).toBe(true);
    expect(result.current.loggingOut).toBe(true);
    act(() => result.current.refresh());
    expect(checks).toHaveLength(2);
    await act(async () => {
      stale.resolve(Response.json(controlPlaneSessionFixture()));
      logout.resolve(
        outcome === "complete"
          ? Response.json({ ok: true, redirectUrl: "/workbench" })
          : outcome === "anonymous"
            ? Response.json({}, { status: 401 })
            : Response.json({}, { status: 403 }),
      );
    });
    expect(result.current.state).toMatchObject({
      result: { kind: outcome === "failure" ? "invalid-response" : "anonymous" },
    });
    expect(result.current.recoveryRequired).toBe(false);
    expect(location.assign).toHaveBeenCalledTimes(outcome === "complete" ? 1 : 0);
  },
);
