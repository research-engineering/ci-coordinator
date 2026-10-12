import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { ComponentPassportInventory } from "../src/features/workbench/ComponentPassportInventory";
import { workflowDiscoveryFixture } from "./fixture";

type Passport = ReturnType<typeof workflowDiscoveryFixture>["componentPassport"];
type Reference = Passport["references"][number];

const CALLER = ".github/workflows/ci.yml";

function reference(overrides: Partial<Reference> & Pick<Reference, "referenceId" | "uses">) {
  return {
    callerJobId: "test",
    callerWorkflowPath: CALLER,
    digest: null,
    immutable: false,
    kind: "action",
    path: null,
    ref: null,
    repository: null,
    resolution: "unresolved",
    resolvedAt: null,
    resolvedBlobSha: null,
    resolvedRevision: null,
    stepIndex: 0,
    unresolvedReason: "resolution_not_requested",
    ...overrides,
  } satisfies Reference;
}

function passport(overrides: Partial<Passport>): Passport {
  return { ...workflowDiscoveryFixture().componentPassport, ...overrides };
}

test("the dependency passport separates observed, denied and unresolved references", () => {
  render(
    <ComponentPassportInventory
      passport={passport({
        references: [
          reference({
            kind: "reusable_workflow",
            ref: "main",
            referenceId: `component:${"1".repeat(32)}`,
            repository: "example-org/shared",
            resolution: "observed",
            resolvedAt: "2026-10-11T12:00:00Z",
            resolvedBlobSha: "b".repeat(40),
            resolvedRevision: "c".repeat(40),
            stepIndex: null,
            unresolvedReason: null,
            uses: "example-org/shared/.github/workflows/build.yml@main",
          }),
          reference({
            ref: "v1",
            referenceId: `component:${"2".repeat(32)}`,
            repository: "example-org/private",
            resolution: "denied",
            unresolvedReason: null,
            uses: "example-org/private@v1",
          }),
          reference({
            immutable: true,
            ref: "d".repeat(40),
            referenceId: `component:${"3".repeat(32)}`,
            repository: "actions/checkout",
            stepIndex: 1,
            unresolvedReason: "outside_caller_installation",
            uses: `actions/checkout@${"d".repeat(40)}`,
          }),
        ],
      })}
    />,
  );

  const table = screen.getByRole("table");
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(3);
  const [callRow, deniedRow, outsideRow] = rows;
  if (!callRow || !deniedRow || !outsideRow) throw new Error("passport rows are missing");
  expect(within(callRow).getByText("Observed")).toBeVisible();
  expect(within(callRow).getByText(`commit ${"c".repeat(12)}`)).toBeVisible();
  expect(within(callRow).getByText(`blob ${"b".repeat(12)}`)).toBeVisible();
  expect(within(callRow).getByText("Observed 11 Oct 2026, 12:00:00 UTC")).toBeVisible();
  expect(within(callRow).getByText("Mutable")).toBeVisible();
  expect(within(deniedRow).getByText("Access denied")).toBeVisible();
  expect(within(deniedRow).queryByText(/^commit /)).toBeNull();
  expect(within(outsideRow).getByText("Outside this installation")).toBeVisible();
  expect(within(outsideRow).getByText("Immutable")).toBeVisible();
  expect(screen.getByText("3 references, 1 observed")).toBeVisible();
  expect(screen.queryByRole("status")).toBeNull();
});

test("a same-commit callee is shown as bound by the caller commit", () => {
  render(
    <ComponentPassportInventory
      passport={passport({
        references: [
          reference({
            immutable: true,
            kind: "reusable_workflow",
            path: ".github/workflows/reusable.yml",
            referenceId: `component:${"4".repeat(32)}`,
            resolution: "observed",
            resolvedBlobSha: "e".repeat(40),
            resolvedRevision: "a".repeat(40),
            stepIndex: null,
            unresolvedReason: null,
            uses: "./.github/workflows/reusable.yml",
          }),
        ],
      })}
    />,
  );

  expect(screen.getByText("Bound by caller commit")).toBeVisible();
});

test("incomplete declarations never read as an empty dependency set", () => {
  render(<ComponentPassportInventory passport={passport({ declarationsComplete: false })} />);

  expect(screen.getByRole("status")).toHaveTextContent(
    "An empty or short list is not proof that the workflows have no other dependencies.",
  );
  expect(
    screen.getByText("No component references projected; declarations are incomplete"),
  ).toBeVisible();
  expect(screen.queryByText("No component references declared")).toBeNull();
});

test("omitted references are explicit and never read as an empty dependency set", () => {
  render(<ComponentPassportInventory passport={passport({ referencesOmitted: true })} />);

  expect(screen.getByRole("status")).toHaveTextContent(
    "This is not proof that the workflows have no dependencies.",
  );
  expect(screen.getByText("References omitted")).toBeVisible();
  expect(
    screen.getByText("Component references omitted; response size limit reached"),
  ).toBeVisible();
  expect(screen.queryByText("No component references declared")).toBeNull();
});
