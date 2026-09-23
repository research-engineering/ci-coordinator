# Analytics Exploration Delivery

Status: implementation plan

Design and invariant owner: [analytics exploration](analytics-exploration.md).
Base: 47bdca30bbaf2a661a56546b03b9b62be31b14f9. One connected UI batch;
existing backend metric, retention and authorization contracts remain unchanged.

1. Add HistoricalMetrics, a small capability-owned report projection and
   selector. Replace stacked historical plots while retaining the forecast,
   diagnostics and existing source inspection.
2. Make observed markers explicit in UsageChart; retain null gaps, finite
   geometry, safe labels and bounded tables. Add optional date inspection to
   table rows and make category population limits explicit in source views.
3. Bind Bart Simpson / bart.simpson only in the visual scenario. Preserve the
   real user identity and the other tests' authentication fixtures.
4. Add parameterized component witnesses for every independent metric operand,
   timing sample support, no network on metric switch, singleton/zero/missing
   geometry and exact callback labels. Retain old negative cases.
5. Extend the native browser journey for metric selection, sparse point
   visibility, date-to-run navigation and a11y across existing viewports.
   Test both populated and empty/missing data; do not manufacture a forecast.
6. Add Proofkit routes under existing UI005/UI022 owners; regenerate the
   route-source digest only. Run the permitted static gates on the final tree.
7. Perform one frozen independent review under AGENTS.md; repair only confirmed
   findings or missing proof. Open one PR and obtain exact-head native checks.
8. Inspect the fresh native screenshot, copy it unchanged to docs/images and
   update the README provenance to that source run in an additive commit.
   Recheck the final complete tree and native qualification before squash merge.

Do not start local tests, containers, databases or application servers. Use
the repository's GitHub Actions route. Do not change pilot target workflows or history
generation. A merged batch closes only this view/inspection increment, not the
global analytics, provider-completeness or production-readiness workstream.
