import { webcrypto } from "node:crypto";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ciEconomicsAttemptPageFixture } from "./ciEconomicsFixture";
import { EconomicsConsole } from "./consoleSelectionHarness";
import { budgetSignals } from "./economicsBudgetFixture";
import {
  economicsDiscovery,
  economicsMeasurements,
  economicsReport,
  economicsReportPage,
  economicsSource,
  economicsSourceItem,
  economicsSourcePage,
} from "./economicsConsoleFixture";
import { controlPlaneSessionFixture } from "./fixture";

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => {
  vi.unstubAllGlobals();
});
const scope = { installationId: 1, repositoryId: 1, limit: 10 };
const session = controlPlaneSessionFixture({ roles: ["audit", "configure", "read"] });

function mockReads() {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const path = new URL(request.url).pathname;
      if (path.endsWith("/budget-signals")) return Response.json(budgetSignals([]));
      if (path.endsWith("/measurements")) return Response.json(economicsMeasurements());
      if (path.includes("/sources/") && path.endsWith("/reports"))
        return Response.json(economicsReportPage());
      if (path.endsWith("/budget")) {
        const report = economicsReport();
        return Response.json({
          schemaVersion: "ci-economics-report-budget/v1",
          ok: true,
          report,
          measurement: report.payload.measurements[1],
          maximumUs: 20,
          thresholdAuthority: "caller_supplied",
          evaluationWindow: "exact_report",
          outcome: "within_budget",
        });
      }
      if (path.includes("/reports/")) return Response.json(economicsReport());
      if (path.endsWith("/attempts"))
        return Response.json(ciEconomicsAttemptPageFixture({ items: [] }));
      return Response.json(economicsSourcePage());
    }),
  );
  return requests;
}

test("navigates retained runs, report evidence and explicit budget without provider writes", async () => {
  const requests = mockReads();
  render(<EconomicsConsole scope={scope} authorityRevision={1} session={session} />);
  await userEvent.click(await screen.findByRole("button", { name: "Inspect run 4201, attempt 2" }));
  expect(await screen.findByText("Provider durations")).toBeVisible();
  expect(await screen.findAllByText("100 ms")).toHaveLength(3);
  await userEvent.click(await screen.findByRole("button", { name: /^Inspect report/ }));
  expect(await screen.findByRole("heading", { name: "backend-tests" })).toBeVisible();
  expect(screen.getByText("20 us")).toBeVisible();
  expect(screen.getByText("Reporter elapsed")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Use as baseline" }));
  expect(screen.getByRole("button", { name: "Compare 1/2" })).toBeDisabled();
  await userEvent.type(screen.getByRole("textbox", { name: "Maximum (microseconds)" }), "20");
  await userEvent.click(screen.getByRole("button", { name: "Evaluate" }));
  expect(await screen.findByText("within budget")).toBeVisible();
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});
test("tabs expose only the selected operation and support keyboard navigation", async () => {
  const requests = mockReads();
  render(<EconomicsConsole scope={scope} authorityRevision={1} session={session} />);
  await screen.findByRole("button", { name: "Inspect run 4201, attempt 2" });
  screen.getByRole("tab", { name: "Registered runs" }).focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Discover runs" })).toHaveFocus();
  expect(
    screen.queryByRole("button", { name: "Inspect run 4201, attempt 2" }),
  ).not.toBeInTheDocument();
  expect(requests.some((request) => request.method === "POST")).toBe(false);
  await userEvent.keyboard("{End}");
  expect(screen.getByRole("tab", { name: "Signals" })).toHaveFocus();
  expect(await screen.findByText("No retained signals match this view.")).toBeVisible();
  await userEvent.keyboard("{Home}");
  expect(screen.getByRole("tab", { name: "Registered runs" })).toHaveFocus();
});
test.each([
  ["capacity", "Repository collection capacity reached"],
  ["not-found", "Run unavailable for registration"],
  ["malformed-not-found", "Registration response could not be validated"],
  ["invalid-response", "Registration response could not be validated"],
  ["network-failure", "Registration could not be confirmed"],
] as const)(
  "discovery registration remains explicit and scoped after %s",
  async (failure, label) => {
    let registrationCount = 0;
    const requests: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        requests.push(request);
        const path = new URL(request.url).pathname;
        if (request.method === "GET") return Response.json(economicsSourcePage());
        if (path.endsWith("/source-discovery")) {
          const body = await request.clone().json();
          return Response.json({
            ...economicsDiscovery(),
            createdFrom: body.createdFrom,
            createdThrough: body.createdThrough,
          });
        }
        registrationCount += 1;
        if (failure === "network-failure") throw new TypeError("Connection lost");
        if (failure === "not-found")
          return Response.json({ ok: false, error: "not_found" }, { status: 404 });
        if (failure === "malformed-not-found") return Response.json({}, { status: 404 });
        if (failure === "invalid-response") return Response.json({});
        return Response.json(
          {
            schemaVersion: "ci-economics-source-registration/v2",
            source: economicsSource(),
            outcome: "capacity_reached",
          },
          { status: 409 },
        );
      }),
    );
    render(<EconomicsConsole scope={scope} authorityRevision={1} session={session} />);
    await userEvent.click(screen.getByRole("tab", { name: "Discover runs" }));
    expect(registrationCount).toBe(0);
    const from = screen.getByLabelText("Created from (UTC)");
    const through = screen.getByLabelText("Created through (UTC)");
    fireEvent.change(from, { target: { value: "2026-09-08T00:00:00" } });
    fireEvent.change(through, { target: { value: "2026-09-08T12:00:00" } });
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Register run 4201, attempt 2" }),
    );
    expect(await screen.findByText(label)).toBeVisible();
    expect(screen.queryByText("Registered for collection")).not.toBeInTheDocument();
    expect(screen.queryByText("Evidence is not retained")).not.toBeInTheDocument();
    expect(registrationCount).toBe(1);
    const command = requests.find(
      (request) => new URL(request.url).pathname === "/api/v2/economics/sources",
    );
    expect(command?.headers.get("x-csrf-token")).toBe(session.csrfToken);
    expect(await command?.json()).toEqual({
      installationId: 1,
      repositoryId: 1,
      workflowRunId: 4201,
      runAttempt: 2,
    });
  },
);
test.each(["scope", "authority"] as const)(
  "discards a delayed response after %s changes",
  async (change) => {
    let resolve: ((response: Response) => void) | undefined;
    const slow = new Promise<Response>((done) => {
      resolve = done;
    });
    const requests: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        requests.push(request);
        if (requests.length === 1) return slow;
        const repositoryId = change === "scope" ? 2 : 1;
        return Response.json({
          ...economicsSourcePage([economicsSourceItem(economicsSource(9002, repositoryId))]),
          repositoryId,
        });
      }),
    );
    const view = render(<EconomicsConsole scope={scope} authorityRevision={1} session={session} />);
    await waitFor(() => expect(requests).toHaveLength(1));
    view.rerender(
      <EconomicsConsole
        scope={{ ...scope, repositoryId: change === "scope" ? 2 : 1 }}
        authorityRevision={change === "authority" ? 2 : 1}
        session={session}
      />,
    );
    expect(
      await screen.findByRole("button", { name: "Inspect run 9002, attempt 2" }),
    ).toBeVisible();
    await act(async () => {
      resolve?.(Response.json(economicsSourcePage()));
    });
    expect(
      screen.queryByRole("button", { name: "Inspect run 4201, attempt 2" }),
    ).not.toBeInTheDocument();
    expect(requests[0]?.signal.aborted).toBe(true);
  },
);
test("invalid evidence supports a bounded explicit retry", async () => {
  let count = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(++count === 1 ? {} : economicsSourcePage())),
  );
  render(<EconomicsConsole scope={scope} authorityRevision={1} session={undefined} />);
  await userEvent.click(await screen.findByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: "Inspect run 4201, attempt 2" })).toBeVisible();
  expect(count).toBe(2);
});
