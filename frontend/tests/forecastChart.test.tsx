import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { analyticsReportSchema } from "../src/api/ciEconomics/analyticsSchema";
import { ForecastChart } from "../src/features/ciEconomics/ForecastChart";
import { analyticsReport, forecastReport } from "./analyticsFixture";

test("forecast chart compares equal-length periods and does not turn an estimate into observed usage", async () => {
  const report = forecastReport();
  expect(analyticsReportSchema.safeParse(report).success).toBe(true);
  render(<ForecastChart report={report} />);
  await userEvent.click(screen.getByText("Data table"));
  const rows = screen.getAllByRole("row");
  expect(rows).toHaveLength(12);
  const first = within(rows[1] as HTMLElement);
  expect(first.getByRole("cell", { name: "21" })).toBeInTheDocument();
  const future = within(rows.at(-1) as HTMLElement);
  expect(future.getByRole("rowheader")).toHaveTextContent("2026-09-09 - 2026-09-15");
  expect(future.getByRole("cell", { name: "Not measured" })).toBeInTheDocument();
  expect(future.getByRole("cell", { name: "21" })).toBeInTheDocument();
  expect(future.getByRole("cell", { name: "21 - 21" })).toBeInTheDocument();
});

test("an unavailable forecast draws no prediction", () => {
  render(<ForecastChart report={analyticsReport()} />);
  expect(screen.queryByRole("figure")).not.toBeInTheDocument();
});
