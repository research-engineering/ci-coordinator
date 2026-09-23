import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { IdentityDisclosure } from "../src/components/IdentityDisclosure";
import { StatePanel } from "../src/components/StatePanel";
import { shortIdentity } from "../src/domain/format";
import { EconomicsError } from "../src/features/ciEconomics/EconomicsControls";
import { WorkbenchSummary } from "../src/features/workbench/WorkbenchSummary";
import { workbenchFixture } from "./fixture";

test.each(["ready", "denied"] as const)("copies the full identity: %s", async (outcome) => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  if (outcome === "ready") write.mockResolvedValue();
  else write.mockRejectedValue(new Error("Clipboard denied"));
  const value = "abcdef0123456789".repeat(4);
  render(<IdentityDisclosure value={value} />);
  await user.click(screen.getByRole("button", { name: `Copy identifier ${shortIdentity(value)}` }));
  expect(write).toHaveBeenCalledExactlyOnceWith(value);
  expect(await screen.findByRole("status")).toHaveTextContent(
    outcome === "ready" ? "Copied" : "Copy unavailable. Select the full identifier.",
  );
  await user.click(screen.getByLabelText(`Inspect identifier ${shortIdentity(value)}`));
  expect(screen.getByText(value, { exact: true })).toBeVisible();
});

test("an old clipboard result cannot label a new identity copied", async () => {
  const user = userEvent.setup();
  const pending = Promise.withResolvers<void>();
  vi.spyOn(navigator.clipboard, "writeText").mockReturnValue(pending.promise);
  const view = render(<IdentityDisclosure value="first-identity" />);
  await user.click(screen.getByRole("button", { name: "Copy identifier first-identity" }));
  expect(screen.getByRole("button")).toBeDisabled();
  view.rerender(<IdentityDisclosure value="second-identity" />);
  await act(async () => pending.resolve());
  expect(screen.getByRole("status")).toBeEmptyDOMElement();
  expect(screen.getByRole("button", { name: "Copy identifier second-identity" })).toBeEnabled();
});

test("ledger revisions are identifiers, not grouped quantities", () => {
  render(<WorkbenchSummary snapshot={workbenchFixture({ ledgerRevision: 1284 })} />);
  expect(screen.getByText("1284")).toBeVisible();
  expect(screen.queryByText("1,284")).not.toBeInTheDocument();
});

test("unvalidated repository data offers recovery without claiming success", async () => {
  const retry = vi.fn();
  render(<StatePanel kind="invalid-response" onRetry={retry} />);
  expect(screen.getByRole("heading", { name: "Response rejected" })).toBeVisible();
  expect(screen.getByText(/The repository data could not be validated/)).toHaveTextContent(
    "contact an administrator",
  );
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledTimes(1);
});

test.each([
  ["not-found", "Run unavailable for registration"],
  ["network-failure", "Registration could not be confirmed"],
  ["invalid-response", "Registration response could not be validated"],
] as const)(
  "registration keeps an uncertain %s outcome separate from reading",
  async (kind, label) => {
    const retry = vi.fn();
    render(<EconomicsError failure={{ kind }} operation="register" onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent(label);
    expect(screen.queryByText("Evidence is not retained")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(retry).toHaveBeenCalledTimes(1));
  },
);
