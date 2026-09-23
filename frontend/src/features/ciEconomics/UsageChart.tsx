import { ArrowLeft, ArrowRight, List } from "lucide-react";
import { useId, useState } from "react";
import { ScrollableRegion } from "../../components/ScrollableRegion";

export interface UsagePoint {
  readonly label: string;
  readonly observed: number | null;
  readonly estimate?: number | null;
  readonly lower?: number | null;
  readonly upper?: number | null;
  readonly sampleCount?: number;
  readonly populationCount?: number;
  readonly onInspect?: () => void;
}

const WIDTH = 1_000;
const HEIGHT = 200;
const PAGE_SIZE = 31;
const MAX_POINTS = 366 + 30;
const numbers = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const compactNumbers = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export function UsageChart({
  title,
  unit,
  points,
  supportLabel = "Samples / population",
}: {
  readonly title: string;
  readonly unit: string;
  readonly points: readonly UsagePoint[];
  readonly supportLabel?: string | undefined;
}) {
  const id = useId();
  const [page, setPage] = useState(0);
  const values = points.flatMap((point) => [
    point.observed,
    point.estimate,
    point.lower,
    point.upper,
  ]);
  if (
    points.length > MAX_POINTS ||
    new Set(points.map((point) => point.label)).size !== points.length ||
    values.some((value) => value != null && (!Number.isFinite(value) || value < 0)) ||
    points.some(
      (point) =>
        (point.sampleCount === undefined) !== (point.populationCount === undefined) ||
        (point.sampleCount !== undefined &&
          point.populationCount !== undefined &&
          (!Number.isSafeInteger(point.sampleCount) ||
            !Number.isSafeInteger(point.populationCount) ||
            point.sampleCount < 0 ||
            point.sampleCount > point.populationCount)),
    ) ||
    points.some(
      (point) =>
        (point.lower != null || point.upper != null) &&
        (point.estimate == null ||
          point.lower == null ||
          point.upper == null ||
          point.lower > point.estimate ||
          point.upper < point.estimate),
    )
  ) {
    return <p role="status">The usage series cannot be displayed.</p>;
  }
  const present = values.filter((value): value is number => value != null);
  const maximum = Math.max(1, ...present);
  const x = (index: number) =>
    points.length < 2 ? WIDTH / 2 : (index * WIDTH) / (points.length - 1);
  const y = (value: number) => HEIGHT - (value / maximum) * HEIGHT;
  const hasEstimate = points.some((point) => point.estimate != null);
  const hasSupport = points.some((point) => point.sampleCount !== undefined);
  const hasInspection = points.some((point) => point.onInspect !== undefined);
  const tickIndexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  const lastPage = Math.max(0, Math.ceil(points.length / PAGE_SIZE) - 1);
  const currentPage = Math.min(page, lastPage);
  const offset = currentPage * PAGE_SIZE;
  const displayed = points.slice(offset, offset + PAGE_SIZE);

  function line(field: "observed" | "estimate") {
    let connected = false;
    return points
      .map((point, index) => {
        const value = point[field];
        if (value == null) {
          connected = false;
          return "";
        }
        const command = connected ? "L" : "M";
        connected = true;
        return `${command}${x(index)},${y(value)} l0.01,0`;
      })
      .join(" ");
  }

  function interval() {
    const segments: number[][] = [];
    let current: number[] = [];
    for (let index = 0; index < points.length; index += 1) {
      const point = points[index];
      if (point?.lower != null && point.upper != null) current.push(index);
      else if (current.length) {
        segments.push(current);
        current = [];
      }
    }
    if (current.length) segments.push(current);
    return segments
      .map((segment) => {
        const upper = segment.map((index) => `${x(index)},${y(points[index]?.upper ?? 0)}`);
        const lower = segment
          .toReversed()
          .map((index) => `${x(index)},${y(points[index]?.lower ?? 0)}`);
        return `M${upper.join(" L")} L${lower.join(" L")} Z`;
      })
      .join(" ");
  }

  return (
    <figure className="usage-chart" aria-label={title}>
      <figcaption id={`${id}-title`}>
        <h3>{title}</h3>
        <span className="usage-unit">{unit}</span>
      </figcaption>
      {present.length ? (
        <>
          <div className="usage-plot">
            <div className="usage-y-axis" aria-hidden="true">
              {[maximum, maximum / 2, 0].map((value) => (
                <span key={value}>{compactNumbers.format(value)}</span>
              ))}
            </div>
            <svg
              role="img"
              aria-labelledby={`${id}-title ${id}-description`}
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              preserveAspectRatio="none"
            >
              <title id={`${id}-description`}>
                Observed values{hasEstimate ? " and informational estimates" : ""}. Missing
                measurements are gaps. Exact values follow in the data table.
              </title>
              {[0, HEIGHT / 2, HEIGHT].map((height) => (
                <path key={height} className="usage-grid" d={`M0,${height} H${WIDTH}`} />
              ))}
              <path className="usage-interval" d={interval()} />
              {points.map((point, index) =>
                point.estimate != null && point.lower != null && point.upper != null ? (
                  <g key={point.label}>
                    <path
                      className="usage-error-bar"
                      d={`M${x(index)},${y(point.lower)} V${y(point.upper)}`}
                    />
                    <circle
                      className="usage-estimate-point"
                      cx={x(index)}
                      cy={y(point.estimate)}
                      r="3"
                    />
                  </g>
                ) : null,
              )}
              <path className="usage-observed" d={line("observed")} />
              {points.map((point, index) =>
                point.observed == null ? null : (
                  <circle
                    key={point.label}
                    className="usage-observed-point"
                    cx={x(index)}
                    cy={y(point.observed)}
                    r="2.5"
                  >
                    <title>
                      {point.label}: {numbers.format(point.observed)} {unit}
                    </title>
                  </circle>
                ),
              )}
              {hasEstimate ? <path className="usage-estimate" d={line("estimate")} /> : null}
            </svg>
            <div className="usage-x-axis" aria-hidden="true">
              {tickIndexes.map((index) => (
                <span key={index} title={points[index]?.label}>
                  {points[index]?.label}
                </span>
              ))}
            </div>
          </div>
          <div className="usage-legend">
            <span>
              <i className="usage-observed-key" aria-hidden="true" />
              Observed
            </span>
            {hasEstimate ? (
              <span>
                <i className="usage-estimate-key" aria-hidden="true" />
                Estimate and uncertainty
              </span>
            ) : null}
          </div>
        </>
      ) : (
        <p role="status">No measured usage in this period.</p>
      )}
      {points.length ? (
        <details className="usage-data">
          <summary>Data table</summary>
          <ScrollableRegion className="table-scroll" label={`${title} data table`}>
            <table>
              <caption>
                {title} ({unit})
              </caption>
              <thead>
                <tr>
                  <th scope="col">Period</th>
                  <th scope="col">Observed</th>
                  {hasSupport ? <th scope="col">{supportLabel}</th> : null}
                  {hasEstimate ? (
                    <>
                      <th scope="col">Estimate</th>
                      <th scope="col">Interval</th>
                    </>
                  ) : null}
                  {hasInspection ? <th scope="col">Source runs</th> : null}
                </tr>
              </thead>
              <tbody>
                {displayed.map((point) => (
                  <tr key={point.label}>
                    <th scope="row">{point.label}</th>
                    <td>
                      {point.observed == null ? "Not measured" : numbers.format(point.observed)}
                    </td>
                    {hasSupport ? (
                      <td>
                        {point.sampleCount === undefined
                          ? "Unavailable"
                          : `${numbers.format(point.sampleCount)} / ${numbers.format(point.populationCount ?? 0)}`}
                      </td>
                    ) : null}
                    {hasEstimate ? (
                      <>
                        <td>
                          {point.estimate == null ? "Unavailable" : numbers.format(point.estimate)}
                        </td>
                        <td>
                          {point.lower == null || point.upper == null
                            ? "Unavailable"
                            : `${numbers.format(point.lower)} - ${numbers.format(point.upper)}`}
                        </td>
                      </>
                    ) : null}
                    {hasInspection ? (
                      <td>
                        {point.onInspect ? (
                          <button
                            type="button"
                            className="icon-button"
                            aria-label={`Inspect source runs for ${point.label}`}
                            title={`Inspect source runs for ${point.label}`}
                            onClick={point.onInspect}
                          >
                            <List aria-hidden="true" />
                          </button>
                        ) : (
                          "Unavailable"
                        )}
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollableRegion>
          {lastPage > 0 ? (
            <div className="usage-pagination">
              <span>
                {offset + 1}-{offset + displayed.length} of {points.length}
              </span>
              <button
                type="button"
                className="icon-button"
                aria-label="Previous data page"
                title="Previous data page"
                disabled={currentPage === 0}
                onClick={() => setPage(currentPage - 1)}
              >
                <ArrowLeft aria-hidden="true" />
              </button>
              <button
                type="button"
                className="icon-button"
                aria-label="Next data page"
                title="Next data page"
                disabled={currentPage === lastPage}
                onClick={() => setPage(currentPage + 1)}
              >
                <ArrowRight aria-hidden="true" />
              </button>
            </div>
          ) : null}
        </details>
      ) : null}
    </figure>
  );
}
