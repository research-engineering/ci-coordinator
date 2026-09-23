import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import {
  ACTIVITY_ACTOR,
  ACTIVITY_CURSOR,
  ACTIVITY_ISSUER,
  activityContinuation,
  activityPage,
  activityRequest,
} from "./activityFixture";
import { ActivityPanel } from "./consoleSelectionHarness";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const scope = { installationId: 7, repositoryId: 31, limit: 10 };

function reply(request: Request) {
  const url = new URL(request.url);
  return Response.json(activityPage(activityRequest(url).query));
}

test("global access activity needs no repository or manually entered issuer", async () => {
  const fetch = vi.fn(async (request: Request) => reply(request));
  vi.stubGlobal("fetch", fetch);
  render(<ActivityPanel scope={undefined} />);
  await screen.findByRole("table", { name: "Retained access events" });
  expect(screen.getByText(ACTIVITY_ISSUER)).toBeVisible();
  expect(screen.getByText("Security events are retained for 30 days.")).toBeVisible();
  expect(screen.queryByRole("tab", { name: "Repository actions" })).not.toBeInTheDocument();
  expect(new URL(fetch.mock.calls[0]?.[0].url ?? "").searchParams.has("issuer")).toBe(false);
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Action" }), "logout");
  await userEvent.click(screen.getByRole("button", { name: "Apply" }));
  await screen.findByRole("cell", { name: "logout" });
  expect(new URL(fetch.mock.calls.at(-1)?.[0].url ?? "").searchParams.get("action")).toBe("logout");
  expect(fetch.mock.calls.every(([request]) => request.method === "GET")).toBe(true);
});

test("keyboard source navigation replaces the exact repository query", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => reply(request)),
  );
  render(<ActivityPanel scope={scope} />);
  const security = screen.getByRole("tab", { name: "Access and sessions" });
  await userEvent.click(security);
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Repository actions" })).toHaveFocus();
  await screen.findByRole("table", { name: "Repository activity references" });
  expect(screen.getByText("Repository 31, installation 7")).toBeVisible();
  await userEvent.keyboard("{Home}");
  expect(security).toHaveFocus();
  await screen.findByRole("table", { name: "Retained access events" });
});

test("source replacement aborts old reads and never displays their late rows", async () => {
  const old = Promise.withResolvers<Response>();
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      requests.push(request);
      return requests.length === 1 ? old.promise : Promise.resolve(reply(request));
    }),
  );
  render(<ActivityPanel scope={scope} />);
  await waitFor(() => expect(requests).toHaveLength(1));
  await userEvent.click(screen.getByRole("tab", { name: "Repository actions" }));
  await screen.findByRole("table", { name: "Repository activity references" });
  expect(requests[0]?.signal.aborted).toBe(true);
  await act(async () => old.resolve(reply(requests[0] as Request)));
  expect(screen.queryByRole("table", { name: "Retained access events" })).not.toBeInTheDocument();
});

test("export failure is explicit, bounded and never silently retried", async () => {
  const fetch = vi.fn(async (request: Request) =>
    new URL(request.url).pathname.endsWith("/export")
      ? Response.json({ ok: false, error: "unavailable" }, { status: 503 })
      : reply(request),
  );
  vi.stubGlobal("fetch", fetch);
  render(<ActivityPanel scope={undefined} />);
  await screen.findByRole("table", { name: "Retained access events" });
  await userEvent.click(screen.getByRole("button", { name: "Export page" }));
  await screen.findByText(/The export could not be confirmed/);
  expect(
    fetch.mock.calls.filter(([request]) => new URL(request.url).pathname.endsWith("/export")),
  ).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Export page" })).toBeEnabled();
});

test("second-page export preserves exact continuation and filters and releases its object URL", async () => {
  const create = vi.fn((_blob: Blob) => "blob:https://console.example/activity"),
    revoke = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static override createObjectURL = create;
      static override revokeObjectURL = revoke;
    },
  );
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const { query, cursor } = activityRequest(new URL(request.url));
      return Response.json(activityContinuation(query, cursor));
    }),
  );
  render(<ActivityPanel scope={undefined} />);
  await screen.findByRole("table", { name: "Retained access events" });
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Action" }), "logout");
  await userEvent.type(screen.getByRole("textbox", { name: "Exact actor ID" }), ACTIVITY_ACTOR);
  await userEvent.click(screen.getByRole("button", { name: "Apply" }));
  await screen.findByRole("cell", { name: "logout" });
  await userEvent.click(screen.getByRole("button", { name: "Next activity page" }));
  await screen.findByText("Page 2");
  await screen.findByText("22222222-2222-4222-8222-222222222222");
  await waitFor(() => expect(screen.getByRole("button", { name: "Export page" })).toBeEnabled());
  expect(requests).toHaveLength(3);
  const first = [...new URL(requests[1]?.url ?? "").searchParams];
  expect(first).toContainEqual(["actor", ACTIVITY_ACTOR]);
  expect(first).toContainEqual(["action", "logout"]);
  const second = [...new URL(requests[2]?.url ?? "").searchParams].sort();
  expect(second).toEqual([...first, ["cursor", ACTIVITY_CURSOR]].sort());
  await userEvent.click(screen.getByRole("button", { name: "Export page" }));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(requests).toHaveLength(4);
  expect(new URL(requests[3]?.url ?? "").pathname).toBe("/api/v1/activity/security/export");
  expect([...new URL(requests[3]?.url ?? "").searchParams].sort()).toEqual(second);
  expect(create).toHaveBeenCalledOnce();
  const blob = create.mock.calls[0]?.[0];
  if (!blob) throw new Error("Export blob is missing");
  const content = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      typeof reader.result === "string"
        ? resolve(reader.result)
        : reject(new Error("Export is not text"));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
  expect(JSON.parse(content)).toMatchObject({ items: [{ sequence: 7 }], nextCursor: null });
  expect(revoke).toHaveBeenCalledWith("blob:https://console.example/activity");
  expect(document.querySelector("a[download]")).toBeNull();
});

test.each(["cursor", "limit"])(
  "activity fixture rejects repeated %s without losing multiplicity",
  (key) => {
    const url = new URL("https://console.example/api/v1/activity/security");
    url.searchParams.set("since", "2026-09-10T00:00:00Z");
    url.searchParams.set("until", "2026-09-13T00:00:00Z");
    url.searchParams.set("limit", "25");
    url.searchParams.set("cursor", ACTIVITY_CURSOR);
    expect(() => activityRequest(url)).not.toThrow();
    url.searchParams.append(key, key === "cursor" ? ACTIVITY_CURSOR : "25");
    expect(() => activityRequest(url)).toThrow(`Duplicate parameter: ${key}`);
  },
);

test("source replacement aborts a pending export before a late download", async () => {
  const pending = Promise.withResolvers<Response>();
  const requests: Request[] = [];
  const create = vi.fn(() => "blob:unexpected");
  vi.stubGlobal(
    "URL",
    class extends URL {
      static override createObjectURL = create;
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      requests.push(request);
      return new URL(request.url).pathname.endsWith("/export")
        ? pending.promise
        : Promise.resolve(reply(request));
    }),
  );
  render(<ActivityPanel scope={scope} />);
  await screen.findByRole("table", { name: "Retained access events" });
  await userEvent.click(screen.getByRole("button", { name: "Export page" }));
  const exportRequest = requests.find((request) =>
    new URL(request.url).pathname.endsWith("/export"),
  );
  if (!exportRequest) throw new Error("Export was not started");
  await userEvent.click(screen.getByRole("tab", { name: "Repository actions" }));
  await screen.findByRole("table", { name: "Repository activity references" });
  expect(exportRequest.signal.aborted).toBe(true);
  await act(async () => pending.resolve(reply(exportRequest)));
  expect(create).not.toHaveBeenCalled();
});
