import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "../src/App";
import { ciEconomicsAttemptPageFixture } from "./ciEconomicsFixture";
import { economicsSourcePage } from "./economicsConsoleFixture";
import {
  configActivationFixture,
  controlPlaneSessionFixture,
  installationCatalogFixture,
  installationFixture,
  repositoryFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "./fixture";
import {
  governanceBaselineApprovalFixture,
  governanceBaselineRecordFixture,
} from "./governanceBaselineFixture";
import {
  governanceComparisonFixture,
  unbaselinedGovernanceComparisonFixture,
} from "./governanceComparisonFixture";

afterEach(() => {
  sessionStorage.clear();
  history.replaceState(null, "", "/workbench");
  vi.unstubAllGlobals();
});

function renderApp(handler: (request: Request) => Promise<Response> = admittedResponse) {
  vi.stubGlobal("fetch", vi.fn(handler));
  return render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

test("keeps authenticated account actions in the sidebar across navigation modes", async () => {
  renderApp((request) =>
    requestUrl(request).pathname === "/api/v1/auth/session"
      ? Promise.resolve(Response.json(controlPlaneSessionFixture()))
      : admittedResponse(request),
  );
  const account = screen.getByRole("region", { name: "Administrator account" });
  expect(await within(account).findByRole("button", { name: "Sign out" })).toBeVisible();
  expect(account.closest("aside")).not.toBeNull();
  expect(account.closest("header")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
  expect(within(account).getByRole("button", { name: "Sign out" })).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
  expect(within(account).getByRole("button", { name: "Sign out" })).toBeVisible();
});

test("paginates organizations without carrying repository authority across pages", async () => {
  const user = userEvent.setup();
  const other = installationFixture({
    installationId: 2,
    accountId: 202,
    accountLogin: "another-org",
  });
  renderApp(async (request) => {
    const url = requestUrl(request);
    if (url.pathname === "/api/v1/workbench/installations") {
      const page = Number(url.searchParams.get("page"));
      return Response.json(
        installationCatalogFixture({
          page,
          hasNextPage: page === 1,
          installations: page === 1 ? [installationFixture()] : [other],
        }),
      );
    }
    if (url.pathname === "/api/v1/workbench/installations/2/repositories") {
      return Response.json(
        repositoryPageFixture({
          installation: other,
          repositories: [
            repositoryFixture({
              scope: { installationId: 2, repositoryId: 22 },
              ownerId: 202,
              ownerLogin: "another-org",
              name: "sample-service",
              fullName: "another-org/sample-service",
              workbenchAuthorized: false,
            }),
          ],
        }),
      );
    }
    return admittedResponse(request);
  });
  expect(await screen.findByText("ci-coordinator")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Next organization page" }));
  expect(await screen.findByText("sample-service")).toBeVisible();
  expect(screen.queryByText("ci-coordinator")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open" })).toBeDisabled();
  await user.click(screen.getByText("Restricted by deployment policy"));
  expect(screen.getByText("2:22")).toBeVisible();
  expect(screen.getByRole("link", { name: "Repository access configuration" })).toHaveAttribute(
    "href",
    "https://github.com/research-engineering/ci-coordinator/blob/master/docs/how-to/discover-installed-organizations.md",
  );
  expect(screen.getByRole("button", { name: "Next organization page" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Previous organization page" }));
  expect(await screen.findByText("ci-coordinator")).toBeVisible();
});

test("discovers the organization and repository before opening exact evidence", async () => {
  const user = userEvent.setup();
  renderApp();

  expect(await screen.findByRole("option", { name: "example-org" })).toBeVisible();
  expect(await screen.findByText("ci-coordinator")).toBeVisible();
  expect(screen.getByRole("spinbutton", { name: "Installation", hidden: true })).not.toBeVisible();
  await user.click(screen.getByRole("button", { name: "Open" }));

  expect(await screen.findByText("Ledger revision")).toBeVisible();
  expect(screen.getByText("No plans in this snapshot.")).toBeVisible();
  expect(screen.getByText("valid")).toBeVisible();
  expect(
    within(screen.getByRole("complementary")).getByText("example-org/ci-coordinator"),
  ).toBeVisible();
});

test("scans the selected repository and discloses evidence before policy source", async () => {
  const user = userEvent.setup();
  renderApp();
  await user.click(await screen.findByRole("button", { name: "Open" }));
  await user.click(screen.getByRole("link", { name: "Workflows" }));

  expect(await screen.findByRole("heading", { name: "Workflow discovery" })).toBeVisible();
  expect(await screen.findByText("Observe-only policy admitted")).toBeVisible();
  expect(screen.getByText("job.permissions.effective")).toBeVisible();
  expect(
    screen.queryByText("Static syntax does not prove runtime behavior."),
  ).not.toBeInTheDocument();
  await user.click(screen.getByText("Authority boundaries"));
  expect(screen.getByText("proposal is not active policy")).toBeVisible();
});

test("only visited tasks load and same-scope navigation preserves discovery input", async () => {
  const requests: Request[] = [];
  renderApp(async (request) => {
    requests.push(request);
    return admittedResponse(request);
  });
  await userEvent.click(await screen.findByRole("button", { name: "Open" }));
  await screen.findByText("Ledger revision");
  expect(
    requests.some((request) =>
      /workflow-discovery|governance-comparison|\/economics\//.test(request.url),
    ),
  ).toBe(false);
  await userEvent.click(screen.getByRole("link", { name: "Workflows" }));
  await screen.findByText("Observe-only policy admitted");
  const reads = requests.length;
  await userEvent.type(screen.getByRole("textbox", { name: "Revision" }), "draft-revision");
  await userEvent.click(screen.getByRole("link", { name: "Overview" }));
  expect(screen.queryByRole("textbox", { name: "Revision" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Configuration" }));
  expect(screen.getByText("No configuration epochs in this snapshot.")).toBeVisible();
  expect(screen.queryByText("No plans in this snapshot.")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("link", { name: "Workflows" }));
  expect(screen.getByRole("textbox", { name: "Revision" })).toHaveValue("draft-revision");
  expect(requests).toHaveLength(reads);
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});

test.each([
  [
    "Workflows",
    "/api/v1/workbench/repositories/1/1/workflow-discovery",
    "Observe-only policy admitted",
  ],
  ["CI economics", "/api/v2/economics/repositories/1/1/sources", "No retained runs."],
  [
    "Governance",
    "/api/v1/workbench/repositories/1/1/governance-comparison",
    "Authentication required",
  ],
] as const)(
  "first visit to %s reaches its scoped reader and content",
  async (view, path, content) => {
    const requests: Request[] = [];
    renderApp(async (request) => {
      requests.push(request);
      return admittedResponse(request);
    });
    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    await screen.findByText("Ledger revision");
    expect(requests.some((request) => requestUrl(request).pathname === path)).toBe(false);
    await userEvent.click(screen.getByRole("link", { name: view }));
    expect(await screen.findByText(content)).toBeVisible();
    expect(requests.some((request) => requestUrl(request).pathname === path)).toBe(true);
    expect(requests.every((request) => request.method === "GET")).toBe(true);
  },
);

test.each(["logout", "expiry"] as const)(
  "%s discards hidden drafts and fences a pending approval after navigation",
  async (exit) => {
    const now = Date.now();
    const clock = vi.spyOn(Date, "now").mockReturnValue(now);
    const timers = vi.spyOn(globalThis, "setTimeout");
    const pending = Promise.withResolvers<Response>();
    const mutations: Request[] = [];
    let response: Response | undefined;
    let authorized = true;
    try {
      renderApp(async (request) => {
        const path = requestUrl(request).pathname;
        if (path === "/api/v1/auth/session") {
          if (!authorized)
            return Response.json({ ok: false, error: "unauthenticated" }, { status: 401 });
          return Response.json(
            controlPlaneSessionFixture({ expiresAt: new Date(now + 60_000).toISOString() }),
          );
        }
        if (path === "/api/v1/auth/keycloak/logout") {
          authorized = false;
          return Response.json({ ok: false, error: "unauthenticated" }, { status: 401 });
        }
        if (path === "/api/v1/workbench/repositories/1/1/governance-comparison" && authorized) {
          return Response.json(unbaselinedGovernanceComparisonFixture());
        }
        if (path === "/api/v1/workbench/repositories/1/1/governance-baselines") {
          mutations.push(request);
          const command = await request.clone().json();
          response = Response.json(
            governanceBaselineApprovalFixture({
              baseline: {
                ...governanceBaselineApprovalFixture().baseline,
                operationId: command.operationId,
              },
              requestOperationId: command.operationId,
            }),
            { status: 201 },
          );
          return pending.promise;
        }
        return admittedResponse(request);
      });
      await userEvent.click(await screen.findByRole("button", { name: "Open" }));
      await userEvent.click(screen.getByRole("link", { name: "Workflows" }));
      await screen.findByText("Observe-only policy admitted");
      await userEvent.type(screen.getByRole("textbox", { name: "Revision" }), "hidden-draft");
      await userEvent.click(screen.getByRole("link", { name: "Governance" }));
      await userEvent.type(
        await screen.findByRole("textbox", { name: "Approval reason" }),
        "Adopt governance",
      );
      await userEvent.click(screen.getByRole("button", { name: "Approve baseline" }));
      await waitFor(() => expect(response).toBeDefined());
      for (const name of ["Collapse sidebar", "Expand sidebar"]) {
        await userEvent.click(screen.getByRole("button", { name }));
        expect(screen.getByRole("textbox", { name: "Approval reason" })).toHaveValue(
          "Adopt governance",
        );
        expect(screen.getByRole("button", { name: "Approving" })).toBeDisabled();
        expect(screen.getByDisplayValue("hidden-draft")).not.toBeVisible();
        expect(mutations).toHaveLength(1);
        expect(mutations[0]?.signal.aborted).toBe(false);
      }
      await userEvent.click(screen.getByRole("link", { name: "Overview" }));
      expect(mutations[0]?.signal.aborted).toBe(false);
      await userEvent.click(screen.getByRole("link", { name: "Governance" }));
      expect(screen.getByRole("button", { name: "Approving" })).toBeDisabled();
      expect(screen.getByRole("textbox", { name: "Approval reason" })).toHaveValue(
        "Adopt governance",
      );
      expect(mutations).toHaveLength(1);
      await userEvent.click(screen.getByRole("link", { name: "Overview" }));
      if (exit === "logout") {
        await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
      } else {
        sessionStorage.setItem(
          "ci-coordinator.session-return.v1",
          JSON.stringify({ version: 1, attemptedAt: now, returnTo: null }),
        );
        const callback = timers.mock.calls.findLast(([, delay]) => delay === 60_000)?.[0];
        if (typeof callback !== "function")
          throw new TypeError("session expiry timer was not installed");
        authorized = false;
        await act(async () => callback());
      }
      await waitFor(() => expect(mutations[0]?.signal.aborted).toBe(true));
      await act(async () => {
        if (!response) throw new TypeError("pending approval response was not prepared");
        pending.resolve(response);
      });
      expect(screen.queryByDisplayValue("hidden-draft")).not.toBeInTheDocument();
      expect(screen.queryByDisplayValue("Adopt governance")).not.toBeInTheDocument();
      expect(screen.queryByText("Expected governance state approved")).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("link", { name: "Governance" }));
      expect(await screen.findByText("Authentication required")).toBeVisible();
      expect(screen.queryByRole("button", { name: "Approve baseline" })).not.toBeInTheDocument();
      expect(screen.queryByText("Expected governance state approved")).not.toBeInTheDocument();
      expect(mutations).toHaveLength(1);
    } finally {
      timers.mockRestore();
      clock.mockRestore();
    }
  },
);

test.each([
  "installationId=0&repositoryId=1&limit=10",
  "installationId=1&repositoryId=1&repositoryId=2&limit=10",
  "installationId=1&repositoryId=1",
])("invalid URL scope does not become a repository read: %s", async (coordinates) => {
  history.replaceState(null, "", `/workbench?${coordinates}&view=workflows`);
  const requests: Request[] = [];
  renderApp(async (request) => {
    requests.push(request);
    return admittedResponse(request);
  });
  await screen.findByText("ci-coordinator");
  expect(
    requests.some((request) =>
      requestUrl(request).pathname.startsWith("/api/v1/workbench/repositories/"),
    ),
  ).toBe(false);
  expect(screen.queryByRole("link", { name: "Workflows" })).not.toBeInTheDocument();
});

test("catalog filtering survives visiting and leaving the selected repository", async () => {
  renderApp();
  await screen.findByText("ci-coordinator");
  const search = screen.getByRole("searchbox", { name: "Search loaded page" });
  await userEvent.type(search, "coordinator");
  await userEvent.click(screen.getByRole("button", { name: "Open" }));
  await userEvent.click(screen.getByRole("link", { name: "Repositories" }));
  expect(screen.getByRole("searchbox", { name: "Search loaded page" })).toHaveValue("coordinator");
  expect(screen.getByRole("button", { name: "Open" })).toBeVisible();
});

test("sidebar defaults follow the task without changing catalog or workspace state", async () => {
  renderApp();
  const open = await screen.findByRole("button", { name: "Open" });
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  await userEvent.click(open);
  expect(await screen.findByText("Ledger revision")).toBeVisible();
  expect(screen.getByRole("button", { name: "Collapse sidebar" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await userEvent.click(screen.getByRole("link", { name: "Repositories" }));
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
});

test.each([true, false])(
  "explicit compact preference %s survives navigation without effects",
  async (compact) => {
    const requests: Request[] = [];
    renderApp(async (request) => {
      requests.push(request);
      return admittedResponse(request);
    });
    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    await screen.findByText("Ledger revision");
    const before = requests.length;
    const url = location.href;
    await userEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    if (!compact) await userEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(requests).toHaveLength(before);
    expect(location.href).toBe(url);
    expect(screen.getByText("Ledger revision")).toBeVisible();
    const action = compact ? "Expand sidebar" : "Collapse sidebar";
    await userEvent.click(screen.getByRole("link", { name: "Repositories" }));
    expect(screen.getByRole("button", { name: action })).toHaveAttribute(
      "aria-expanded",
      String(!compact),
    );
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByRole("button", { name: action })).toHaveAttribute(
      "aria-expanded",
      String(!compact),
    );
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  },
);

test("shows effective governance without claiming a baseline or compliance", async () => {
  const user = userEvent.setup();
  renderApp(async (request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/auth/session") return Response.json(controlPlaneSessionFixture());
    if (path === "/api/v1/workbench/repositories/1/1/governance-comparison") {
      return Response.json(unbaselinedGovernanceComparisonFixture());
    }
    return admittedResponse(request);
  });
  await user.click(await screen.findByRole("button", { name: "Open" }));
  await user.click(screen.getByRole("link", { name: "Governance" }));

  expect(await screen.findByRole("heading", { name: "Effective governance" })).toBeVisible();
  expect(await screen.findByText("Observation only")).toBeVisible();
  expect(screen.getByText("Best effort")).toBeVisible();
  expect(screen.getByText("required_status_checks")).toBeVisible();
  expect(screen.getByText("Canonical provider rule")).not.toBeVisible();

  await user.click(screen.getByText("required_status_checks"));
  expect(screen.getByText("Canonical provider rule")).toBeVisible();
  expect(screen.getByText("No approved baseline")).toBeVisible();
  expect(screen.getByText("unbaselined")).toBeVisible();
  expect(screen.queryByRole("button", { name: /compliance|enforce/i })).not.toBeInTheDocument();
});

test("binds an approved governance receipt to the current authority revision", async () => {
  const requests: Request[] = [];
  let retainedOperationId: string | undefined;
  renderApp(async (request) => {
    requests.push(request);
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/auth/session") return Response.json(controlPlaneSessionFixture());
    if (path === "/api/v1/workbench/repositories/1/1/governance-comparison") {
      if (!retainedOperationId) {
        return Response.json(unbaselinedGovernanceComparisonFixture());
      }
      return Response.json(
        governanceComparisonFixture({
          baseline: governanceBaselineRecordFixture({
            operationId: retainedOperationId,
          }),
        }),
      );
    }
    if (path === "/api/v1/workbench/repositories/1/1/governance-baselines") {
      const command = await request.clone().json();
      retainedOperationId = command.operationId;
      return Response.json(
        governanceBaselineApprovalFixture({
          baseline: {
            ...governanceBaselineApprovalFixture().baseline,
            operationId: command.operationId,
          },
          requestOperationId: command.operationId,
        }),
        { status: 201 },
      );
    }
    if (path === "/api/v1/auth/keycloak/logout") {
      return Response.json({ ok: true, redirectUrl: "/workbench" });
    }
    return admittedResponse(request);
  });

  await userEvent.click(await screen.findByRole("button", { name: "Open" }));
  await userEvent.click(screen.getByRole("link", { name: "Governance" }));
  await userEvent.type(
    await screen.findByRole("textbox", { name: "Approval reason" }),
    "Adopt repository governance",
  );
  await userEvent.click(screen.getByRole("button", { name: "Approve baseline" }));

  expect(await screen.findByText("Expected governance state approved")).toBeVisible();
  expect(await screen.findByText("Approved expected state")).toBeVisible();
  expect(screen.queryByText(/compliant/i)).not.toBeInTheDocument();
  const mutation = requests.find(
    (request) =>
      request.method === "POST" && requestUrl(request).pathname.endsWith("governance-baselines"),
  );
  expect(mutation?.headers.get("x-csrf-token")).toBe("c".repeat(43));
  await expect(mutation?.clone().json()).resolves.toMatchObject({
    expectedActive: null,
    expectedStateDigest: "84e824c649cc36e3ec99cb36c0b37181e7b5fe6e0ed38e4b66bc5b016c821cb7",
    reason: "Adopt repository governance",
  });

  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(
    await screen.findByText("Administrator session required for baseline approval"),
  ).toBeVisible();
  expect(screen.queryByText("Expected governance state approved")).not.toBeInTheDocument();
});

test("re-reads the active baseline after an historical operation replay", async () => {
  let mutationCompleted = false;
  let postMutationReads = 0;
  const predecessor = governanceBaselineRecordFixture();
  const active = governanceBaselineRecordFixture({
    approvedAt: "2026-07-26T12:01:01Z",
    auditEventId: `audit_${"d".repeat(32)}`,
    operationId: "22222222-2222-4222-8222-222222222222",
    pointer: {
      baselineId: `governance-baseline:${"e".repeat(64)}`,
      stateDigest: predecessor.pointer.stateDigest,
      version: 2,
    },
    supersedes: predecessor.pointer,
  });
  renderApp(async (request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/auth/session") return Response.json(controlPlaneSessionFixture());
    if (path === "/api/v1/workbench/repositories/1/1/governance-comparison") {
      if (mutationCompleted) postMutationReads += 1;
      return Response.json(
        mutationCompleted
          ? governanceComparisonFixture({ baseline: active })
          : unbaselinedGovernanceComparisonFixture(),
      );
    }
    if (path === "/api/v1/workbench/repositories/1/1/governance-baselines") {
      const command = await request.clone().json();
      mutationCompleted = true;
      return Response.json(
        governanceBaselineApprovalFixture({
          baseline: { ...predecessor, operationId: command.operationId },
          requestOperationId: command.operationId,
          state: "duplicate",
        }),
      );
    }
    return admittedResponse(request);
  });

  await userEvent.click(await screen.findByRole("button", { name: "Open" }));
  await userEvent.click(screen.getByRole("link", { name: "Governance" }));
  await userEvent.type(
    await screen.findByRole("textbox", { name: "Approval reason" }),
    "Adopt repository governance",
  );
  await userEvent.click(screen.getByRole("button", { name: "Approve baseline" }));

  expect(await screen.findByText("Approval already retained")).toBeVisible();
  expect(await screen.findByText(/Version 2 approved/)).toBeVisible();
  expect(postMutationReads).toBe(1);
});

test("aborts and discards an approval from an older comparison generation", async () => {
  const pendingMutation = Promise.withResolvers<Response>();
  const predecessor = governanceBaselineRecordFixture();
  const replacement = governanceBaselineRecordFixture({
    approvedAt: "2026-07-26T12:01:01Z",
    auditEventId: `audit_${"d".repeat(32)}`,
    operationId: "22222222-2222-4222-8222-222222222222",
    pointer: {
      baselineId: `governance-baseline:${"e".repeat(64)}`,
      stateDigest: predecessor.pointer.stateDigest,
      version: 2,
    },
    supersedes: predecessor.pointer,
  });
  let refreshRequested = false;
  let comparisonReads = 0;
  let mutationRequest: Request | undefined;
  let lateResponse: Response | undefined;
  renderApp(async (request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/auth/session") return Response.json(controlPlaneSessionFixture());
    if (path === "/api/v1/workbench/repositories/1/1/governance-comparison") {
      comparisonReads += 1;
      return Response.json(
        governanceComparisonFixture({
          baseline: refreshRequested ? replacement : predecessor,
        }),
      );
    }
    if (path === "/api/v1/workbench/repositories/1/1/governance-baselines") {
      mutationRequest = request;
      const command = await request.clone().json();
      lateResponse = Response.json(
        governanceBaselineApprovalFixture({
          baseline: { ...replacement, operationId: command.operationId },
          requestOperationId: command.operationId,
        }),
        { status: 201 },
      );
      return pendingMutation.promise;
    }
    return admittedResponse(request);
  });

  await userEvent.click(await screen.findByRole("button", { name: "Open" }));
  await userEvent.click(screen.getByRole("link", { name: "Governance" }));
  await userEvent.type(
    await screen.findByRole("textbox", { name: "Approval reason" }),
    "Replace repository governance",
  );
  await userEvent.click(screen.getByRole("button", { name: "Replace baseline" }));
  await waitFor(() => expect(mutationRequest).toBeDefined());

  refreshRequested = true;
  await userEvent.click(screen.getByRole("button", { name: "Refresh effective governance" }));
  expect(await screen.findByText(/Version 2 approved/)).toBeVisible();
  await waitFor(() => expect(mutationRequest?.signal.aborted).toBe(true));
  const readsBeforeLateMutation = comparisonReads;

  await act(async () => {
    if (!lateResponse) throw new TypeError("late mutation response was not prepared");
    pendingMutation.resolve(lateResponse);
  });

  expect(screen.queryByText("Expected governance state approved")).not.toBeInTheDocument();
  expect(screen.getByText(/Version 2 approved/)).toBeVisible();
  expect(comparisonReads).toBe(readsBeforeLateMutation);
});

test("requires repository authority before activating the observed proposal", async () => {
  const requests: Request[] = [];
  renderApp(async (request) => {
    requests.push(request);
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/auth/session") return Response.json(controlPlaneSessionFixture());
    if (path === "/api/v1/workbench/repositories/1/1") {
      return Response.json(workbenchFixture());
    }
    if (path === "/api/v1/repository-attestations/github/start") {
      return Response.json({ error: "already_reviewed", ok: false }, { status: 409 });
    }
    if (path === "/api/v1/config/activations") {
      return Response.json(configActivationFixture());
    }
    return admittedResponse(request);
  });

  expect(await screen.findByText("Bart Simpson")).toBeVisible();
  await userEvent.click(await screen.findByRole("button", { name: "Open" }));
  await userEvent.click(screen.getByRole("link", { name: "Workflows" }));
  await userEvent.click(await screen.findByRole("button", { name: "Verify authority" }));
  await userEvent.click(await screen.findByRole("button", { name: "Activate proposal" }));

  expect(await screen.findByText("Configuration activated")).toBeVisible();
  expect(screen.getByText("Revision 1 is now authoritative")).toBeVisible();
  const attestation = requests.find(
    (request) =>
      request.method === "POST" &&
      requestUrl(request).pathname === "/api/v1/repository-attestations/github/start",
  );
  expect(attestation).toBeDefined();
  expect(attestation?.credentials).toBe("same-origin");
  expect(attestation?.headers.get("x-csrf-token")).toBe("c".repeat(43));
  await expect(attestation?.clone().json()).resolves.toMatchObject({
    expectedActive: null,
    expectedManifestId: "proposal:c0169591134297170c6402e83fc6b67f",
  });
  const activation = requests.find(
    (request) =>
      request.method === "POST" && requestUrl(request).pathname === "/api/v1/config/activations",
  );
  await expect(activation?.clone().json()).resolves.toMatchObject({
    expectedRevision: null,
    proposalManifestId: "proposal:c0169591134297170c6402e83fc6b67f",
    targetEpochId: "4".repeat(64),
  });
});

test("does not treat a forged repository callback query as retained authority", async () => {
  const operationId = "forged-review-operation";
  globalThis.history.replaceState(
    {},
    "",
    "/workbench?installationId=1&repositoryId=1&repositoryAttestation=reviewed&proposalManifestId=proposal:c0169591134297170c6402e83fc6b67f&reviewOperationId=" +
      operationId,
  );
  try {
    renderApp(async (request) => {
      const path = requestUrl(request).pathname;
      if (path === "/api/v1/auth/session") return Response.json(controlPlaneSessionFixture());
      if (path === "/api/v1/workbench/repositories/1/1") {
        return Response.json(workbenchFixture());
      }
      return admittedResponse(request);
    });

    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    await userEvent.click(screen.getByRole("link", { name: "Workflows" }));

    expect(
      await screen.findByText(
        "GitHub returned control. Confirm the retained review before activation.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Confirm review" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Activate proposal" })).not.toBeInTheDocument();
    expect(screen.getByText("awaiting review")).toBeVisible();
  } finally {
    globalThis.history.replaceState({}, "", "/");
  }
});

test("replays the exact callback operation before exposing retained authority", async () => {
  const operationId = "review-operation-from-callback";
  const attestationCommands: unknown[] = [];
  globalThis.history.replaceState(
    {},
    "",
    "/workbench?installationId=1&repositoryId=1&repositoryAttestation=reviewed&proposalManifestId=proposal:c0169591134297170c6402e83fc6b67f&reviewOperationId=" +
      operationId,
  );
  try {
    renderApp(async (request) => {
      const path = requestUrl(request).pathname;
      if (path === "/api/v1/auth/session") return Response.json(controlPlaneSessionFixture());
      if (path === "/api/v1/workbench/repositories/1/1") {
        return Response.json(workbenchFixture());
      }
      if (path === "/api/v1/repository-attestations/github/start") {
        attestationCommands.push(await request.clone().json());
        return Response.json({ error: "already_reviewed", ok: false }, { status: 409 });
      }
      return admittedResponse(request);
    });

    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    await userEvent.click(screen.getByRole("link", { name: "Workflows" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm review" }));

    expect(await screen.findByText("reviewed")).toBeVisible();
    expect(screen.getByRole("button", { name: "Activate proposal" })).toBeVisible();
    expect(attestationCommands).toHaveLength(1);
    expect(attestationCommands[0]).toMatchObject({ operationId });
  } finally {
    globalThis.history.replaceState({}, "", "/");
  }
});

test("invalidates personalized repository evidence after logout", async () => {
  let signedOut = false;
  let installationCalls = 0;
  renderApp(async (request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/auth/session") {
      return signedOut
        ? Response.json({ error: "unauthenticated", ok: false }, { status: 401 })
        : Response.json(controlPlaneSessionFixture());
    }
    if (path === "/api/v1/auth/keycloak/logout") {
      signedOut = true;
      return Response.json({ ok: true, redirectUrl: "/workbench" });
    }
    if (path === "/api/v1/workbench/installations") {
      installationCalls += 1;
      return signedOut
        ? Response.json(
            { error: "unauthenticated", ok: false, retryAfterSeconds: null },
            { status: 401 },
          )
        : Response.json(installationCatalogFixture());
    }
    if (path === "/api/v1/workbench/installations/1/repositories") {
      return Response.json(repositoryPageFixture());
    }
    throw new Error(`unexpected request: ${request.url}`);
  });

  expect(await screen.findByText("ci-coordinator")).toBeVisible();
  await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));

  expect(await screen.findByText(/Repositories authentication required/)).toBeVisible();
  expect(screen.queryByText("ci-coordinator")).not.toBeInTheDocument();
  expect(installationCalls).toBeGreaterThanOrEqual(2);
});

test("invalidates personalized repository evidence at session expiry", async () => {
  let installationCalls = 0;
  const expiresAt = Date.now() + 750;
  sessionStorage.setItem(
    "ci-coordinator.session-return.v1",
    JSON.stringify({ version: 1, attemptedAt: Date.now(), returnTo: null }),
  );
  renderApp(async (request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/auth/session") {
      if (Date.now() >= expiresAt)
        return Response.json({ ok: false, error: "unauthenticated" }, { status: 401 });
      return Response.json(
        controlPlaneSessionFixture({ expiresAt: new Date(expiresAt).toISOString() }),
      );
    }
    if (path === "/api/v1/workbench/installations") {
      installationCalls += 1;
      return Date.now() < expiresAt
        ? Response.json(installationCatalogFixture())
        : Response.json(
            { error: "unauthenticated", ok: false, retryAfterSeconds: null },
            { status: 401 },
          );
    }
    if (path === "/api/v1/workbench/installations/1/repositories") {
      return Response.json(repositoryPageFixture());
    }
    throw new Error(`unexpected request: ${request.url}`);
  });

  expect(await screen.findByText("ci-coordinator")).toBeVisible();
  expect(
    await screen.findByText(/Repositories authentication required/, undefined, {
      timeout: 2_000,
    }),
  ).toBeVisible();
  expect(screen.queryByText("ci-coordinator")).not.toBeInTheDocument();
  expect(installationCalls).toBeGreaterThanOrEqual(2);
});

test("admits only an exact lowercase revision before rescanning", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn(admittedResponse);
  renderApp(fetchMock);
  await user.click(await screen.findByRole("button", { name: "Open" }));
  await user.click(screen.getByRole("link", { name: "Workflows" }));
  await screen.findByText("Observe-only policy admitted");
  const revision = screen.getByRole("textbox", { name: "Revision" });

  await user.type(revision, "main");
  await user.click(screen.getByRole("button", { name: "Scan" }));
  expect(screen.getByText("Enter an exact 40-character lowercase commit SHA.")).toBeVisible();
  expect(
    fetchMock.mock.calls.some(
      ([request]) => requestUrl(request).searchParams.get("revision") === "main",
    ),
  ).toBe(false);

  await user.clear(revision);
  await user.type(revision, "a".repeat(40));
  await user.click(screen.getByRole("button", { name: "Scan" }));
  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(
        ([request]) => requestUrl(request).searchParams.get("revision") === "a".repeat(40),
      ),
    ).toBe(true);
  });
});

test.each([
  [401, "Authentication required"],
  [403, "Repository access denied"],
  [503, "Service unavailable"],
] as const)("renders workbench HTTP %s as a typed failure", async (status, title) => {
  history.replaceState(null, "", "/workbench?installationId=1&repositoryId=1&limit=10");
  renderApp(async (request) =>
    requestUrl(request).pathname === "/api/v1/workbench/repositories/1/1"
      ? Response.json({ ok: false }, { status })
      : admittedResponse(request),
  );
  expect(await screen.findByRole("heading", { name: title })).toBeVisible();
});

test.each([
  ["valid", "positive"],
  ["in_progress", "info"],
  ["invalid", "negative"],
  ["unavailable", "warning"],
] as const)("preserves %s replay semantics", async (status, tone) => {
  history.replaceState(null, "", "/workbench?installationId=1&repositoryId=1&limit=10");
  renderApp(async (request) =>
    requestUrl(request).pathname === "/api/v1/workbench/repositories/1/1"
      ? Response.json(
          workbenchFixture({
            replay: {
              reason: null,
              snapshotRevision: 42,
              status,
              verifiedRevision: status === "valid" ? 42 : null,
            },
          }),
        )
      : admittedResponse(request),
  );

  const badge = await screen.findByText(status.replace("_", " "));
  expect(badge).toHaveClass(`status-badge--${tone}`);
});

test("validates advanced scope before issuing a request", async () => {
  const user = userEvent.setup();
  let resolveSession!: (response: Response) => void;
  const session = new Promise<Response>((resolve) => {
    resolveSession = resolve;
  });
  renderApp((request) =>
    requestUrl(request).pathname === "/api/v1/auth/session"
      ? session.then((response) => response.clone())
      : admittedResponse(request),
  );
  expect(screen.queryByText("Advanced degraded access")).not.toBeInTheDocument();
  await act(async () => resolveSession(Response.json(controlPlaneSessionFixture())));
  expect(await screen.findByRole("button", { name: "Sign out" })).toBeVisible();
  await user.click(await screen.findByText("Advanced degraded access"));
  const installation = screen.getByRole("spinbutton", { name: "Installation" });
  await user.clear(installation);
  await user.type(installation, "0");
  await user.click(screen.getByRole("button", { name: "Load snapshot" }));

  expect(screen.getByRole("alert")).toHaveTextContent("Installation: enter a whole number");
});

test("does not invent a repository failure when every installation is ineligible", async () => {
  const fetchMock = vi.fn(async (request: Request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/auth/session") {
      return Response.json({ error: "unauthenticated", ok: false }, { status: 401 });
    }
    if (path !== "/api/v1/workbench/installations") {
      throw new Error(`unexpected request: ${request.url}`);
    }
    return Response.json(
      installationCatalogFixture({
        installations: [
          {
            ...installationFixture(),
            state: "suspended",
          },
        ],
      }),
    );
  });
  renderApp(fetchMock);

  expect(await screen.findByText("No active organizations")).toBeVisible();
  expect(screen.getByRole("combobox", { name: "Organization" })).toBeVisible();
  expect(
    fetchMock.mock.calls.filter(
      ([request]) => requestUrl(request).pathname === "/api/v1/workbench/installations",
    ),
  ).toHaveLength(2);
  expect(screen.queryByText("Repositories connection failed")).not.toBeInTheDocument();
});

test("distinguishes an unavailable authorized catalog from an empty grant", async () => {
  renderApp(async () =>
    Response.json(
      installationCatalogFixture({
        complete: false,
        failures: [{ installationId: 1, reason: "unavailable", retryAfterSeconds: null }],
        installations: [],
      }),
    ),
  );

  expect(await screen.findByText("Authorized organizations unavailable")).toBeVisible();
  expect(screen.getByRole("combobox", { name: "Organization" })).toBeDisabled();
  expect(screen.queryByText("No authorized organizations")).not.toBeInTheDocument();
});

test("refreshes both organization and repository observations", async () => {
  let installationCalls = 0;
  let repositoryCalls = 0;
  renderApp(async (request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/workbench/installations") {
      installationCalls += 1;
      return Response.json(installationCatalogFixture());
    }
    if (path === "/api/v1/workbench/installations/1/repositories") {
      repositoryCalls += 1;
      return Response.json(
        repositoryCalls === 1
          ? repositoryPageFixture()
          : repositoryPageFixture({
              repositories: [
                repositoryFixture({
                  fullName: "example-org/sample-service",
                  name: "sample-service",
                }),
              ],
            }),
      );
    }
    throw new Error(`unexpected request: ${request.url}`);
  });

  expect(await screen.findByText("ci-coordinator")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Refresh repositories" }));

  expect(await screen.findByText("sample-service")).toBeVisible();
  await waitFor(() => {
    expect(installationCalls).toBeGreaterThanOrEqual(2);
    expect(repositoryCalls).toBeGreaterThanOrEqual(2);
  });
});

test("clears stale repository evidence when the selected installation becomes suspended", async () => {
  let installationState: "active" | "suspended" = "active";
  renderApp(async (request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/workbench/installations") {
      return Response.json(
        installationCatalogFixture({
          installations: [installationFixture({ state: installationState })],
        }),
      );
    }
    if (path === "/api/v1/workbench/installations/1/repositories") {
      return Response.json(repositoryPageFixture());
    }
    throw new Error(`unexpected request: ${request.url}`);
  });

  expect(await screen.findByText("ci-coordinator")).toBeVisible();
  installationState = "suspended";
  await userEvent.click(screen.getByRole("button", { name: "Refresh repositories" }));

  expect(await screen.findByText("No active organizations")).toBeVisible();
  expect(screen.queryByText("ci-coordinator")).not.toBeInTheDocument();
});

test("shows exact partial-installation and repository rate-limit evidence", async () => {
  renderApp(async (request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/workbench/installations") {
      return Response.json(
        installationCatalogFixture({
          complete: false,
          failures: [{ installationId: 2, reason: "rate_limited", retryAfterSeconds: 45 }],
        }),
      );
    }
    if (path === "/api/v1/workbench/installations/1/repositories") {
      return Response.json(
        { error: "rate_limited", ok: false, retryAfterSeconds: 30 },
        { status: 429 },
      );
    }
    throw new Error(`unexpected request: ${request.url}`);
  });

  expect(
    await screen.findByText(
      "Installation 2: GitHub rate limited the request; retry after 45 seconds",
    ),
  ).toBeVisible();
  expect(
    await screen.findByText("GitHub rate limit reached. Retry after 30 seconds."),
  ).toBeVisible();
});

test("keeps every admitted URL limit visible in the advanced control", async () => {
  const user = userEvent.setup();
  history.replaceState(null, "", "/workbench?installationId=1&repositoryId=1&limit=5");
  renderApp();
  expect(await screen.findByText("Ledger revision")).toBeVisible();
  await user.click(screen.getByRole("link", { name: "Repositories" }));
  await user.click(screen.getByText("Advanced degraded access"));

  expect(screen.getByRole("spinbutton", { name: "Items per section" })).toHaveValue(5);
  await user.click(screen.getByRole("link", { name: "Overview" }));
  expect(screen.getByText("Ledger revision")).toBeVisible();
});

test("a late response for an abandoned scope cannot replace current evidence", async () => {
  const first = Promise.withResolvers<Response>();
  const second = Promise.withResolvers<Response>();
  history.replaceState(null, "", "/workbench?installationId=1&repositoryId=1&limit=10");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = requestUrl(request).pathname;
      if (path === "/api/v1/workbench/repositories/1/1") return (await first.promise).clone();
      if (path === "/api/v1/workbench/repositories/1/2") return (await second.promise).clone();
      return admittedResponse(request);
    }),
  );
  const user = userEvent.setup();
  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

  await user.click(screen.getByRole("link", { name: "Repositories" }));
  await user.click(screen.getByText("Advanced degraded access"));
  const repository = screen.getByRole("spinbutton", { name: "Repository" });
  await user.clear(repository);
  await user.type(repository, "2");
  await user.click(screen.getByRole("button", { name: "Load snapshot" }));

  expect(screen.getByRole("heading", { name: "Loading snapshot" })).toBeVisible();
  expect(screen.queryByText("Ledger revision")).not.toBeInTheDocument();

  await act(async () => {
    second.resolve(
      Response.json(
        workbenchFixture({
          ledgerRevision: 99,
          scope: { installationId: 1, repositoryId: 2 },
        }),
      ),
    );
  });
  expect(await screen.findByText("99")).toBeVisible();

  await act(async () => {
    first.resolve(Response.json(workbenchFixture({ ledgerRevision: 1 })));
  });
  expect(screen.getByText("99")).toBeVisible();
  expect(screen.queryByText(/^1$/)).not.toBeInTheDocument();
});

test("aborts and discards governance evidence from an abandoned repository scope", async () => {
  const first = Promise.withResolvers<Response>();
  const firstSignals: AbortSignal[] = [];
  history.replaceState(null, "", "/workbench?installationId=1&repositoryId=1&limit=10");
  renderApp(async (request) => {
    const path = requestUrl(request).pathname;
    if (path === "/api/v1/workbench/repositories/1/1/governance-comparison") {
      firstSignals.push(request.signal);
      return first.promise;
    }
    if (path === "/api/v1/workbench/repositories/1/2/governance-comparison") {
      return Response.json(
        { error: "not_found", ok: false, retryAfterSeconds: null },
        { status: 404 },
      );
    }
    if (path === "/api/v1/workbench/repositories/1/2") {
      return Response.json(
        workbenchFixture({
          ledgerRevision: 99,
          scope: { installationId: 1, repositoryId: 2 },
        }),
      );
    }
    if (path === "/api/v1/workbench/repositories/1/2/workflow-discovery") {
      return Response.json({ error: "not_found", ok: false }, { status: 404 });
    }
    return admittedResponse(request);
  });

  await userEvent.click(screen.getByRole("link", { name: "Governance" }));
  await waitFor(() => expect(firstSignals.length).toBeGreaterThan(0));
  await userEvent.click(screen.getByRole("link", { name: "Repositories" }));
  await userEvent.click(screen.getByText("Advanced degraded access"));
  const repository = screen.getByRole("spinbutton", { name: "Repository" });
  await userEvent.clear(repository);
  await userEvent.type(repository, "2");
  await userEvent.click(screen.getByRole("button", { name: "Load snapshot" }));
  await userEvent.click(screen.getByRole("link", { name: "Governance" }));

  expect(await screen.findByText("Repository not found")).toBeVisible();
  expect(firstSignals.length).toBeGreaterThan(0);
  expect(firstSignals.every((signal) => signal.aborted)).toBe(true);

  await act(async () => {
    first.resolve(Response.json(governanceComparisonFixture()));
  });
  expect(screen.getByText("Repository not found")).toBeVisible();
  expect(screen.queryByText("required_status_checks")).not.toBeInTheDocument();
});

async function admittedResponse(request: Request): Promise<Response> {
  const path = requestUrl(request).pathname;
  if (path === "/api/v1/auth/session") {
    return Response.json({ error: "unauthenticated", ok: false }, { status: 401 });
  }
  if (path === "/api/v1/workbench/installations") {
    return Response.json(installationCatalogFixture());
  }
  if (path === "/api/v1/workbench/installations/1/repositories") {
    return Response.json(repositoryPageFixture());
  }
  if (path === "/api/v1/workbench/repositories/1/1") {
    return Response.json(workbenchFixture());
  }
  if (path === "/api/v1/economics/repositories/1/1/attempts") {
    return Response.json(ciEconomicsAttemptPageFixture({ items: [] }));
  }
  if (path === "/api/v2/economics/repositories/1/1/sources") {
    return Response.json(economicsSourcePage([]));
  }
  if (path === "/api/v1/workbench/repositories/1/1/workflow-discovery") {
    return Response.json(workflowDiscoveryFixture());
  }
  if (path === "/api/v1/workbench/repositories/1/1/governance-comparison") {
    return Response.json(
      { error: "unauthenticated", ok: false, retryAfterSeconds: null },
      { status: 401 },
    );
  }
  if (path === "/api/v1/workbench/repositories/1/1/governance-baselines") {
    return Response.json({ error: "unauthenticated", ok: false }, { status: 401 });
  }
  throw new Error(`unexpected request: ${request.url}`);
}

function requestUrl(request: Request): URL {
  return new URL(request.url);
}
