import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import {
  projectWorkflowRoles,
  type WorkflowRoleCallEdge,
  type WorkflowRoleInput,
} from "../src/api/workflowDiscovery/workflowRoles";
import { WorkflowRoleInventory } from "../src/features/workbench/WorkflowRoleInventory";

const DIR = ".github/workflows";

function workflow(name: string, triggers: readonly string[] | null) {
  return { name, path: `${DIR}/${name}.yml`, triggers };
}

function localCall(
  caller: string,
  target: string,
  status: WorkflowRoleCallEdge["status"] = "resolved",
): WorkflowRoleCallEdge {
  return {
    callerJobId: `call-${target}`,
    callerWorkflowPath: `${DIR}/${caller}.yml`,
    edgeId: `edge:${caller}-${target}`,
    kind: "local",
    remoteRef: null,
    status,
    targetPath: `${DIR}/${target}.yml`,
    uses: `./${DIR}/${target}.yml`,
  };
}

function remoteCall(caller: string): WorkflowRoleCallEdge {
  return {
    callerJobId: "remote",
    callerWorkflowPath: `${DIR}/${caller}.yml`,
    edgeId: `edge:${caller}-remote`,
    kind: "remote",
    remoteRef: "main",
    status: "remote",
    targetPath: null,
    uses: "example/shared/.github/workflows/python.yml@main",
  };
}

function input(
  workflows: WorkflowRoleInput["workflows"],
  callEdges: readonly WorkflowRoleCallEdge[],
  unparsed: readonly string[] = [],
): WorkflowRoleInput {
  return {
    callEdges,
    sources: [
      ...workflows.map((item) => ({ path: item.path })),
      ...unparsed.map((name) => ({ path: `${DIR}/${name}.yml` })),
    ],
    workflows,
  };
}

const paths = (rows: readonly { readonly path: string }[]) => rows.map((row) => row.path);

test("a workflow with standalone triggers and workflow_call holds both roles", () => {
  // Kills a mutant that assigns exactly one role per workflow.
  const roles = projectWorkflowRoles(
    input([workflow("api-contract", ["workflow_call", "workflow_dispatch"])], []),
  );

  expect(paths(roles.entrypoints)).toEqual([`${DIR}/api-contract.yml`]);
  expect(paths(roles.components)).toEqual([`${DIR}/api-contract.yml`]);
  expect(roles.unclassified).toEqual([]);
});

test("a shared callee lists every caller instead of one invented parent", () => {
  // Kills a mutant that keeps only the first caller of a component.
  const roles = projectWorkflowRoles(
    input(
      [
        workflow("ci", ["push"]),
        workflow("nightly", ["schedule"]),
        workflow("lint", ["workflow_call"]),
      ],
      [localCall("ci", "lint"), localCall("nightly", "lint")],
    ),
  );

  expect(paths(roles.components)).toEqual([`${DIR}/lint.yml`]);
  expect(roles.components[0]?.callers.map((edge) => edge.callerWorkflowPath)).toEqual([
    `${DIR}/ci.yml`,
    `${DIR}/nightly.yml`,
  ]);
  expect(paths(roles.entrypoints)).toEqual([`${DIR}/ci.yml`, `${DIR}/nightly.yml`]);
});

test("unknown triggers and unparsed sources stay unknown instead of becoming a role", () => {
  // Kills mutants that default unknown triggers to an entrypoint or drop unparsed sources.
  const roles = projectWorkflowRoles(
    input(
      [workflow("dynamic", null), workflow("empty", [])],
      [localCall("dynamic", "broken", "missing")],
      ["broken"],
    ),
  );

  expect(roles.entrypoints).toEqual([]);
  expect(roles.components).toEqual([]);
  expect(roles.unclassified.map((row) => [row.path, row.unclassifiedReason])).toEqual([
    [`${DIR}/dynamic.yml`, "triggers_unknown"],
    [`${DIR}/empty.yml`, "no_triggers"],
    [`${DIR}/broken.yml`, "source_unparsed"],
  ]);
  expect(roles.unclassified[2]?.callers.map((edge) => edge.status)).toEqual(["missing"]);
});

test("roles never derive from underscores or filenames", () => {
  // Kills a mutant that classifies `_`-prefixed files as components.
  const roles = projectWorkflowRoles(
    input([workflow("_release", ["push"]), workflow("ci", ["workflow_call"])], []),
  );

  expect(paths(roles.entrypoints)).toEqual([`${DIR}/_release.yml`]);
  expect(paths(roles.components)).toEqual([`${DIR}/ci.yml`]);
});

test("entrypoint expansion follows resolved calls once and keeps every unresolved edge", () => {
  // Kills mutants that hide cycle/remote/missing edges, stop after one level, or
  // repeat a shared callee reached through two paths.
  const roles = projectWorkflowRoles(
    input(
      [
        workflow("ci", ["pull_request"]),
        workflow("a", ["workflow_call"]),
        workflow("b", ["workflow_call"]),
        workflow("shared", ["workflow_call"]),
        workflow("loop", ["workflow_call"]),
      ],
      [
        localCall("ci", "a"),
        localCall("ci", "b"),
        localCall("a", "shared"),
        localCall("b", "shared"),
        localCall("shared", "loop", "cycle"),
        localCall("loop", "shared", "cycle"),
        localCall("a", "gone", "missing"),
        remoteCall("shared"),
      ],
    ),
  );

  const calls = roles.entrypoints[0]?.reachableCalls.map((edge) => edge.edgeId);
  expect(calls).toEqual([
    "edge:ci-a",
    "edge:ci-b",
    "edge:a-shared",
    "edge:a-gone",
    "edge:b-shared",
    "edge:shared-loop",
    "edge:shared-remote",
  ]);
  expect(new Set(calls).size).toBe(calls?.length);
});

test("the role inventory renders entrypoints, components and unknown roles as tables", async () => {
  const user = userEvent.setup();
  render(
    <WorkflowRoleInventory
      report={input(
        [
          workflow("ci", ["push", "workflow_call"]),
          workflow("lint", ["workflow_call"]),
          workflow("odd", null),
        ],
        [localCall("ci", "lint"), remoteCall("lint")],
      )}
    />,
  );

  const tables = screen.getAllByRole("table");
  expect(tables).toHaveLength(3);
  const [entrypoints, components, unknown] = tables;
  if (!entrypoints || !components || !unknown) throw new Error("role tables are missing");
  expect(within(entrypoints).getByText(`${DIR}/ci.yml`)).toBeVisible();
  expect(within(components).getByText(`${DIR}/ci.yml`)).toBeVisible();
  expect(within(components).getByText(`${DIR}/lint.yml`)).toBeVisible();
  expect(within(unknown).getByText("Triggers unknown")).toBeVisible();

  const expansion = within(entrypoints).getByText("2 calls");
  await user.click(expansion);
  expect(
    within(entrypoints).getByText("example/shared/.github/workflows/python.yml@main"),
  ).toBeVisible();
  expect(within(entrypoints).getByText("remote")).toBeVisible();
});
