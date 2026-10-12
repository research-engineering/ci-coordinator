import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import {
  type WorkflowDiscoveryReport,
  workflowDiscoverySchema,
} from "../src/api/workflowDiscovery/schema";
import { WorkflowDiscoveryInventory } from "../src/features/workbench/WorkflowDiscoveryInventory";
import { workflowDiscoveryFixture } from "./fixture";

type Assessment = WorkflowDiscoveryReport["adoptionAssessments"][number];

function renderWithAssessment(change: (assessment: Assessment) => Assessment = (item) => item) {
  const value = workflowDiscoveryFixture();
  const report = workflowDiscoverySchema.parse({
    ...value,
    adoptionAssessments: value.adoptionAssessments.map(change),
  });
  render(<WorkflowDiscoveryInventory report={report} />);
  const table = screen.getAllByRole("table")[0];
  if (!table) throw new Error("adoption table is missing");
  return table;
}

test("adoption readiness explains its support profile, consumer changes and workflow unknowns", async () => {
  const user = userEvent.setup();
  const table = renderWithAssessment();

  expect(within(table).getByText("github.com/github-actions-static/v5")).toBeVisible();
  expect(
    within(table).getByText("Declare this workflow in the target execution registry"),
  ).toBeVisible();
  expect(
    within(table).getByText("Add invocation classifier job ci-invocation with needs: none"),
  ).toBeVisible();
  expect(
    within(table).getByText(
      "Add an owner-named aggregate gate job with needs: test, the owner-named plan job",
    ),
  ).toBeVisible();
  expect(
    within(table).getByText(
      "Add an owner-named plan job with needs: the owner-named plan request job",
    ),
  ).toBeVisible();
  expect(
    within(table).getByText(
      "Register job test as an execution job with needs: the owner-named plan job",
    ),
  ).toBeVisible();
  expect(
    within(table).getByText("Set the owner-named aggregate gate job condition to always()"),
  ).toBeVisible();
  expect(within(table).queryByText(/owner-named invocation classifier/)).toBeNull();

  const unknowns = within(table).getByText("1 unknown");
  expect(within(table).getByText(/requires provider state/)).not.toBeVisible();
  await user.click(unknowns);
  expect(within(table).getByText("job.permissions.effective")).toBeVisible();
  expect(within(table).getByText(/requires provider state/)).toBeVisible();
});

test("an unadmitted support profile renders full validation only without consumer changes", () => {
  const table = renderWithAssessment((assessment) => ({
    ...assessment,
    blockers: ["support_profile_unknown"],
    requiredChanges: [],
    supportProfileId: null,
  }));

  expect(within(table).getByText("unadmitted: full validation only")).toBeVisible();
  expect(within(table).queryByText(/target execution registry/)).toBeNull();
  expect(within(table).getByText("support profile unknown")).toBeVisible();
});

test("required changes render repository-provided identities as inert text", () => {
  const hostile = "<img src=x onerror=alert(1)>";
  const table = renderWithAssessment((assessment) => ({
    ...assessment,
    blockers: ["registered_gate_signal_mismatch"],
    requiredChanges: [
      {
        action: "set_job_name",
        blocker: "registered_gate_signal_mismatch",
        expectedValue: hostile,
        jobId: "gate",
        needs: null,
        needsControlRoles: [],
        role: "gate",
      },
    ],
  }));

  expect(within(table).getByText(`Set aggregate gate job gate name to ${hostile}`)).toBeVisible();
  expect(table.querySelector("img")).toBeNull();
});
