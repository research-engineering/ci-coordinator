import { webcrypto } from "node:crypto";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ObservationCommand } from "../src/api/ciEconomics/observationSchema";
import { ObservationEditor } from "../src/features/ciEconomics/ObservationEditor";
import { controlPlaneSessionFixture } from "./fixture";
import {
  OBSERVATION_CONFIG,
  OBSERVATION_SCOPE,
  observationMutation,
  observationSnapshot,
  observationWorkflows,
} from "./observationFixture";

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());
const session = controlPlaneSessionFixture({ roles: ["audit", "configure", "read"] });
const props = {
  scope: OBSERVATION_SCOPE,
  snapshot: observationSnapshot(),
  session,
  stale: false,
  onSaved: vi.fn(),
};

test("a bounded exact command enables observation, then pauses it without deleting history", async () => {
  const commands: ObservationCommand[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const command: ObservationCommand = await request.json();
      commands.push(command);
      expect(request.headers.get("x-csrf-token")).toBe(session.csrfToken);
      return Response.json(observationMutation(command));
    }),
  );
  render(<ObservationEditor {...props} snapshot={null} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Observation enabled" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Initial history" }), "6");
  await userEvent.click(screen.getByRole("button", { name: "Save observation" }));
  await screen.findByText("Saved revision 1");
  await userEvent.click(screen.getByRole("checkbox", { name: "Observation enabled" }));
  await userEvent.click(screen.getByRole("button", { name: "Save observation" }));
  await screen.findByText("Saved revision 2");
  expect(commands.map((command) => command.expectedRevision)).toEqual([0, 1]);
  expect(commands.map((command) => command.configuration.enabled)).toEqual([true, false]);
  expect(commands[0]?.configuration.backfillDays).toBe(6);
  expect(commands[0]).not.toHaveProperty("limit");
  expect(commands[0]?.operationId).not.toBe(commands[1]?.operationId);
});
test("polling neither overwrites a draft nor silently rebases its expected revision", async () => {
  const view = render(<ObservationEditor {...props} />);
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Initial history" }), "4");
  view.rerender(
    <ObservationEditor {...props} snapshot={observationSnapshot(OBSERVATION_CONFIG, 2)} />,
  );
  expect(screen.getByRole("combobox", { name: "Initial history" })).toHaveValue("4");
  expect(screen.getByRole("button", { name: "Save observation" })).toBeDisabled();
  expect(screen.getByText("Saved revision 1")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Reload saved configuration" }));
  expect(screen.getByRole("combobox", { name: "Initial history" })).toHaveValue("1");
  expect(screen.getByText("Saved revision 2")).toBeVisible();
});
test("lost reply retains an immutable retry command and prevents duplicate in-flight writes", async () => {
  const response = Promise.withResolvers<Response>();
  const commands: ObservationCommand[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const command: ObservationCommand = await request.json();
      commands.push(command);
      return commands.length === 1
        ? response.promise
        : Response.json({ ...observationMutation(command), outcome: "replayed" });
    }),
  );
  render(<ObservationEditor {...props} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Observation enabled" }));
  fireEvent.submit(screen.getByRole("form", { name: "Observation configuration" }));
  fireEvent.submit(screen.getByRole("form", { name: "Observation configuration" }));
  await waitFor(() => expect(commands).toHaveLength(1));
  expect(screen.getByRole("checkbox", { name: "Observation enabled" })).toBeDisabled();
  await act(async () => response.reject(new TypeError("offline")));
  await screen.findByText(/Save outcome unknown/);
  expect(screen.getByRole("button", { name: "Save observation" })).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "Reload saved configuration" }),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Retry same operation" }));
  await screen.findByText("Saved revision 2");
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
});
test.each(["revision_conflict", "operation_conflict", "capacity_reached"])(
  "a rejected %s write cannot repeat without explicit reload",
  async (outcome) => {
    const fetch = vi.fn(async (request: Request) => {
      const command: ObservationCommand = await request.json();
      return Response.json(
        { ...observationMutation(command), outcome, snapshot: null },
        { status: 409 },
      );
    });
    vi.stubGlobal("fetch", fetch);
    render(<ObservationEditor {...props} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Observation enabled" }));
    await userEvent.click(screen.getByRole("button", { name: "Save observation" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Save observation" })).toBeDisabled();
    expect(fetch).toHaveBeenCalledTimes(1);
  },
);
test("selected provider IDs are retained even when absent from the current page", async () => {
  const commands: ObservationCommand[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "GET") return Response.json(observationWorkflows());
      const command: ObservationCommand = await request.json();
      commands.push(command);
      return Response.json(observationMutation(command));
    }),
  );
  render(
    <ObservationEditor
      {...props}
      snapshot={observationSnapshot({
        ...OBSERVATION_CONFIG,
        selector: { kind: "selected", workflowIds: [999] },
      })}
    />,
  );
  await userEvent.click(await screen.findByRole("checkbox", { name: /Full Check/ }));
  await userEvent.click(screen.getByText("Selected workflow IDs (2)"));
  expect(screen.getByText("Workflow #999")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Save observation" }));
  await waitFor(() => expect(commands).toHaveLength(1));
  expect(commands[0]?.configuration.selector.workflowIds).toEqual([999, 101]);
  await screen.findByText("Saved revision 2");
});
test.each(["read-only", "stale"])("%s state cannot submit configuration", async (kind) => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  render(
    <ObservationEditor
      {...props}
      stale={kind === "stale"}
      session={kind === "read-only" ? controlPlaneSessionFixture({ roles: ["audit"] }) : session}
    />,
  );
  expect(screen.getByRole("checkbox", { name: "Observation enabled" })).toBeDisabled();
  fireEvent.submit(screen.getByRole("form", { name: "Observation configuration" }));
  expect(fetch).not.toHaveBeenCalled();
});
