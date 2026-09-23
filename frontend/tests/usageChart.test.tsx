import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { UsageChart, type UsagePoint } from "../src/features/ciEconomics/UsageChart";

const point = (label: string, observed: number | null): UsagePoint => ({ label, observed });

test.each([
  { name: "empty", points: [] },
  { name: "unknown", points: [point("2026-09-01", null)] },
])("does not turn missing observations into idle infrastructure: $name", ({ points }) => {
  render(<UsageChart title="Runner usage" unit="runner-minutes" points={points} />);
  expect(screen.getByRole("status")).toHaveTextContent("No measured usage");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("preserves measured zero and breaks the path across unknown days", async () => {
  const view = render(
    <UsageChart
      title="Runner usage"
      unit="runner-minutes"
      points={[
        point("2026-09-01", 0),
        point("2026-09-02", null),
        point("2026-09-03", 12),
        point("2026-09-04", 8),
      ]}
    />,
  );
  expect(screen.getByRole("img")).toHaveAccessibleName(/Runner usage/);
  const path = view.container.querySelector(".usage-observed")?.getAttribute("d");
  expect(path?.match(/M/g)).toHaveLength(2);
  expect(path?.match(/L/g)).toHaveLength(1);
  const markers = view.container.querySelectorAll(".usage-observed-point");
  expect(markers).toHaveLength(3);
  expect(Array.from(markers, (marker) => marker.textContent)).toEqual([
    "2026-09-01: 0 runner-minutes",
    "2026-09-03: 12 runner-minutes",
    "2026-09-04: 8 runner-minutes",
  ]);
  await userEvent.click(screen.getByText("Data table"));
  const rows = screen.getAllByRole("row");
  const viewport = screen.getByRole("region", { name: "Runner usage data table" });
  expect(viewport).toHaveAttribute("tabindex", "0");
  viewport.focus();
  expect(viewport).toHaveFocus();
  expect(within(rows[1] as HTMLElement).getByRole("cell")).toHaveTextContent(/^0$/);
  expect(within(rows[2] as HTMLElement).getByRole("cell")).toHaveTextContent("Not measured");
  expect(within(rows[3] as HTMLElement).getByRole("cell")).toHaveTextContent(/^12$/);
});

test.each([0, 12])("a singleton observation %s has a visible centered marker", (value) => {
  const view = render(
    <UsageChart title="Single" unit="jobs" points={[point("2026-09-10", value)]} />,
  );
  const marker = view.container.querySelector(".usage-observed-point");
  expect(marker).toHaveAttribute("cx", "500");
  expect(marker).toHaveAttribute("cy", value === 0 ? "200" : "0");
  expect(marker).toHaveAttribute("r", "2.5");
  expect(marker).toHaveTextContent(`2026-09-10: ${value} jobs`);
});

test("estimates and intervals are separate from observations", async () => {
  const view = render(
    <UsageChart
      title="Usage"
      unit="runner-minutes"
      points={[
        point("2026-09-01", 4),
        { label: "2026-09-02", observed: null, estimate: 5, lower: 2, upper: 8 },
        { label: "2026-09-03", observed: null, estimate: 6, lower: 3, upper: 9 },
      ]}
    />,
  );
  expect(screen.getByText("Estimate and uncertainty")).toBeVisible();
  expect(view.container.querySelector(".usage-estimate")?.getAttribute("d")).toContain("L");
  expect(view.container.querySelector(".usage-interval")?.getAttribute("d")).toContain("Z");
  await userEvent.click(screen.getByText("Data table"));
  expect(screen.getByRole("columnheader", { name: "Estimate" })).toBeVisible();
  expect(screen.getByRole("cell", { name: "2 - 8" })).toBeVisible();
  expect(screen.getAllByRole("cell", { name: "Not measured" })).toHaveLength(2);
});

test.each(
  [
    [point("negative", -1)],
    [point("not finite", Number.POSITIVE_INFINITY)],
    [point("not a number", Number.NaN)],
    [point("duplicate period", 1), point("duplicate period", 2)],
    [{ ...point("partial interval", null), estimate: 5, lower: 2 }],
    [{ ...point("contradictory interval", null), estimate: 5, lower: 6, upper: 8 }],
    [{ ...point("unbound interval", null), lower: 2, upper: 8 }],
    Array.from({ length: 397 }, (_, index) => point(String(index), 1)),
  ].map((points) => ({ points, name: points.length === 397 ? "oversized" : points[0]?.label })),
)("rejects invalid geometry without drawing a misleading series: $name", ({ points }) => {
  render(<UsageChart title="Usage" unit="runner-minutes" points={points} />);
  expect(screen.getByRole("status")).toHaveTextContent("cannot be displayed");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("keeps the table bounded and never interprets provider labels as markup", async () => {
  const label = '<img src=x alt="" onerror="alert(1)">';
  const points = Array.from({ length: 32 }, (_, index) =>
    point(index === 0 ? label : `Day ${index + 1}`, index),
  );
  const view = render(<UsageChart title="Usage" unit="runner-minutes" points={points} />);
  await userEvent.click(screen.getByText("Data table"));
  expect(screen.getAllByRole("row")).toHaveLength(32);
  expect(screen.getByRole("rowheader", { name: label })).toBeVisible();
  expect(view.container.querySelector("img")).toBeNull();
  expect(screen.getByRole("button", { name: "Previous data page" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Next data page" }));
  expect(screen.getAllByRole("row")).toHaveLength(2);
  expect(screen.getByRole("rowheader", { name: "Day 32" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Next data page" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Previous data page" }));
  expect(screen.getByRole("rowheader", { name: label })).toBeVisible();
});

test("partial measurements expose support beside values, including absent samples", async () => {
  render(
    <UsageChart
      title="Support"
      unit="runner-minutes"
      points={[
        { label: "Partial", observed: 10, sampleCount: 2, populationCount: 5 },
        { label: "Missing", observed: null, sampleCount: 0, populationCount: 3 },
        { label: "Future", observed: null, estimate: 4 },
      ]}
    />,
  );
  await userEvent.click(screen.getByText("Data table", { selector: "summary" }));
  expect(screen.getByRole("columnheader", { name: "Samples / population" })).toBeVisible();
  for (const [row, support] of [
    ["Partial", "2 / 5"],
    ["Missing", "0 / 3"],
    ["Future", "Unavailable"],
  ]) {
    expect(
      within(screen.getByRole("row", { name: new RegExp(`^${row} `) })).getAllByText(support ?? "")
        .length,
    ).toBeGreaterThan(0);
  }
});

test.each([
  { sampleCount: 1 },
  { populationCount: 1 },
  { sampleCount: 2, populationCount: 1 },
  { sampleCount: -1, populationCount: 1 },
  { sampleCount: 0.5, populationCount: 1 },
])("rejects contradictory support %#", (support) => {
  render(
    <UsageChart
      title="Support"
      unit="runner-minutes"
      points={[{ label: "Period", observed: 1, ...support }]}
    />,
  );
  expect(screen.getByRole("status")).toHaveTextContent("cannot be displayed");
});
