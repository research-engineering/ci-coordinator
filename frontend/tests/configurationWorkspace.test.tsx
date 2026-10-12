import { webcrypto } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, onTestFinished, test, vi } from "vitest";
import type { ControlPlaneSession } from "../src/api/controlPlaneIdentity/schema";
import { ConfigurationPage } from "../src/features/configuration/ConfigurationPage";
import { consoleHref, readConsoleRoute } from "../src/features/workbench/navigation";
import {
  configEpoch,
  configJson,
  configPreview,
  configStatus,
  configurationSource,
  configValidation,
  sourceResponse,
} from "./configurationFixture";
import { RepositoryWorkspace } from "./consoleSelectionHarness";
import { controlPlaneSessionFixture, workbenchFixture } from "./fixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
beforeEach(() => vi.stubGlobal("crypto", webcrypto));

const props = () => ({
  scope: { installationId: 1, repositoryId: 1, limit: 10 },
  session: controlPlaneSessionFixture({ roles: ["activate", "configure", "read"] }),
  authorityRevision: 1,
  active: true,
  onWorkflows: undefined,
});

async function enterSource() {
  await userEvent.selectOptions(screen.getByLabelText("Source format"), "json");
  fireEvent.change(screen.getByRole("textbox", { name: "Source" }), {
    target: { value: configurationSource },
  });
  await userEvent.click(screen.getByRole("button", { name: "Validate source" }));
  await screen.findByText("Source validated for repository 1.");
}

async function reviewRegistration() {
  await enterSource();
  await userEvent.click(screen.getByRole("button", { name: "Review registration" }));
}

async function selectRollback() {
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await userEvent.click(
    await screen.findByRole("button", {
      name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}`,
    }),
  );
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Restore prior source" } });
  await userEvent.click(screen.getByRole("button", { name: "Review rollback" }));
}

function routeConfiguration(mutate: (request: Request) => Promise<Response>) {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const path = new URL(request.url).pathname;
      if (path.endsWith("/validations")) return configJson(configValidation());
      if (path.endsWith("/status")) return configJson(configStatus());
      if (path === "/api/v1/config/epochs" || path.endsWith("/rollbacks")) return mutate(request);
      return Response.json(workbenchFixture());
    }),
  );
  return requests;
}

test("source edits invalidate validation and preview; no registration or activation without confirmation", async () => {
  const mutation = vi.fn(async () => configJson({}));
  routeConfiguration(mutation);
  render(<ConfigurationPage {...props()} />);
  expect(screen.getByRole("button", { name: "Review registration" })).toBeDisabled();
  await reviewRegistration();
  expect(screen.getByRole("button", { name: "Confirm registration" })).toHaveFocus();
  expect(mutation).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: "Source" }), {
    target: { value: `${configurationSource}\n` },
  });
  expect(screen.queryByRole("button", { name: "Confirm registration" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review registration" })).toBeDisabled();
  expect(mutation).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /Activate/ })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Review workflow proposal" })).toHaveAttribute(
    "href",
    "/workbench?installationId=1&repositoryId=1&limit=10&view=workflows",
  );
});

test("registration uncertain command survives tabs and repository sidebar navigation without replay", async () => {
  const commands: unknown[] = [];
  const requests = routeConfiguration(async (request) => {
    commands.push(await request.json());
    if (commands.length === 1) throw new TypeError("lost response");
    return configJson({
      schemaVersion: "ci-config-epoch-registration-result/v1",
      ok: true,
      epochId: configEpoch().epochId,
      duplicate: true,
    });
  });
  const base = { ...props(), tab: "plans" as const, onTab: vi.fn(), onWorkflows: vi.fn() };
  const view = render(<RepositoryWorkspace {...base} view="configuration" />);
  await reviewRegistration();
  await userEvent.click(screen.getByRole("button", { name: "Confirm registration" }));
  await screen.findByText(/Registration outcome unknown/);
  expect(screen.getByRole("textbox", { name: "Source" })).toBeDisabled();
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  expect(commands).toHaveLength(1);
  view.rerender(<RepositoryWorkspace {...base} view="repositories" />);
  const count = requests.length;
  await act(async () => {});
  expect(requests).toHaveLength(count);
  view.rerender(<RepositoryWorkspace {...base} view="configuration" />);
  expect(commands).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Retry same command" }));
  await screen.findByText(/Source registration confirmed/);
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
  expect(commands[0]).toEqual({
    schemaVersion: "ci-config-epoch-registration/v1",
    source: configurationSource,
    sourceFormat: "json",
    operationId: expect.any(String),
  });
  expect(
    requests.filter((request) => new URL(request.url).pathname.endsWith("/activations")),
  ).toHaveLength(0);
});

test("rollback preview makes no mutation; same command is preserved after response loss and a failed replay", async () => {
  const commands: unknown[] = [];
  routeConfiguration(async (request) => {
    commands.push(await request.json());
    if (commands.length === 1) throw new TypeError("lost rollback response");
    if (commands.length === 2)
      return configJson({ ok: false, error: "revision_conflict", diagnostics: [] }, 409);
    return configJson({
      schemaVersion: "ci-config-epoch-activation-result/v1",
      ok: true,
      epochId: configEpoch().epochId,
      duplicate: true,
      revision: 5,
    });
  });
  const base = props();
  const view = render(<ConfigurationPage {...base} />);
  await selectRollback();
  expect(commands).toHaveLength(0);
  expect(screen.getByRole("button", { name: "Confirm rollback" })).toHaveFocus();
  expect(
    within(screen.getByRole("region", { name: "Rollback" })).getByText(
      configStatus().active?.epochId ?? "missing",
    ),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Confirm rollback" }));
  await screen.findByText(/Rollback outcome unknown/);
  await userEvent.click(screen.getByRole("tab", { name: "Source" }));
  view.rerender(<ConfigurationPage {...base} active={false} />);
  expect(commands).toHaveLength(1);
  view.rerender(<ConfigurationPage {...base} />);
  await userEvent.click(screen.getByRole("button", { name: "Retry same command" }));
  await screen.findByText(/Rollback outcome unknown/);
  await userEvent.click(screen.getByRole("button", { name: "Retry same command" }));
  await screen.findByText("Rollback confirmed at revision 5.");
  expect(commands).toHaveLength(3);
  expect(commands[1]).toEqual(commands[0]);
  expect(commands[2]).toEqual(commands[0]);
  expect(commands[0]).toMatchObject({
    targetEpochId: configEpoch().epochId,
    expectedRevision: 4,
    reason: "Restore prior source",
    operationId: expect.any(String),
  });
});

const definitiveRollbackRejections = [
  "revision_conflict",
  "coverage_reducing",
  "coverage_unproven",
  "target_never_activated",
] as const;

test.each(definitiveRollbackRejections)(
  "rollback %s requires fresh review rather than automatic retry",
  async (error) => {
    const commands: unknown[] = [];
    routeConfiguration(async (request) => {
      commands.push(await request.json());
      return configJson({ ok: false, error, diagnostics: [] }, 409);
    });
    render(<ConfigurationPage {...props()} />);
    await selectRollback();
    await userEvent.click(screen.getByRole("button", { name: "Confirm rollback" }));
    await screen.findByRole("alert");
    expect(commands).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Confirm rollback" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry same command" })).not.toBeInTheDocument();
  },
);

test.each(["authority", "scope", "actor", "csrf", "roles"])(
  "%s change clears foreign source and uncertain command",
  async (change) => {
    const mutation = vi.fn(async () => {
      throw new TypeError("lost response");
    });
    routeConfiguration(mutation);
    const base = props();
    const view = render(<ConfigurationPage {...base} />);
    await reviewRegistration();
    await userEvent.click(screen.getByRole("button", { name: "Confirm registration" }));
    await screen.findByText(/Registration outcome unknown/);
    const session: ControlPlaneSession = {
      ...base.session,
      ...(change === "actor"
        ? { user: { ...base.session.user, actorId: `keycloak-human:v1:${"b".repeat(64)}` } }
        : {}),
      ...(change === "csrf" ? { csrfToken: "x".repeat(43) } : {}),
      ...(change === "roles" ? { roles: ["configure"] } : {}),
    };
    view.rerender(
      <ConfigurationPage
        {...base}
        authorityRevision={change === "authority" ? 2 : 1}
        scope={change === "scope" ? { ...base.scope, repositoryId: 2 } : base.scope}
        session={session}
      />,
    );
    expect(screen.queryByRole("button", { name: "Retry same command" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Source" })).toHaveValue("");
    expect(mutation).toHaveBeenCalledTimes(1);
  },
);

test("denied and already expired sessions cannot read or write configuration", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const base = props();
  const view = render(
    <ConfigurationPage {...base} session={{ ...base.session, roles: ["audit"] }} />,
  );
  expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  view.rerender(
    <ConfigurationPage
      {...base}
      session={{ ...base.session, expiresAt: "2000-01-01T00:00:00Z" }}
    />,
  );
  expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

test("in-place expiry cancels obsolete validation and removes its source", async () => {
  vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
  let finish: ((value: Response) => void) | undefined;
  let request: Request | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((value: Request) => {
      request = value;
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }),
  );
  const base = props();
  const expiry = Date.now() + 60_000;
  render(
    <ConfigurationPage
      {...base}
      session={{ ...base.session, expiresAt: new Date(expiry).toISOString() }}
    />,
  );
  fireEvent.change(screen.getByLabelText("Source format"), { target: { value: "json" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Source" }), {
    target: { value: configurationSource },
  });
  fireEvent.click(screen.getByRole("button", { name: "Validate source" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_001);
  });
  await act(async () => {
    finish?.(configJson(configValidation()));
  });
  expect(screen.queryByRole("button", { name: "Confirm registration" })).not.toBeInTheDocument();
  expect(request).toBeDefined();
  expect(request?.signal.aborted).toBe(true);
  expect(screen.queryByLabelText("Source", { exact: true })).not.toBeInTheDocument();
});

test("hidden epochs abort reads, discard stale export, and do not publish downloads", async () => {
  let finish: ((value: Response) => void) | undefined;
  let downloadRequest: Request | undefined;
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      requests.push(request);
      if (new URL(request.url).pathname.endsWith("/source")) {
        downloadRequest = request;
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      }
      return Promise.resolve(configJson(configStatus()));
    }),
  );
  const createObjectURL = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static override createObjectURL = createObjectURL;
    },
  );
  const base = props();
  const view = render(<ConfigurationPage {...base} />);
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await userEvent.click(
    await screen.findByRole("button", {
      name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}`,
    }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Download source" }));
  await waitFor(() => expect(downloadRequest).toBeDefined());
  view.rerender(<ConfigurationPage {...base} active={false} />);
  expect(downloadRequest?.signal.aborted).toBe(true);
  const count = requests.length;
  await act(async () => {
    finish?.(sourceResponse());
  });
  expect(createObjectURL).not.toHaveBeenCalled();
  expect(requests).toHaveLength(count);
  view.rerender(<ConfigurationPage {...base} />);
  await waitFor(() => expect(requests.length).toBe(count + 1));
  expect(screen.queryByRole("button", { name: "Download source" })).not.toBeInTheDocument();
});

test("older active revisions and equal-revision identity drift fail closed", async () => {
  const status = configStatus();
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(configJson(status))
    .mockResolvedValueOnce(
      configJson({ ...status, active: { epochId: status.active?.epochId, revision: 3 } }),
    )
    .mockResolvedValueOnce(
      configJson({ ...status, active: { epochId: configEpoch().epochId, revision: 4 } }),
    );
  vi.stubGlobal("fetch", fetch);
  render(<ConfigurationPage {...props()} />);
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await screen.findByText("Active revision 4");
  await userEvent.click(screen.getByRole("button", { name: "Refresh epochs" }));
  await screen.findByText("Response identity or metadata could not be verified.");
  await userEvent.click(screen.getByRole("button", { name: "Refresh epochs" }));
  await screen.findByText("Response identity or metadata could not be verified.");
  expect(screen.queryByRole("button", { name: "Review rollback" })).not.toBeInTheDocument();
});

test("configuration navigation and keyboard tabs retain scope without granting proposal authority", async () => {
  const route = readConsoleRoute("?installationId=1&repositoryId=1&limit=10&view=configuration");
  expect(consoleHref(route)).toBe(
    "/workbench?installationId=1&repositoryId=1&limit=10&view=configuration",
  );
  routeConfiguration(async () => configJson({}));
  render(<ConfigurationPage {...props()} />);
  const sourceTab = screen.getByRole("tab", { name: "Source" });
  sourceTab.focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Retained epochs" })).toHaveFocus();
  await userEvent.keyboard("{Home}");
  expect(sourceTab).toHaveFocus();
  expect(sourceTab).toHaveAttribute("aria-selected", "true");
});

const activeEpochId = configEpoch(`${configurationSource}\n`).epochId;

function routeLifecycle(
  status = configStatus(),
  mutate: (request: Request) => Promise<Response> = async () => configJson({}),
) {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const path = new URL(request.url).pathname;
      if (path.endsWith("/status")) return configJson(status);
      if (path.endsWith("/preview")) return configJson(configPreview());
      if (request.method === "POST") return mutate(request);
      return Response.json(workbenchFixture());
    }),
  );
  return requests;
}

async function inspectTarget() {
  await userEvent.click(screen.getByRole("tab", { name: "Retained epochs" }));
  await userEvent.click(
    await screen.findByRole("button", {
      name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}`,
    }),
  );
}

test("read-only session lists retained epochs without source, export, review or command authority", async () => {
  const requests = routeLifecycle();
  const base = props();
  render(<ConfigurationPage {...base} session={{ ...base.session, roles: ["read"] }} />);
  expect(screen.getByText("Configure role required to validate or register source.")).toBeVisible();
  await inspectTarget();
  expect(screen.queryByRole("button", { name: "Download source" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Request GitHub review" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Reason")).not.toBeInTheDocument();
  expect(screen.getByText("Activate role required for rollback.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Compare with active epoch" }));
  await screen.findByText("/repository/rules");
  expect(requests.map((request) => request.method)).not.toContain("POST");
});

test("rollback needs only the activate role the API admits, not an extra UI-only configure role", async () => {
  const commands: unknown[] = [];
  routeLifecycle(configStatus(), async (request) => {
    commands.push(await request.json());
    return configJson({
      schemaVersion: "ci-config-epoch-activation-result/v1",
      ok: true,
      epochId: configEpoch().epochId,
      duplicate: false,
      revision: 5,
    });
  });
  const base = props();
  render(
    <ConfigurationPage {...base} session={{ ...base.session, roles: ["activate", "read"] }} />,
  );
  await inspectTarget();
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Restore prior source" } });
  await userEvent.click(screen.getByRole("button", { name: "Review rollback" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm rollback" }));
  await screen.findByText("Rollback confirmed at revision 5.");
  expect(commands).toHaveLength(1);
});

test("a never-activated epoch offers comparison and review but no rollback target", async () => {
  const status = configStatus();
  const requests = routeLifecycle({
    ...status,
    epochs: status.epochs.map((epoch) =>
      epoch.epochId === configEpoch().epochId ? { ...epoch, previouslyActivated: false } : epoch,
    ),
  });
  render(<ConfigurationPage {...props()} />);
  await inspectTarget();
  const row = screen.getByRole("button", {
    name: `Inspect epoch ${configEpoch().epochId.slice(0, 12)}`,
  });
  expect(within(row.closest("tr") as HTMLElement).getByText("No")).toBeInTheDocument();
  expect(screen.queryByLabelText("Reason")).not.toBeInTheDocument();
  expect(
    screen.getByText(
      "Rollback is offered only for an epoch previously activated in this repository.",
    ),
  ).toBeInTheDocument();
  expect(requests.some((request) => request.url.includes("/preview"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Compare with active epoch" }));
  const preview = await screen.findByRole("region", { name: "Change against the active epoch" });
  expect(within(preview).getByText("Equal validation coverage")).toBeInTheDocument();
  expect(within(preview).getByText("/repository/rules")).toBeInTheDocument();
  const url = new URL(requests.find((request) => request.url.includes("/preview"))?.url ?? "");
  expect(url.pathname).toBe(
    `/api/v1/config/repositories/1/1/epochs/${configEpoch().epochId}/preview`,
  );
  expect(url.searchParams.get("baseEpochId")).toBe(activeEpochId);
});

test("review request posts the exact registered-epoch subject and hands control to GitHub", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { origin: globalThis.location.origin, search: "", assign });
  const bodies: unknown[] = [];
  routeLifecycle(configStatus(), async (request) => {
    bodies.push(await request.json());
    return Response.json({
      ok: true,
      authorizationUrl: "https://github.com/login/oauth/authorize?state=one",
    });
  });
  render(<ConfigurationPage {...props()} />);
  await inspectTarget();
  await userEvent.click(screen.getByRole("button", { name: "Request GitHub review" }));
  await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
  expect(assign).toHaveBeenCalledWith("https://github.com/login/oauth/authorize?state=one");
  expect(bodies).toEqual([
    {
      schemaVersion: "ci-registered-epoch-attestation-start/v1",
      installationId: 1,
      repositoryId: 1,
      expectedActive: { epochId: activeEpochId, revision: 4 },
      targetEpochId: configEpoch().epochId,
      expectedSourceHash: configEpoch().sourceHash,
      operationId: expect.any(String),
    },
  ]);
});

test("returned review shows the diff before v3 activation and retains an uncertain activation", async () => {
  const target = configEpoch();
  const search = `?installationId=1&repositoryId=1&limit=10&repositoryAttestation=registered-epoch-reviewed&targetEpochId=${target.epochId}&expectedSourceHash=${target.sourceHash}&reviewOperationId=review-one&reviewBaseEpochId=${activeEpochId}&reviewBaseRevision=4`;
  vi.stubGlobal("location", { origin: globalThis.location.origin, search, assign: vi.fn() });
  const commands: unknown[] = [];
  const requests = routeLifecycle(configStatus(), async (request) => {
    commands.push(await request.json());
    if (commands.length === 1) throw new TypeError("lost activation response");
    return configJson({
      schemaVersion: "ci-config-epoch-activation-result/v1",
      ok: true,
      epochId: target.epochId,
      duplicate: true,
      revision: 5,
    });
  });
  render(<ConfigurationPage {...props()} />);
  const panel = screen.getByRole("region", { name: "Reviewed registered epoch" });
  await within(panel).findByText("/repository/rules");
  expect(commands).toHaveLength(0);
  await userEvent.click(within(panel).getByRole("button", { name: "Activate reviewed epoch" }));
  await screen.findByText(/Activation outcome unknown/);
  expect(within(panel).getByRole("button", { name: "Activate reviewed epoch" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Retry same command" }));
  await screen.findByText("Activation confirmed at revision 5.");
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
  expect(commands[0]).toEqual({
    schemaVersion: "ci-config-epoch-activation/v3",
    installationId: 1,
    repositoryId: 1,
    targetEpochId: target.epochId,
    expectedSourceHash: target.sourceHash,
    registeredEpochReviewOperationId: "review-one",
    expectedRevision: 4,
    operationId: expect.any(String),
  });
  expect(
    within(panel).queryByRole("button", { name: "Activate reviewed epoch" }),
  ).not.toBeInTheDocument();
  expect(
    requests.filter((request) => request.method === "POST").map((request) => request.url),
  ).toEqual([
    `${globalThis.location.origin}/api/v1/config/activations`,
    `${globalThis.location.origin}/api/v1/config/activations`,
  ]);
});

function reviewedCallbackSearch() {
  const target = configEpoch();
  return `?installationId=1&repositoryId=1&limit=10&repositoryAttestation=registered-epoch-reviewed&targetEpochId=${target.epochId}&expectedSourceHash=${target.sourceHash}&reviewOperationId=review-one&reviewBaseEpochId=${activeEpochId}&reviewBaseRevision=4`;
}

function routeDeferredPreview(commands: unknown[]) {
  const previews: { readonly request: Request; readonly resolve: (value: Response) => void }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/preview")) {
        const pending = Promise.withResolvers<Response>();
        previews.push({ request, resolve: pending.resolve });
        return pending.promise;
      }
      if (path.endsWith("/status")) return configJson(configStatus());
      if (request.method === "POST") {
        commands.push(await request.json());
        return configJson({
          schemaVersion: "ci-config-epoch-activation-result/v1",
          ok: true,
          epochId: configEpoch().epochId,
          duplicate: false,
          revision: 5,
        });
      }
      return Response.json(workbenchFixture());
    }),
  );
  return previews;
}

// Kills: activation enabled while the preview read is pending (or without its result), and a
// gate that treats only a ready preview as terminal.
test.each([
  ["ready preview", () => configJson(configPreview()), "/repository/rules"],
  [
    "failed preview",
    () => configJson({ ok: false, error: "unavailable", diagnostics: [] }, 503),
    "Configuration service is unavailable.",
  ],
] as const)(
  "reviewed activation stays disabled until the %s is displayed",
  async (_name, response, shown) => {
    vi.stubGlobal("location", {
      origin: globalThis.location.origin,
      search: reviewedCallbackSearch(),
      assign: vi.fn(),
    });
    const commands: unknown[] = [];
    const previews = routeDeferredPreview(commands);
    render(<ConfigurationPage {...props()} />);
    const panel = screen.getByRole("region", { name: "Reviewed registered epoch" });
    await waitFor(() => expect(previews).toHaveLength(1));
    const activate = within(panel).getByRole("button", { name: "Activate reviewed epoch" });
    expect(activate).toBeDisabled();
    await userEvent.click(activate);
    expect(commands).toHaveLength(0);
    await act(async () => previews[0]?.resolve(response()));
    await within(panel).findByText(shown);
    expect(activate).toBeEnabled();
    await userEvent.click(activate);
    await screen.findByText("Activation confirmed at revision 5.");
    expect(commands).toHaveLength(1);
  },
);

// Kills: a reviewed preview read that survives navigation or document hiding, publishes its
// late response into the hidden panel, or is not re-read on return.
test.each([
  [
    "navigation",
    (view: ReturnType<typeof render>, base: ReturnType<typeof props>) =>
      view.rerender(<ConfigurationPage {...base} active={false} />),
    (view: ReturnType<typeof render>, base: ReturnType<typeof props>) =>
      view.rerender(<ConfigurationPage {...base} />),
  ],
  [
    "document hiding",
    async () => {
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    },
    async () => {
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
      await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    },
  ],
] as const)(
  "reviewed preview read stops on %s and reads again on return",
  async (_name, hide, show) => {
    onTestFinished(() => {
      vi.restoreAllMocks();
    });
    vi.stubGlobal("location", {
      origin: globalThis.location.origin,
      search: reviewedCallbackSearch(),
      assign: vi.fn(),
    });
    const commands: unknown[] = [];
    const previews = routeDeferredPreview(commands);
    const base = props();
    const view = render(<ConfigurationPage {...base} />);
    const panel = screen.getByRole("region", { name: "Reviewed registered epoch" });
    await waitFor(() => expect(previews).toHaveLength(1));
    await hide(view, base);
    expect(previews[0]?.request.signal.aborted).toBe(true);
    await act(async () => previews[0]?.resolve(configJson(configPreview())));
    expect(within(panel).queryByText("/repository/rules")).not.toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Activate reviewed epoch" })).toBeDisabled();
    expect(previews).toHaveLength(1);
    await show(view, base);
    await waitFor(() => expect(previews).toHaveLength(2));
    expect(within(panel).getByRole("button", { name: "Activate reviewed epoch" })).toBeDisabled();
    await act(async () => previews[1]?.resolve(configJson(configPreview())));
    await within(panel).findByText("/repository/rules");
    expect(within(panel).getByRole("button", { name: "Activate reviewed epoch" })).toBeEnabled();
    expect(commands).toHaveLength(0);
  },
);
