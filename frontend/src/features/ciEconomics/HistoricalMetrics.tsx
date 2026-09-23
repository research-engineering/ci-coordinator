import { useState } from "react";
import type { AnalyticsReport } from "../../api/ciEconomics/analyticsSchema";
import { UsageChart, type UsagePoint } from "./UsageChart";

type Bucket = AnalyticsReport["buckets"][number];
interface Metric {
  readonly id: string;
  readonly title: string;
  readonly unit: string;
  readonly supportLabel?: string;
  readonly note?: string;
  readonly project: (bucket: Bucket) => Omit<UsagePoint, "label">;
}

const metrics = [
  {
    id: "runner",
    title: "Retained runner usage",
    unit: "runner-minutes",
    supportLabel: "Measured jobs / selected jobs",
    project: (bucket: Bucket) => ({
      observed: bucket.observedRunnerMs === null ? null : bucket.observedRunnerMs / 60000,
      sampleCount: bucket.selected.durationSamples,
      populationCount: bucket.selected.jobs,
    }),
  },
  {
    id: "duration",
    title: "Mean observed job duration",
    unit: "seconds / measured job",
    supportLabel: "Measured jobs / selected jobs",
    project: (bucket: Bucket) => ({
      observed:
        bucket.selected.durationSamples === 0
          ? null
          : bucket.selected.runnerMs / bucket.selected.durationSamples / 1000,
      sampleCount: bucket.selected.durationSamples,
      populationCount: bucket.selected.jobs,
    }),
  },
  {
    id: "queue",
    title: "Mean observed job queue time",
    unit: "seconds / measured job",
    supportLabel: "Queue measurements / selected jobs",
    project: (bucket: Bucket) => ({
      observed:
        bucket.selected.queueSamples === 0
          ? null
          : bucket.selected.queueMs / bucket.selected.queueSamples / 1000,
      sampleCount: bucket.selected.queueSamples,
      populationCount: bucket.selected.jobs,
    }),
  },
  {
    id: "attempts",
    title: "Retained run attempts",
    unit: "attempts",
    note: "Run attempts are counted before job and category filters.",
    project: (bucket: Bucket) => ({ observed: bucket.attempts }),
  },
  {
    id: "jobs",
    title: "Selected jobs",
    unit: "jobs",
    project: (bucket: Bucket) => ({ observed: bucket.selected.jobs }),
  },
  {
    id: "failures",
    title: "Failed jobs",
    unit: "jobs",
    project: (bucket: Bucket) => ({ observed: bucket.selected.failures }),
  },
  {
    id: "cancelled",
    title: "Cancelled jobs",
    unit: "jobs",
    project: (bucket: Bucket) => ({ observed: bucket.selected.cancellations }),
  },
] as const satisfies readonly Metric[];

export function HistoricalMetrics({
  report,
  onInspect,
}: {
  readonly report: AnalyticsReport;
  readonly onInspect: (day: string) => void;
}) {
  const [metric, setMetric] = useState<Metric>(metrics[0]);
  return (
    <section className="analytics-history" aria-label="Historical measurements">
      <label className="analytics-metric">
        Metric
        <select
          value={metric.id}
          onChange={(event) => {
            const next = metrics.find((item) => item.id === event.currentTarget.value);
            if (next) setMetric(next);
          }}
        >
          {metrics.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
      </label>
      {metric.note ? <p className="analytics-provenance">{metric.note}</p> : null}
      <UsageChart
        key={metric.id}
        title={metric.title}
        unit={metric.unit}
        supportLabel={metric.supportLabel}
        points={report.buckets.map((bucket) => ({
          ...metric.project(bucket),
          label: bucket.day.slice(0, 10),
          onInspect: () => onInspect(bucket.day),
        }))}
      />
    </section>
  );
}
