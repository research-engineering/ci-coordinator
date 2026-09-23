import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { RepositoryCatalog } from "../src/features/workbench/RepositoryCatalog";
import {
  installationCatalogFixture,
  installationFixture,
  repositoryFixture,
  repositoryPageFixture,
} from "./fixture";

afterEach(() => vi.unstubAllGlobals());

function renderCatalog(handler: (request: Request) => Promise<Response>) {
  const fetch = vi.fn(handler);
  vi.stubGlobal("fetch", fetch);
  render(<RepositoryCatalog itemLimit={10} onSelect={vi.fn()} />);
  return fetch;
}

test.each([
  [false, false],
  [true, false],
  [false, true],
  [true, true],
])(
  "first-page controls use independent continuations: org=%s repo=%s",
  async (orgNext, repoNext) => {
    const fetch = renderCatalog(async (request) => {
      const url = new URL(request.url);
      return Response.json(
        url.pathname.endsWith("/installations")
          ? installationCatalogFixture({ hasNextPage: orgNext })
          : repositoryPageFixture({ hasNextPage: repoNext, totalCount: repoNext ? 101 : 1 }),
      );
    });
    expect(await screen.findByText("ci-coordinator")).toBeVisible();
    for (const [name, visible] of [
      ["Organization", orgNext],
      ["Repository", repoNext],
    ] as const) {
      const nav = screen.queryByRole("navigation", { name: `${name} pages` });
      if (visible) {
        expect(nav).toBeVisible();
        if (!nav) throw new Error("Expected contextual pagination");
        expect(within(nav).getByRole("button", { name: /Previous/ })).toBeDisabled();
        expect(within(nav).getByRole("button", { name: /Next/ })).toBeEnabled();
        expect(nav).toHaveTextContent("Page 1");
      } else {
        expect(nav).not.toBeInTheDocument();
      }
    }
    const user = userEvent.setup();
    await user.type(screen.getByRole("searchbox"), "not-on-this-page");
    expect(screen.getByText("No matching repositories on this page.")).toBeVisible();
    expect(Boolean(screen.queryByRole("navigation", { name: "Organization pages" }))).toBe(orgNext);
    for (const kind of ["organization", "repository"] as const) {
      if (kind === "organization" ? orgNext : repoNext) {
        expect(screen.getByRole("button", { name: `Previous ${kind} page` })).toBeDisabled();
        expect(screen.getByRole("button", { name: `Next ${kind} page` })).toBeEnabled();
      }
    }
    expect(Boolean(screen.queryByRole("navigation", { name: "Repository pages" }))).toBe(repoNext);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(
      fetch.mock.calls.map(([request]) => new URL(request.url).searchParams.get("page")),
    ).toEqual(["1", "1"]);
  },
);

test.each([
  ["organization", true],
  ["repository", true],
  ["organization", false],
] as const)(
  "%s traversal preserves back paths without prefetch; empty last page=%s",
  async (kind, emptyLast) => {
    const pages: number[] = [];
    const fetch = renderCatalog(async (request) => {
      const url = new URL(request.url);
      const page = Number(url.searchParams.get("page"));
      const organization = url.pathname.endsWith("/installations");
      if (organization === (kind === "organization")) pages.push(page);
      return Response.json(
        organization
          ? installationCatalogFixture({
              page,
              hasNextPage: kind === "organization" && page < 3,
              installations: page === 3 && emptyLast ? [] : [installationFixture()],
            })
          : repositoryPageFixture({
              page,
              hasNextPage: kind === "repository" && page < 3,
              totalCount: kind === "repository" ? (page === 3 ? 200 : 201) : 1,
              ...(page === 3 ? { repositories: [] } : {}),
            }),
      );
    });
    const user = userEvent.setup();
    const name = kind === "organization" ? "Organization pages" : "Repository pages";
    const next = () => screen.getByRole("button", { name: `Next ${kind} page` });
    const previous = () => screen.getByRole("button", { name: `Previous ${kind} page` });
    expect(await screen.findByText("ci-coordinator")).toBeVisible();
    expect(pages).toEqual([1]);
    await user.click(next());
    await waitFor(() =>
      expect(screen.getByRole("navigation", { name })).toHaveTextContent("Page 2"),
    );
    expect(previous()).toBeEnabled();
    expect(next()).toBeEnabled();
    expect(pages).toEqual([1, 2]);
    await user.click(next());
    await waitFor(() =>
      expect(screen.getByRole("navigation", { name })).toHaveTextContent("Page 3"),
    );
    if (!emptyLast) {
      await user.type(screen.getByRole("searchbox"), "not-on-this-page");
      expect(screen.getByRole("searchbox")).toHaveValue("not-on-this-page");
    }
    expect(previous()).toBeEnabled();
    expect(next()).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Open" })).not.toBeInTheDocument();
    if (kind === "organization" && emptyLast) {
      expect(screen.getByRole("combobox", { name: "Organization" })).toBeDisabled();
    } else if (kind === "repository") {
      expect(screen.getByText("No repositories.")).toBeVisible();
    }
    const reads = fetch.mock.calls.length;
    await user.click(next());
    expect(fetch).toHaveBeenCalledTimes(reads);
    await user.click(previous());
    await waitFor(() =>
      expect(screen.getByRole("navigation", { name })).toHaveTextContent("Page 2"),
    );
    expect(pages).toEqual([1, 2, 3, 2]);
    await user.click(previous());
    await waitFor(() =>
      expect(screen.getByRole("navigation", { name })).toHaveTextContent("Page 1"),
    );
    expect(previous()).toBeDisabled();
    expect(pages).toEqual([1, 2, 3, 2, 1]);
  },
);

test.each([false, true])(
  "single-page empty organizations preserve incomplete=%s",
  async (partial) => {
    const fetch = renderCatalog(async () =>
      Response.json(
        installationCatalogFixture({
          installations: [],
          complete: !partial,
          failures: partial
            ? [{ installationId: 1, reason: "unavailable", retryAfterSeconds: null }]
            : [],
        }),
      ),
    );
    expect(await screen.findByRole("combobox", { name: "Organization" })).toBeDisabled();
    expect(screen.queryByRole("navigation", { name: /pages/ })).not.toBeInTheDocument();
    if (partial) {
      expect(screen.getByText("Partial catalog: 1 installation unavailable.")).toBeVisible();
      expect(screen.getByText("Authorized organizations unavailable")).toBeVisible();
    } else {
      expect(screen.queryByText(/Partial catalog/)).not.toBeInTheDocument();
    }
    expect(fetch).toHaveBeenCalledTimes(1);
  },
);

test("partial first-page organizations without options retain continuation", async () => {
  renderCatalog(async () =>
    Response.json(
      installationCatalogFixture({
        installations: [],
        complete: false,
        hasNextPage: true,
        failures: [{ installationId: 1, reason: "rate_limited", retryAfterSeconds: 45 }],
      }),
    ),
  );
  expect(await screen.findByRole("combobox", { name: "Organization" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Next organization page" })).toBeEnabled();
  expect(
    screen.getByText("Installation 1: GitHub rate limited the request; retry after 45 seconds"),
  ).toBeVisible();
  expect(screen.queryByRole("navigation", { name: "Repository pages" })).not.toBeInTheDocument();
});

test("refresh retains an active selection, repository page and search; selection replacement resets them", async () => {
  const other = installationFixture({ installationId: 2, accountLogin: "other-org" });
  let includeOther = true;
  const repositoryReads: string[] = [];
  renderCatalog(async (request) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/installations")) {
      return Response.json(
        installationCatalogFixture({
          installations: includeOther ? [installationFixture(), other] : [installationFixture()],
        }),
      );
    }
    repositoryReads.push(`${url.pathname}?${url.searchParams}`);
    const page = Number(url.searchParams.get("page"));
    const isOther = url.pathname.includes("/2/repositories");
    return Response.json(
      repositoryPageFixture({
        installation: isOther ? other : installationFixture(),
        page,
        hasNextPage: isOther && page === 1,
        repositories: isOther
          ? [
              repositoryFixture({
                scope: { installationId: 2, repositoryId: page },
                ownerLogin: other.accountLogin,
                name: "other-repo",
                fullName: `${other.accountLogin}/other-repo`,
              }),
            ]
          : [],
        totalCount: isOther ? 101 : 0,
      }),
    );
  });
  const user = userEvent.setup();
  const select = await screen.findByRole("combobox", { name: "Organization" });
  await user.selectOptions(select, "2");
  expect(await screen.findByText("other-repo")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Next repository page" }));
  await waitFor(() =>
    expect(screen.getByRole("navigation", { name: "Repository pages" })).toHaveTextContent(
      "Page 2",
    ),
  );
  await user.type(screen.getByRole("searchbox"), "kept");
  await user.click(screen.getByRole("button", { name: "Refresh repositories" }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Organization" })).toHaveValue("2"),
  );
  expect(await screen.findByText("No matching repositories on this page.")).toBeVisible();
  expect(screen.getByRole("searchbox")).toHaveValue("kept");
  expect(screen.getByRole("navigation", { name: "Repository pages" })).toHaveTextContent("Page 2");
  expect(repositoryReads.at(-1)).toBe(
    "/api/v1/workbench/installations/2/repositories?page=2&perPage=100",
  );
  includeOther = false;
  await user.click(screen.getByRole("button", { name: "Refresh repositories" }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Organization" })).toHaveValue("1"),
  );
  expect(await screen.findByText("No repositories.")).toBeVisible();
  expect(screen.getByRole("searchbox")).toHaveValue("");
  expect(repositoryReads.at(-1)).toBe(
    "/api/v1/workbench/installations/1/repositories?page=1&perPage=100",
  );
});

test.each(["organization", "repository"] as const)(
  "%s loading and failure hide only unavailable controls, and explicit retry keeps the requested page",
  async (kind) => {
    const pending = Promise.withResolvers<Response>();
    let recover = false;
    const pages: number[] = [];
    renderCatalog(async (request) => {
      const url = new URL(request.url);
      const page = Number(url.searchParams.get("page"));
      const organization = url.pathname.endsWith("/installations");
      if (organization === (kind === "organization")) {
        pages.push(page);
        if (page === 2 && !recover) return pending.promise;
      }
      return Response.json(
        organization
          ? installationCatalogFixture({ page, hasNextPage: page === 1 })
          : repositoryPageFixture({ page, hasNextPage: page === 1, totalCount: 101 }),
      );
    });
    const user = userEvent.setup();
    const name = kind === "organization" ? "Organization pages" : "Repository pages";
    expect(await screen.findByText("ci-coordinator")).toBeVisible();
    await user.click(screen.getByRole("button", { name: `Next ${kind} page` }));
    expect(
      screen.getByRole("progressbar", {
        name: `Loading ${kind === "organization" ? "organizations" : "repositories"}`,
      }),
    ).toBeVisible();
    expect(screen.queryByRole("navigation", { name })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open" })).not.toBeInTheDocument();
    await act(async () =>
      pending.resolve(
        Response.json(
          {
            error: "rate_limited",
            ok: false,
            retryAfterSeconds: 30,
          },
          { status: 429 },
        ),
      ),
    );
    expect(
      await screen.findByText("GitHub rate limit reached. Retry after 30 seconds."),
    ).toBeVisible();
    expect(screen.queryByRole("navigation", { name })).not.toBeInTheDocument();
    expect(pages).toEqual([1, 2]);
    recover = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.getByRole("navigation", { name })).toHaveTextContent("Page 2"),
    );
    expect(screen.getByRole("button", { name: `Previous ${kind} page` })).toBeEnabled();
    expect(screen.getByRole("button", { name: `Next ${kind} page` })).toBeDisabled();
    expect(pages).toEqual([1, 2, 2]);
  },
);
