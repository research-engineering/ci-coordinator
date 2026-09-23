# Analytics Exploration

Status: bounded UI implementation contract

Owner: operator UI, under REQ-CI-UI-005 and REQ-CI-UI-022. Metric meaning stays
with [archive analytics](archive-analytics-product.md). Delivery:
[implementation plan](analytics-exploration-plan.md). Product routing:
[roadmap](../../ROADMAP.md#d4d7-v1-dashboard-and-pipeline-views).

## Decision

Reuse the admitted analytics response and existing bounded UsageChart. One
metric selector replaces the two permanently stacked historical charts; the
conditional forecast and its validation remain separate. This adds useful
views without another endpoint, collection process, chart dependency or model.

As-is, tiny path segments stand in for isolated observations, most returned
job outcomes have no time-series view, and day inspection is exposed only by
slowdown events. The intended delta is presentation and read-only navigation.
Do not change collection, retention, forecast acceptance, query bounds,
authentication, database authority, planning or CI execution.

## Projection Invariants

For each already admitted daily bucket b, the selected metric is exactly:

| Metric              | Value                                                      | Sample support                                   |
|---------------------|------------------------------------------------------------|--------------------------------------------------|
| Runner usage        | observedRunnerMs / 60000, or null                          | durationSamples / jobs                           |
| Mean job duration   | runnerMs / durationSamples / 1000, or null at zero samples | durationSamples / jobs                           |
| Mean job queue time | queueMs / queueSamples / 1000, or null at zero samples     | queueSamples / jobs                              |
| Run attempts        | attempts                                                   | Not a job-filtered population                    |
| Selected jobs       | selected.jobs                                              | Retained observations, not provider completeness |
| Failed jobs         | selected.failures                                          | Same selected job population                     |
| Cancelled jobs      | selected.cancellations                                     | Same selected job population                     |

Changing the metric transforms the current report only: no fetch or mutation.
Run-attempt counts precede job/category filtering; label that distinction.
An empty retained count is a known zero in this archive, not proof that no work
ran upstream. Missing timing stays null, including before unit conversion.
No rate, billing or causal attribution is invented.

Every finite observed point has a visible marker, including zero and singleton
series. Null observations have no marker and still interrupt line segments.
Markers and strokes remain legible when the viewBox is horizontally scaled;
safe native titles expose the date/value. Keep the existing finite coordinate
space, geometry admission, forecast distinction and bounded accessible table.

Day inspection is an optional table action using the original bucket day, not
a formatted or positional reconstruction. Keep scope, generation, query and
revision binding in the existing AnalyticsSources path. Source runs are a
containing population, not proof that every job in a run matches a category;
show a category-filter limitation when applicable. No raw provider HTML/SVG.

## Presentation and Example Identity

Use existing spacing, colors, native selects, icon buttons and accessible
labels. Keep one historical plot at a time rather than making the page longer.
Retain keyboard-accessible table values and read-only source navigation.

The documentation screenshot must show Bart Simpson / bart.simpson using a
synthetic session in the visual browser scenario. Do not modify Keycloak,
runtime users, permissions or default authentication fixtures. Replace the
README image only with that scenario's fresh native CI artifact; keep its
example-data provenance and inspect the actual image before publication.

## Cost and Revision Conditions

A new chart engine is unnecessary for seven scalar projections and at most
396 points. Duplicated per-metric renderers would repeat geometry/a11y policy.
Keep metric selection in one capability component and geometry in UsageChart.
Reconsider a library only for a measured interaction/layout need not served by
this bounded chart; reconsider metric meaning only through its domain owner.

| Writer         | Operands and protected observations                                                               | Independent falsifiers and gate                                                                                               |
|----------------|---------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| Analytics view | bucket values, sample counts, day, selected metric; preserve exact API query and immutable report | Seven projections, zero/null/partial support, no-fetch metric switch, exact day callback; native component/browser witnesses  |
| Chart          | observed/estimate/bounds/label and finite geometry; preserve gap, table and forecast behavior     | Singleton/zero/null markers, gap path, invalid geometry, responsive pixel visibility and a11y; native chart/browser witnesses |
| Example        | displayName and preferredUsername only; preserve synthetic actor/roles/CSRF                       | Browser asserts Bart Simpson and no old visible name; native screenshot plus root image inspection                            |

Static checks cover TypeScript, Biome, documentation links and Proofkit routes.
Native GitHub witnesses, frozen independent review and actual image inspection
are separate acceptance evidence; no production qualification is implied.
