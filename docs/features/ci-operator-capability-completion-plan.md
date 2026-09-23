# CI Operator Capability Completion Plan

Status: implementation plan
Date: 2026-09-22

## Scope And Authority

This plan owns the execution and acceptance delta for six operator-capability
refinements, not new runtime authority or a second global roadmap. The
[roadmap delivery grouping](../../ROADMAP.md#cohesive-delivery-batches) owns
priority and readiness. Existing module contracts and feature designs remain
authoritative; their source-delivered behavior must not be reimplemented merely
because a completion criterion is listed here.

The accepted refinements originated in a bounded external prototype study.
They are retained as product requirements, not evidence of source, provider or
runtime qualification in this repository. Transfer operator use cases, not
private implementation, framework choices or assurance heuristics. No external
prototype installation or shared database is required.

The simplest sufficient trajectory is to extend the existing capability owners:

```text
accepted refinement
  -> existing capability owner and delivery batch
  -> missing contract or read-model delta
  -> UI/API scenario and independent counterexample
  -> source qualification, then live evidence where required
```

This costs less duplicated policy and migration work than a separate infrastructure-management layer.
Reconsider only if a concrete requirement cannot fit an existing owner without
forbidden co-ownership; use the repository's
[decomposition contract](../architecture/cross-cutting/module-ownership-and-decomposition.md),
not file size or analogy, to establish that conclusion.

## Delivery Dependencies

O1-O6 below are acceptance slices within existing delivery batches, not new
global stages and not necessarily separate PRs. B1 remains the own-CI pilot.
Bring forward only O1 facts needed to diagnose that pilot; do not gate it on
the complete dashboard, test-history importer or notification product.

| Slice | Delivery placement | Dependency and closure boundary |
| --- | --- | --- |
| O1: capability diagnostics | B1/B2 evidence; B3 completion | Existing identity, inventory and operation evidence before read-only API/UI projection. |
| O2: reusable dependency passport | B3; consumed by B5 | Exact bounded discovery and access before reverse references; display is not omission proof. |
| O3: optimization portfolio | B4 with D7 | Existing economics facts and cohort contracts before cross-repository queries; partial history is explicit. |
| O4: test reliability | B4 | Admitted test-result acquisition before test-level claims; run-level summaries remain independently usable. |
| O5: actionable notifications | B4 with D6/D7 | Existing signals before subscriptions, durable delivery and acknowledgment/recovery. |
| O6: external analysis adapters | B4 with D6 | Actual producer API and scoped admission before result display or optional advice exchange. |

B2 closes shared acquisition/recovery requirements once, not separately for
every view. B6/B7 retain deployment/incident bindings and operational proof.
B8 chat stays last. Group source, contracts, migration if needed, API/UI,
documentation and qualification for each coherent outcome. Reuse completed
work when its exact evidence is still applicable; report the remaining delta.

## Common Acceptance Discipline

Before implementing each slice, bind its source snapshot, semantic owner,
existing behavior, missing behavior, protected observations and finite budgets.
Add or extend the owning typed requirement, Proofkit route and negative witness
only for a real changed guarantee. A planning-document binding proves routing,
not implementation of O1-O6. A changed domain relation requires its own focused
design before code; a read-only projection may reuse the current owner design.

All slices preserve repository/installation authorization, exact evidence
identity, deterministic planning and independent FullCI fallback. Missing,
stale, denied, failed and incomplete observations stay distinguishable. UI and
API share policy; no UI-only administration path or invented green readiness.

For every new persisted or derived value specify its independent inputs,
refresh/invalidation, retention/deletion, query/cardinality budget and failure
outcome. Set numeric budgets from the representative deployment before
acceptance, not arbitrary constants in this plan. Reuse admitted libraries and
native analyzers when their contracts suffice. An unavailable statistic never
weakens planning, credentials or required checks.

Independent review follows [the repository policy](../../AGENTS.md). Use exact
source-bound native checks and scenario evidence; retain source delivery,
provider behavior and operational qualification separately. No percentage,
absence of findings or dashboard badge proves global correctness.

## O1: Capability Diagnostics

Owners: [provider inventory](../architecture/modules/provider-inventory.md),
[observability](../architecture/modules/observability.md),
[workbench reads](../architecture/modules/workbench-read-models.md) and
[API-first configuration](api-first-configuration-lifecycle.md).

1. Inventory existing App access/permissions, last received webhook, last
   successful reconciliation, observation freshness/gaps, rate-limit evidence,
   trust-key availability and selected-mode blockers. Reuse their source
   identities and observation timestamps; do not create a second aggregate
   mutable readiness flag.
2. Expose a bounded capability passport with evidence links and concrete next
   action: request access, repair webhook, refresh evidence or complete
   configuration. Distinguish observation permission from execution authority
   and historical access from current admission.
3. Provide the same diagnostics through API and the integration/repository UI.
   Passive evidence is the baseline; an active probe needs explicit authorized
   scope, finite cost and a separate recorded outcome.

Exit witnesses: webhook silence remains unknown, not broken delivery;
permission denial is not empty inventory; an unavailable provider does not
erase prior observations; expired evidence cannot grant a mode; identical
repository names across installations do not cross-contaminate access. A
passport failure leaves planning and existing readiness owners unchanged.

## O2: Reusable Dependency Passport

Owners: [workflow discovery](../architecture/modules/workflow-discovery.md),
[governance observation](../architecture/modules/governance-observation.md),
[universal adoption](universal-workflow-adoption.md) and the
[reusable-workflow boundary](../../ROADMAP.md#cross-repository-reusable-workflows-and-model-limits).

1. Extend the existing admitted graph read model with exact caller commit,
   callee commit/blob, action path/revision or image digest, non-secret inputs
   and resolution time. Distinguish job-level reusable calls from step actions.
2. Add bounded reverse queries: which authorized repositories and declared
   source versions consume this component? Keep actual run/attempt usage a
   separate relation. A moving tag never rewrites historical resolution.
3. Present one expandable workflow with unresolved edges and source links;
   show impacted consumers for a changed component. Reuse this evidence in
   adaptation recommendations, without automatically changing workflows.

Exit witnesses: nested calls, cycles/limits, retagging, inaccessible source,
revoked installation access and equal names in different repositories remain
distinct. An incomplete graph reports incomplete impact, not no consumers.
Changing a component invalidates only evidence depending on that exact input.
No second YAML parser, broad token or new selective authority is introduced.

## O3: Optimization Portfolio

Owners: [economics](../architecture/modules/ci-economics.md),
[budget policies](economics-budget-policies.md),
[analytics exploration](analytics-exploration.md) and
[dashboard/pipeline views](../../ROADMAP.md#d4d7-v1-dashboard-and-pipeline-views).

1. Add authorized organization/repository summaries using the existing facts,
   forecasts and budgets. Start with questions that lead to action: where is
   queue/execution time spent, what regressed, which evidence is missing, and
   which optimization warrants investigation?
2. Keep workflow/job/source/runner cohorts, sample support, denominators and
   provenance visible. CPU, runner occupancy, wall time, billing, predicted
   savings and paired measured savings are distinct quantities. No pool
   utilization without an observed eligible-capacity denominator.
3. Deliver a compact global overview and attention list with repository/job
   drill-down, filters and linked tabs. Reuse charts, uncertainty and forecasts;
   choose a table where it answers the question more clearly. Pipeline views
   retain declared, selected and observed identities rather than blending them.

Exit witnesses: two repositories with a workflow named CI never share a cohort
accidentally; reruns/callee views do not double count work; partial/empty/stale
history is visible; incompatible windows are not compared as measured savings.
Queries and rendering meet declared budgets, with responsive keyboard-accessible
journeys and a table alternative. Analytics failure cannot change a plan.

## O4: Test Reliability

Owners: economics observations and
[testing contracts](../architecture/cross-cutting/testing-and-proofkit.md).
Place acquisition in the provider adapter, normalization in the capability,
and presentation in read models; do not make the HTTP router a test classifier.

1. Start with one needed JUnit/native reporter format, not a universal parser
   framework. Bind artifact producer/digest, repository, source, run/attempt,
   job, test/parameter identity, toolchain and available runner/environment
   coordinates. Reject unsupported formats explicitly; bound bytes, expansion,
   records and parsing time, disable external entity/network resolution and
   avoid storing secrets or raw failure output by default.
2. Define stable identity, duplicate handling, retry/attempt attribution and
   completeness before retaining a compact test-result history. Make detail
   retention configurable under the archive policy; preserve only admitted,
   non-sensitive aggregates beyond that lifetime and honor erasure contracts.
3. Separate observed instability from inferred flakiness. Changed source or
   incomparable environments cannot establish a flaky test. Require documented
   minimum samples and uncertainty; missing comparable history yields
   insufficient evidence, not reliable. Run-level failures remain separately
   usable when no test artifact is available.

Exit witnesses: duplicate uploads, renamed/parameterized tests, fail-then-pass
after a code change, same-source retries, partial artifacts, incompatible
toolchains and erased detail preserve their intended distinctions. Validate
each metric with independent examples and counterexamples. The feature never
auto-quarantines tests, treats a retry as first-attempt success or reduces CI.

## O5: Actionable Notifications

Owners: economics budget signals, administrator Activity/audit, the existing
PostgreSQL worker lifecycle and [D6](../../ROADMAP.md#external-advisory-assistance).

1. Add repository/event subscriptions and an attention inbox with exact signal
   links, acknowledgment and recovery. Acknowledgment is operator state, not
   resolution of the underlying condition; never rewrite immutable audit rows.
2. Deliver through one actually needed external channel first, behind the
   existing authenticated destination/egress boundary. Use durable identities
   binding repository, event, policy revision and recipient. Bound retries,
   backoff, concurrency, storage and cross-repository fairness.
3. Distinguish queued, attempted, confirmed, uncertain, failed and recovered
   outcomes. A provider timeout after accepting a message is not proof of
   non-delivery; use native idempotency/reconciliation where available and
   disclose weaker guarantees otherwise. Retain safe delivery diagnostics.

Exit witnesses: equal titles from different repositories both survive;
duplicate events, process restart, concurrent workers, policy/recipient change,
revoked access, ambiguous send and late recovery do not silently lose or
cross-deliver a signal. A failed channel cannot hold planning resources
indefinitely. No Redis/BullMQ or second broker without measured necessity.

## O6: External Analysis Adapters

Owners: scoped bot read/command APIs and
[external advisory assistance](../../ROADMAP.md#external-advisory-assistance).

1. Study one real producer contract before implementing an adapter. Accept
   bounded versioned findings or evidence references from a CI analyzer, review
   service or optional external integration. Bind producer identity, repository,
   exact source/run/attempt as applicable, tool/version, scope, completeness,
   digest and freshness. Reject unsupported variants; arbitrary metadata is
   not a replacement for an admitted schema.
2. Reuse existing authentication, authorization, replay protection, egress
   allowlists and audit. Keep untrusted findings inert and source-linked.
   Prefer references over duplicating raw SARIF/log archives; define retrieval,
   redaction, expiry and deletion before accepting external payloads.
3. Preserve separate operations for external results, outbound requests for
   advice and inbound optional requests for additional checks. Receiving a
   result does not launch a local scanner or execute a suggested command.
   An execution request still needs its own monotonic scope admission.

Exit witnesses: cross-repository injection, stale SHA, wrong producer, replay,
unknown schema, missing evidence, oversized payload, recursive invocation and
provider failure are bounded and visible. Findings cannot become passed CI,
verified provenance, a compliance grade or omission authority. No shared private
database, mandatory prototype dependency or embedded scanner/LLM platform is needed.

## Source Dispositions And Revision Conditions

This table conserves every study candidate; it is provenance and scope control,
not a second execution queue.

| Study item | Disposition | Acceptance owner |
| --- | --- | --- |
| W1: economics | Reuse existing measurement/forecast semantics; add portfolio UX. | O3 |
| W2: test reliability | Add qualified test-level acquisition/attribution. | O4 |
| W3: shared dependencies | Extend existing discovery with exact reverse references. | O2 |
| W4: notifications | Complete durable, scoped operator delivery. | O5 |
| W5: integration readiness | Project current owners into actionable diagnostics. | O1 |
| W6: external analyzers | Add provider-specific admitted result/advice adapters. | O6 |
| W7: presentation | Apply global/scoped navigation within these journeys, not a cloned UI. | O1-O3 and O5 |
| W8: security/governance | Reuse admitted native analyzers and external CI evidence; no inferred SLSA/compliance score. | O6; existing release admission unchanged |
| W9: platform/operations | Keep general monitoring, identity scanning, person rankings and branch cleanup outside CI Coordinator. | Existing B6/B7 only for accepted environment/incident needs |

Do not copy CI duration as deployment lead time, capped runner-time sums as CPU
utilization, pass/fail flips as proven flakiness, process-local notification
deduplication or field presence as provenance assurance. The local study found
these insufficient as transfer mechanisms; this plan does not generalize that
bounded observation into an exhaustive external-product defect verdict.

True deployment/DORA metrics require a separately demonstrated operator need,
exact deployment/environment/incident relationships and an admitted design.
They are conditional, not new completion blockers. Reopen a rejected direction
only on changed product requirements, a missing owner capability or measured
failure of the simpler approach, not because another project contains it.
No recommendation waives future review of new counterexamples or other scopes.
