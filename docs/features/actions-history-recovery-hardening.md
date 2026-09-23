# Actions History Recovery Hardening

Status: active repair design and acceptance plan; not a qualification receipt.

This supplement resolves observed integration and lifecycle counterexamples in
the [history population](actions-history-population.md) and
[administration](actions-history-administration.md). Their capability contracts
remain the semantic owners. No active CI evidence, published migration or
unrelated repository workflow is weakened by these repairs.

## Current Decisions

The external report reviewed f6d63c34, before the History UI and the negative
JSON-fixture correction. Its19 rows are not19 demonstrated runtime defects.
Root validation separates current failures, conditional risks and deliberately
undelivered features. Independent administration review uses3b4695d.

| ID  | Current disposition                                          | Owner and required next action                                                                                                                                                   |
|-----|--------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| D01 | Confirmed provider admission mismatch                        | Align Actions discovery with the existing finite transport byte budget; retain exact request/pagination binding and a realistic large-page witness.                              |
| D02 | Confirmed recovery integration gap                           | Complete a durable recent-discovery/archive handoff before archive activation; it must work without a webhook and without making analytics capacity block active CI evidence.    |
| D03 | Confirmed avoidable coverage loss                            | At an irreducible saturated window, consume all available permitted pages and retain an explicit truncation gap.                                                                 |
| D04 | Confirmed inherited deadline gap                             | Place rejection sending inside the same absolute request budget and release the work permit independently of response delivery.                                                  |
| D05 | Corrected in source; native proof pending                    | Lexical JSON now reaches the intended numeric validator without prior canonicalization.                                                                                          |
| R01 | Confirmed candidate-selection risk                           | An exhausted gap-blocked item cannot prevent a still-admissible healthy item in the same scope from being considered. Prove with PostgreSQL.                                     |
| R02 | Valid throughput constraint, not a measured incident         | Admit a finite time/count-budgeted drain rather than one item per periodic cycle; preserve fairness, provider budgets and cancellation. Quantify throughput in qualification.    |
| R03 | Valid redundant work with a freshness trade-off              | Distinguish complete retained attempts from partial/gap repair and explicit rescan before skipping provider reads. Identity alone is insufficient reuse authority.               |
| R04 | Valid operator-alert gap                                     | Add enabled/paused-aware history failure/backlog alert semantics before continuous operational qualification; never degrade planning authority because optional statistics fail. |
| P01 | Valid limit of the existing mutation claim                   | Keep call-phase kill distinct from guard-specific causal proof. High-risk mutant-to-falsifier binding remains the own-CI oracle workstream.                                      |
| P02 | Confirmed measurement gap                                    | Include capability editors in the coverage population without lowering thresholds or extracting state solely for a metric.                                                       |
| P03 | Valid release-evidence gap, no specific CVE established      | Bind final-image OS vulnerability results to its OCI digest and registry/scanner/advisory policy in release qualification. SBOM is not vulnerability analysis.                   |
| G01 | Open archive-query capability                                | Bounded authorized attempt/job/gap/detail queries and analytics belong to the next archive read slice. Status counts are not those queries.                                      |
| G02 | Implemented after the reviewed source; qualification pending | History client and controls exist. Close the independent lifecycle counterexamples and native/browser proof.                                                                     |
| G03 | Open detail-import lifecycle                                 | Additional job/step detail import and scheduled expiry must ship together. Stored policy alone is not a functioning detail importer or a 365-day retention claim.                |
| G04 | Open retroactive policy operation                            | Keep requested/effective/applied distinct; add explicit preview/apply with revisions, cutoffs and race witnesses. Do not turn a settings edit into silent deletion.              |
| G05 | Open administrative erasure                                  | Add owned generation-fenced resumable erasure and restore semantics; do not infer deletion authority from a state enum.                                                          |
| G06 | Open longitudinal analytics semantics                        | Admit versioned workflow/job/cohort/category mapping with unknown outcomes; never infer stable workload identity from a display name.                                            |
| G07 | Open operational qualification                               | Exact native aggregate, immutable deployment, sustained import/recovery, quota, backup and load evidence remain separate.                                                        |

G01/G03-G06 refine the existing roadmap; they are not silently removed or
declared done by a status endpoint. Production or all-history completeness
claims remain forbidden while their applicable acceptance conditions are open.

## Repair Relations

The [recent-recovery design](actions-history-recent-recovery.md) refines D02
with separate durable frontiers and atomic pending-source handoff.

### Supplementary Review

The independent16-hypothesis report also targets f6d63c34. Its labels are
candidates, not authority. Current-source validation gives the following scope.

| IDs           | Disposition and action                                                                                                                                                                                                                                 |
|---------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| H1            | Confirmed by a fresh read-only REST attempt 2 response: 27 of 29 jobs have inconsistent timestamps. Preserve raw facts, expose derived timing quality and withhold invalid durations; do not reject the entire attempt.                                |
| H2            | Conditional outage-to-gap amplification; do not classify an observed nonterminal run as failed I/O. The existing bounded gap and repair contract otherwise remains; systemic cooldown needs measured failure-class evidence.                           |
| H3/H7/H8      | Bounded-read usefulness, schema-admission cost and shared-pool contention need latency/query/occupancy qualification. Neither arbitrary deadline growth nor unsafe schema caching follows.                                                             |
| H4/H10/H13    | Duplicates R02/D02/G03-G04 above; preserve their respective implementation and open-feature boundaries.                                                                                                                                                |
| H5/H6/H12/H15 | Evolution, parity, internal grammar and subdivision optimizations have no demonstrated current failure. Preserve versioned provenance, independent trust admission and bounded coverage; reconsider on a concrete incompatible input or measured cost. |
| H9            | Body422 before authentication is not an established bypass: finite body/work bounds, authorization and CSRF still precede effects. Revisit if response secrecy or a resource budget requires different admission order.                                |
| H11           | Rejected: the actor belongs to the command digest, so a changed actor conflicts before the stored-receipt integrity check.                                                                                                                             |
| H14           | Confirmed possible lock convoy: never wait for an observation guard while holding the audit head. A failed nonblocking guard admission rolls back the atomic delivery pair and returns retryable unavailability.                                       |
| H16           | Confirmed least-privilege gap: remove unused permanent-statistics DELETE and defaults UPDATE grants. Retain active detail/recheck deletion; future erasure/default writers must admit their own grants and witnesses.                                  |

For H1, timestamp presence and timestamp consistency are separate predicates.
Archive storage records the provider facts exactly. `timing_quality` is derived,
not another persisted authority. Only a consistent `JobTiming` may be projected;
an inconsistent tuple returns no timing projection, never a negative duration or
invented timestamp. Job population completeness does not imply timing quality.
This changes archive admission only, not current CI evidence or its strict timing
contract. Native witnesses bind realistic rerun input, canonical storage roundtrip,
unchanged raw timestamps and explicit absent timing; malformed identity remains
rejected independently.

The provider adapter must use the shared structural job-fact decoder, not the
reconciliation wrapper that additionally requires ordered start/end times.
The wrapper retains that strict predicate for existing reconciliation consumers;
the archive admits raw facts and projects timing quality at its own boundary.
There is no caller flag that silently weakens reconciliation admission.

For H14, preserve the existing audit-before-observation lock order. Reordering
locks could create a cycle with other writers; a nonblocking observation guard
removes this wait edge instead. A PostgreSQL barrier witness must hold that
guard, attempt a duplicate, and let a third independent delivery progress after
the refused transaction settles. This is not a measured ingress latency claim.

ACL transition also tests previously granted column UPDATE privileges, not only
a fresh role. PostgreSQL's table-level [REVOKE](https://www.postgresql.org/docs/18/sql-revoke.html)
already removes corresponding column privileges. Retain that admitted primitive
and exact postcondition checks rather than adding a redundant manual ACL walker.

### Provider Pages

GitHub permits 100 runs per page and up to 1000 search results for a created-time
filter. A measured admissible page of 100 runs was 1,412,833 bytes, larger than
the consumer's1 MiB decoder bound. The transport already owns a finite8 MiB
response bound. An unrelated stricter decoder cap must not silently contradict
the requested page shape.

Retain logical page size and cursor identity so existing stored pagination is
not reinterpreted. Raising only this endpoint's admission to the existing
transport budget is simpler than changing page size, checkpoint format and
physical-to-logical page mapping. It admits the observed counterexample without
removing any response bound. Values beyond the declared finite budget remain
unavailable, not successful empty data. Further adaptive sizing requires its
own cursor/refinement proof rather than an unversioned global page-size change.

For an irreducible interval:

```text
Saturated(window) and CannotSplit(window) and NextPageAvailable(page)
  => NextCursor.window = window and NextCursor.page = page + 1

LastPermittedPage or ProviderTermination
  => AdvanceWindow and RetainExplicitTruncation
```

This consumes the provider-accessible prefix without asserting that its
unavailable suffix is complete. Persist the gap without repeatedly consuming
gap quota for every page of the same irreducible interval.

### Request Deadline

An ASGI send may suspend under write backpressure. Its small JSON size does not
bound that wait. A rejection therefore needs the same absolute deadline owner
as successful work.

```text
HardDeadline = AdmissionTime + ConfiguredRequestBudget
WorkDeadline = HardDeadline - ReservedFailureSendBudget
0 < ReservedFailureSendBudget <= min(1 second, RequestBudget / 10)

Timeout or Overload
  => NoUnboundedSend and WorkPermitReleasedBeforeFailureSend
```

The reserve is inside, not added after, the configured budget. Liveness routes
and external cancellation retain their existing semantics. Expired delivery
must propagate termination rather than initiating another unbounded response.
No task may detach to finish sending. The bound assumes cooperative ASGI work;
CPU isolation and underlying transport/process shutdown remain separate owners.
Hard-budget expiry propagates cancellation so application generic-error
middleware does not start another response. Uvicorn's own fallback/error I/O
and actual socket closure are outside this ASGI predicate and still require
server/proxy qualification; this patch alone does not prove their deadlines.

### UI Lifetime

Keep History and Observation lazily mounted after their first visit inside
the existing scoped Economics console. Inactive panels stop status polling;
their dirty or unresolved command state survives sibling-tab navigation.
Actual scope/session replacement still cancels and destroys the old owner.
Global storage or persistence across a full page reload is not introduced.

Before every status read, require both an active view and visible document.
One queued refresh may survive a hidden interval; returning visible executes
it once, even when automatic polling is disabled by the latest paused result.
Current in-flight work remains bounded and its disposal fences late results.
The browser-only reader owns disposal in a layout effect, so the committed
view/scope transition removes the old listener, timer and request before later
passive work can resume it. Network work itself remains asynchronous.

The response-byte witness uses an otherwise valid exact mutation receipt and
application/json. Only legal trailing JSON whitespace changes its size across
the boundary. Rejecting an invalid content type first cannot prove byte-limit
sensitivity.

## Implementation And Acceptance Order

The runtime collection callback drains each existing lane independently, with
at most 16 items and one 45-second monotonic budget per lane. The enclosing
maintenance budget remains55 seconds; provider operations keep their existing
20-second limit. A fast recent lane does not wait for a slow backfill item before
attempting its next item. An empty lane, capacity refusal, abort or storage
failure ends that drain. Each attempted item retains its own outcome metric;
the callback is not a claim that the whole backlog is empty. These conservative
initial caps remove the one-item-per-period bottleneck but are not measured
throughput, a provider-rate allowance or a globally optimal configuration.

The existing `run` operation remains a single finite three-lane turn for its
owned caller contract. Only the runtime callable selects bounded repeated
turns. There is no independent background task after callback completion, no
broker and no unbounded result list. Native witnesses force a slow backfill
with a fully drained recent lane, count exhaustion, no work, abort and a whole
drain timeout independently from per-provider deadlines.

1. Repair UI lifetime, visibility and isolated byte witnesses, and include
   capability editors in native coverage. Preserve existing observation tests.
2. Fix endpoint admission and irreducible-page traversal. Cover a realistic
   large page, all 1000 accessible results, replay and one retained gap.
3. Fix absolute rejection delivery and test independently stalled response
   start/body for overload and deadline expiry, permit reuse and cancellation.
4. Resolve same-scope recovery fairness with a bounded alternate eligible query;
   preserve the blocked item, quotas, retry debit and exact lease authority.
5. Close recent-discovery handoff and count/time-budgeted drain under explicit
   transaction, capacity and lock-order contracts. Do not couple optional
   archive failure to active CI correctness through a best-effort callback.
6. Reconcile alert, reuse, read, retention, erasure, provenance and qualification
   obligations with their existing roadmap owners; retain every open item.
7. Run owner-valid static gates, exact GitHub native/browser/coverage checks
   and fresh bounded review of material changes before merge. Local execution
   of behavioral, browser, database or container tests remains disallowed.

The preferred changes preserve current capability and transaction boundaries.
A broker, framework, new service, raw provider-data mirror or global rewrite
does not follow from these counterexamples. Reconsider only against a named
missing invariant or measured workload budget.

## External Premises

[GitHub workflow-run pagination](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-repository)
supports the page/result limits, not a point-in-time snapshot guarantee.
[Uvicorn write flow control](https://uvicorn.dev/server-behavior/#write-flow-control)
supports the potentially suspending send premise, not application correctness.
