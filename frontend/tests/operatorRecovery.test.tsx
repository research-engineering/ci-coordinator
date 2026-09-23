import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { admitWorkbenchScope, type WorkbenchScope } from "../src/api/workbench/client";
import { MAX_WORKBENCH_SECTION_ITEMS } from "../src/api/workbench/limits";
import {
  ApplicationErrorBoundary,
  reportRenderFailure,
} from "../src/components/ApplicationErrorBoundary";
import { IdentityDisclosure } from "../src/components/IdentityDisclosure";
import { shortIdentity } from "../src/domain/format";
import { ScopeForm } from "../src/features/workbench/ScopeForm";

const SCOPE = { installationId: 1, repositoryId: 2, limit: 10 } as const;
const FIELDS = ["installationId", "repositoryId", "limit"] as const;

afterEach(() => vi.unstubAllGlobals());

test.each(FIELDS)("rejects every invalid numeric boundary for %s", (field) => {
  for (const value of [0, -1, 1.5, Number.NaN, Infinity, -Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    expect(admitWorkbenchScope({ ...SCOPE, [field]: value }), `${field}=${value}`).toBeUndefined();
  }
});

test.each([1, MAX_WORKBENCH_SECTION_ITEMS])(
  "preserves exact admitted scope at limit %s",
  (limit) => {
    const candidate = {
      installationId: Number.MAX_SAFE_INTEGER,
      repositoryId: Number.MAX_SAFE_INTEGER,
      limit,
      localNote: "not a request field",
    };
    expect(admitWorkbenchScope(candidate)).toBe(candidate);
  },
);

test("rejects a safe integer beyond the section limit", () => {
  expect(admitWorkbenchScope({ ...SCOPE, limit: MAX_WORKBENCH_SECTION_ITEMS + 1 })).toBeUndefined();
});

test.each([
  ["Installation", "installationId", "0"],
  ["Repository", "repositoryId", "1.5"],
  ["Items per section", "limit", String(MAX_WORKBENCH_SECTION_ITEMS + 1)],
] as const)(
  "associates and clears the %s error without submitting",
  async (label, field, invalid) => {
    const submit = vi.fn<(scope: WorkbenchScope) => void>();
    const user = userEvent.setup();
    render(<ScopeForm initialScope={SCOPE} onSubmit={submit} />);
    const announcement = screen.getByRole("alert");
    expect(announcement).toHaveTextContent(/^$/);
    const input = screen.getByRole("spinbutton", { name: label });

    await user.clear(input);
    await user.type(input, invalid);
    await user.click(screen.getByRole("button", { name: "Load snapshot" }));

    expect(screen.getByRole("alert")).toBe(announcement);
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(
      `${label}: enter a whole number between 1 and ${field === "limit" ? MAX_WORKBENCH_SECTION_ITEMS : Number.MAX_SAFE_INTEGER}.`,
    );
    expect(submit).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, String(SCOPE[field]));
    expect(input).toHaveAttribute("aria-invalid", "false");
    expect(input).toHaveAccessibleDescription("");
    expect(submit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Load snapshot" }));
    expect(submit).toHaveBeenCalledExactlyOnceWith(SCOPE);
  },
);

test("reports every invalid field and preserves the empty-input rejection", async () => {
  const submit = vi.fn();
  const user = userEvent.setup();
  render(<ScopeForm initialScope={SCOPE} onSubmit={submit} />);
  for (const input of screen.getAllByRole("spinbutton")) await user.clear(input);
  await user.click(screen.getByRole("button", { name: "Load snapshot" }));
  for (const input of screen.getAllByRole("spinbutton")) {
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/enter a whole number/);
  }
  expect(submit).not.toHaveBeenCalled();
});

test("contains a descendant rendering failure without exposing or replaying it", () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  function BrokenView(): never {
    throw new Error("private-credential-and-provider-payload");
  }
  render(
    <ApplicationErrorBoundary>
      <BrokenView />
    </ApplicationErrorBoundary>,
    { onCaughtError: reportRenderFailure },
  );
  expect(screen.getByRole("heading", { name: "Console unavailable" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Refresh console" })).toBeEnabled();
  expect(screen.queryByText(/private-credential/)).not.toBeInTheDocument();
  expect(log).toHaveBeenCalledExactlyOnceWith("Operator console rendering failed.");
  expect(fetch).not.toHaveBeenCalled();
});

test("leaves healthy descendants unchanged", () => {
  render(
    <ApplicationErrorBoundary>
      <h1>Repository portfolio</h1>
    </ApplicationErrorBoundary>,
  );
  expect(screen.getByRole("heading", { name: "Repository portfolio" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Refresh console" })).not.toBeInTheDocument();
});

test.each(["small", "1234567890123456"])(
  "does not add disclosure to a short identifier %s",
  (value) => {
    const { container } = render(<IdentityDisclosure value={value} />);
    expect(screen.getByText(value)).toBeVisible();
    expect(container.querySelector("details")).toBeNull();
  },
);

test("discloses the exact value without changing its compact identity", async () => {
  const value = `audit_${"1234567890abcdef".repeat(4)}`;
  render(<IdentityDisclosure value={value} />);
  const summary = screen.getByText(shortIdentity(value));
  const full = screen.getByText(value);
  expect(full).not.toBeVisible();
  await userEvent.click(summary);
  expect(full).toBeVisible();
  expect(full.textContent).toBe(value);
  await userEvent.click(summary);
  expect(full).not.toBeVisible();
});
