import type { AnalyticsReport } from "../../api/ciEconomics/analyticsSchema";
import { UsageChart, type UsagePoint } from "./UsageChart";

export function ForecastChart({ report }: { readonly report: AnalyticsReport }) {
  const forecast = report.forecast;
  if (
    forecast.status !== "available" ||
    forecast.predictedRunnerMs === null ||
    forecast.lowerRunnerMs === null ||
    forecast.upperRunnerMs === null
  )
    return null;
  const horizon = forecast.horizonDays;
  const points: UsagePoint[] = [];
  const start = report.buckets.length % horizon;
  for (let index = start; index < report.buckets.length; index += horizon) {
    const group = report.buckets.slice(index, index + horizon);
    const first = group[0];
    const last = group.at(-1);
    if (!first || !last || group.length !== horizon) continue;
    points.push({
      label: `${first.day.slice(0, 10)} - ${last.day.slice(0, 10)}`,
      sampleCount: group.reduce((sum, row) => sum + row.selected.durationSamples, 0),
      populationCount: group.reduce((sum, row) => sum + row.selected.jobs, 0),
      observed: group.some((row) => row.observedRunnerMs === null)
        ? null
        : group.reduce((sum, row) => sum + (row.observedRunnerMs ?? 0) / 60000, 0),
    });
  }
  const end = new Date(Date.parse(forecast.cutoff) + (horizon - 1) * 86400000)
    .toISOString()
    .slice(0, 10);
  points.push({
    label: `${forecast.cutoff.slice(0, 10)} - ${end}`,
    observed: null,
    estimate: forecast.predictedRunnerMs / 60000,
    lower: forecast.lowerRunnerMs / 60000,
    upper: forecast.upperRunnerMs / 60000,
  });
  return (
    <UsageChart
      title={`${horizon}-day usage forecast`}
      unit="runner-minutes per period"
      points={points}
    />
  );
}
