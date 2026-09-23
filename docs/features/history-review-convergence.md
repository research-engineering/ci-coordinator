# History Review Convergence

Status: proposed successor integration design; not an implementation or qualification receipt.

## Purpose And Authority

Reconcile the PR160 review record against master
`9547a85b1f9b2a8c79c59bf8c8633167731c9021`, then close the remaining
owner-supported gaps without reimplementing repairs or promoting hypotheses
to defects. The [implementation plan](history-review-convergence-plan.md)
owns work ordering and acceptance; this document owns integration decisions
and the historical claim-to-owner mapping only.

The existing [storage](actions-history-storage.md),
[retention](actions-history-retention.md),
[administration](actions-history-administration.md),
[archive read](archive-read-retention-product.md),
[population](actions-history-population.md),
[worker](history-worker-progress.md), and
[release](../architecture/cross-cutting/release-artifact-publication.md)
contracts retain their respective semantic authority. Published designs and
plans are not rewritten. This successor corrects stale integration assumptions,
including the former maintenance-owned collection drain.

The prior [snapshot adjudication](../adoption/snapshot-review-validation-2026-09-13.md)
is retained as historical evidence, including its corrected parent/child race
subjects, clock-origin caveat and bounded 28-claim universe. Its source epoch
is not reused as evidence that a capability is still absent at this baseline.

## Evidence And Claim Algebra

The historical reports concern different snapshots. Prefix `A` identifies the
19-row f6d63c34 report, `H` its supplementary hypotheses, and `S` the
7e4ef975 snapshot review. In particular, A-R03 (repeated provider reads) and
S-R03 (HTTP route labels) are not the same finding.

The retained external report bytes are identified by SHA-256:
`review.ru.md`: `727ef08b4d5085aecbab473066358c659e4707e94396d0ff5eb78ba0d30a2b4b`;
`validated-review.ru.md`: `04d989f4131d789880b0e4f8b7ba7b5655d7658564ededa287e14cb678af457f`.
These hashes identify local historical inputs, not independently reproducible
public publications. The S report is retained in thread
`01a09839-2af1-73c1-81c0-9cc657250cfd`, final message
`msg_0990e780b9b3fc26016aa5ff3e153487d2a0c8f50d85ccb6ab`;
its correction is final message
`msg_0990e780b9b3fc26016aa6127d44ec87d2857e0e974bc6ace3`.
Those messages were retrieved directly, not inferred from a memory summary.
Their UTF-8 text SHA-256 digests are respectively
`e031524001da7657ca3eb6effd09f079f0a4aa573172db94aca785f16c3e719e`
and `ac689ce3e5e529b4c14f1ba53619a00a21cba04629e416651f0b703df9af9083`.
They are private retrieval coordinates, not a tracked public report. The S
ledger uses padded aliases (`TEST-003` for original `TEST-03`, for example);
unnumbered hypotheses are named explicitly. Exact corpus-byte preservation and
all internal agent hypotheses remain distinct from these user-visible reports.

```text
OpenImplementation(f) := CurrentCounterexample(f) and OwnerRequiresDelta(f)
SourceCorrected(f) != NativeQualified(f) != OperationallyQualified(f)
Rejected(f) := RefuterAppliesToExactOriginalClaim(f)
Closed(f) := EveryApplicableAcceptancePredicate(f) has current evidence
```

An unknown performance premise remains unknown. A status enum is not an erasure
implementation; an SBOM is not vulnerability analysis; a test file is not a
test result. This program does not assert universal optimality or that the
entire project is SOTA. It selects the cheapest sufficient supported mechanism
under explicit falsifiers and preserves all residual proof obligations.

### Historical Disposition Ledger

`Source` means an observed implementation at the pinned baseline, not a fresh
test pass. `Proof` requires a new witness or qualification. `Work` requires
implementation. `Rejected` and `Conditional` do not authorize speculative fixes.

| Historical ID                             | Baseline disposition                   | Current owner, refuter, or work package                                                                                                                                                                                                                                                                                      |
|-------------------------------------------|----------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| A-D01                                     | Source                                 | Discovery response admission uses the existing 8 MiB transport bound, not 1 MiB; retain over-1-MiB and over-cap witnesses.                                                                                                                                                                                                   |
| A-D02 / H10 recovery subset               | Source                                 | Durable discovery lane and atomic recent handoff recover provider-visible missed new-run notifications; no all-rerun completeness claim.                                                                                                                                                                                     |
| A-D03                                     | Source                                 | Irreducible saturated windows consume permitted subsequent pages before retaining truncation.                                                                                                                                                                                                                                |
| A-D04                                     | Source                                 | Request admission places rejection sends inside its absolute deadline; server/socket behavior remains operational proof.                                                                                                                                                                                                     |
| A-D05                                     | Source                                 | Negative numeric fixtures use lexical JSON, not canonicalization that changes the tested input.                                                                                                                                                                                                                              |
| A-R01                                     | Source; prior report corrected         | Recheck claim selection has an alternate acquisition-count-bounded query when the exhausted oldest row cannot create a gap. Never remove that fairness branch as dead code.                                                                                                                                                  |
| A-R02 / H4 drain subset                   | Source + Proof                         | Four independent continuous worker lanes replace the old one-item maintenance turn; offered-load capacity remains WP7.                                                                                                                                                                                                       |
| A-R03 / H4 reuse subset                   | Conditional                            | Identity alone cannot authorize skipping provider reads; preserve explicit rescan and partial refinement. WP7 measures actual redundant-read cost before adding cached-completion authority.                                                                                                                                 |
| A-R04                                     | Source + Proof                         | History worker, collection, provider, delivery and pending-source alerts exist. Validate active-scope selection and disabled/paused behavior; do not infer a missing alert from an old report.                                                                                                                               |
| A-P01                                     | Proof                                  | A call-phase mutation kill is not a guard-specific causal witness. WP6 binds high-risk falsifiers without inflating the mutation claim.                                                                                                                                                                                      |
| A-P02                                     | Source; prior report corrected         | Vitest includes History, Observation and BudgetPolicy editors in the coverage population. Preserve thresholds.                                                                                                                                                                                                               |
| A-P03                                     | Work                                   | WP5: final-image digest-bound OS vulnerability evidence and fail-closed advisory policy. No specific exploitable CVE is alleged.                                                                                                                                                                                             |
| A-G01                                     | Source                                 | Authorized bounded archive pages and analytics exist; status counts alone were not closure evidence.                                                                                                                                                                                                                         |
| A-G02                                     | Source + Proof                         | History controls and archive browser exist; preserve scope/session/unknown-outcome handling and browser qualification.                                                                                                                                                                                                       |
| A-G03 / H13 detail subset / S-PERSIST-003 | Work                                   | WP2: allowlisted detail import, first durable import time, bounded expiry, and no resurrection.                                                                                                                                                                                                                              |
| A-G04 / H13 policy subset                 | Source + Proof                         | Preview/apply binds revisions, cutoff and digest; require current native race witnesses.                                                                                                                                                                                                                                     |
| A-G05 / S-PERSIST-004                     | Work + operational boundary            | WP3: explicit generation-fenced primary-store erasure and new-dataset admission; backup restore reconciliation is WP7, not a SQL-delete claim.                                                                                                                                                                               |
| A-G06                                     | Partial; source-evidence work open     | Versioned purpose mapping and cohort quality exist, but historical workflow source is not populated. An absent workflow blob SHA must stay unknown, never be invented from a job name or current default branch. WP4 qualifies projections; exact historical-source binding remains required for known longitudinal cohorts. |
| A-G07                                     | Proof                                  | WP7: exact native, deployment, sustained recovery, quotas, load and backup/restore qualification.                                                                                                                                                                                                                            |
| H1                                        | Source                                 | Archive preserves inconsistent raw timestamps while withholding derived timing; current-evidence admission remains strict.                                                                                                                                                                                                   |
| H2                                        | Conditional                            | Nonterminal provider state no longer consumes failure attempts. A systemic cooldown requires failure-class/latency evidence and must not erase explicit gaps.                                                                                                                                                                |
| H3                                        | Conditional                            | Whole-attempt provider budget is finite; usefulness for large attempts requires WP7 measurement, not an arbitrary timeout increase.                                                                                                                                                                                          |
| H5                                        | Conditional                            | Fingerprint evolution needs a concrete version transition, not speculative dual-format storage.                                                                                                                                                                                                                              |
| H6                                        | Conditional                            | Separate trust-boundary admission is not automatically duplicate authority; parity work must identify independent operands.                                                                                                                                                                                                  |
| H7 / H8                                   | Proof                                  | Measure current schema-admission and shared-pool costs after the existing batching changes; do not reuse predecessor cost estimates.                                                                                                                                                                                         |
| H9                                        | Rejected as auth bypass                | Bounded malformed input can return 422 before authentication; effects remain behind authentication, role and CSRF admission. Reopen for an actual secrecy or resource counterexample.                                                                                                                                        |
| H10 non-recovery subset                   | Rejected/duplicate                     | Documentation/activation claims must bind their own snapshot; the actual recovery issue is A-D02.                                                                                                                                                                                                                            |
| H11                                       | Rejected                               | Actor is part of the command digest; changed-actor replay conflicts before receipt-integrity decoding. Clock anomalies intentionally yield unavailable.                                                                                                                                                                      |
| H12                                       | Conditional                            | Lax internal datetime construction is not a proved public bypass; retain exact public/SQL admission.                                                                                                                                                                                                                         |
| H14                                       | Source                                 | A failed nonblocking observation guard rolls back the delivery pair instead of waiting while holding the audit head.                                                                                                                                                                                                         |
| H15                                       | Conditional                            | Repeated subdivision is an efficiency question; no current coverage loss is established.                                                                                                                                                                                                                                     |
| H16                                       | Source                                 | Defaults no longer need UPDATE; permanent statistics no longer need ordinary DELETE. WP3 must not silently restore broad runtime deletion rights.                                                                                                                                                                            |
| S-ARCH-01                                 | Source                                 | Application imports use the public worker-ID validator, not the private observation helper.                                                                                                                                                                                                                                  |
| S-API-SEC-001                             | Source                                 | Operator success/error responses use no-store.                                                                                                                                                                                                                                                                               |
| S-R03 route labels                        | Source; initially rejected incorrectly | A whitelist admitting only installation/repository placeholders mapped other valid templates to unmatched. Exact mounted route-catalog binding closes that counterexample without raw-path cardinality.                                                                                                                      |
| S-R04 Prometheus                          | Rejected                               | `max` deliberately answers a service-level terminal-failure question. Loss of instance/job labels does not falsify that contract; it would falsify a separately required per-instance alert.                                                                                                                                 |
| S-AB-02 import cycle                      | Rejected                               | The reported edge is under TYPE_CHECKING; a static graph cycle is not an executable import cycle. Reopen for an actual eager import path.                                                                                                                                                                                    |
| S-HIST-01                                 | Rejected                               | The gap uses the old claim checkpoint, preserving the pending population; the hypothesized truncation used the successor checkpoint incorrectly.                                                                                                                                                                             |
| S-FE expired lease                        | Improvement                            | Expired allocation is not proof a worker runs. Preserve truthful wording and add direct temporal presentation oracles in WP4.                                                                                                                                                                                                |
| S-FE revision equality                    | Rejected                               | Scan progress revision and configuration revision have independent owners; equality is not an invariant.                                                                                                                                                                                                                     |
| S-PERSIST-001                             | Conditional + Proof                    | Domain claims have exact 60-second leases while the scan SQL envelope admits an interval. Prove actual writer admission and boundary rejection before choosing a versioned schema tightening. Published migrations are immutable.                                                                                            |
| S-PERSIST-002                             | Conditional + Proof                    | Delivery-ID FK does not itself bind all duplicated columns; transfer independently revalidates the source. WP6 tests that boundary rather than assuming misdelivery or adding a redundant FK.                                                                                                                                |
| S-TEST-001                                | Proof                                  | Two-connection child-insert versus retained-source cascade race through the shared observation guard, not a details-parent race.                                                                                                                                                                                             |
| S-TEST-002                                | Source + Proof                         | Native collection/store bridge exists; qualify worker cancellation and transactional integration without inventing a monolithic fixture requirement.                                                                                                                                                                         |
| S-TEST-003                                | Work                                   | WP4: unequal nondefault scope IDs must occur in the actual request path and response admission.                                                                                                                                                                                                                              |
| S-TEST-004                                | Work                                   | WP4: distinct timestamps assert the correct label, machine datetime, title and rendered value; field-swapping must fail.                                                                                                                                                                                                     |
| S-TEST-005                                | Source + Proof                         | Empty-lane witnesses must use the current four-lane worker and its idle/stop contract, not the removed drain.                                                                                                                                                                                                                |
| S-DOC-01                                  | Work                                   | This ledger retains original namespaces, refuters and source snapshot provenance; preserve report hashes with the implementation receipt.                                                                                                                                                                                    |
| S-DOC-02                                  | Work                                   | WP6: test the current provider/item/lease budget relation; the 55-second delivery budget is no longer the collection parent.                                                                                                                                                                                                 |
| S-UI-CONTRACT-01                          | Conditional                            | Server admits dataset/scan generation and configuration coherence in one statement. Client scan revision need not equal configuration revision; redundant epoch fields require an actual independent client obligation.                                                                                                      |
| S-PERF-01                                 | Proof                                  | Old 3-by-16 burst arithmetic does not describe the current worker. WP7 measures its current workload and connection use.                                                                                                                                                                                                     |
| S-RACE-02                                 | Conditional                            | Provider read after configuration change has no durable write authority; current generation/configuration CAS fences completion. Measure unnecessary work separately.                                                                                                                                                        |
| S-RACE-03                                 | Conditional                            | Cooperative cancellation does not prove atomic physical-time completion. Terminal SQL lease CAS is the storage authority.                                                                                                                                                                                                    |
| S-OBS-01                                  | Obsolete premise                       | The history collector is no longer a maintenance callback; qualify worker outcomes at their current owner.                                                                                                                                                                                                                   |
| S-GH-SEC                                  | Conditional                            | Token MIME/grammar concerns require an exact reachable counterexample and token-response owner; do not claim credential compromise.                                                                                                                                                                                          |
| S-AB-03                                   | Source + Proof                         | Current CommonJS signal grammar is pinned and admits static requires/exports, retaining unknown for dynamic forms; metrics do not establish architecture failure.                                                                                                                                                            |
| S-OpenAPI freshness                       | Proof                                  | Generated-contract equality and mounted native API campaigns must bind the candidate head, not merely checked-in schema bytes.                                                                                                                                                                                               |
| S-Alert rule fixtures                     | Source + Proof                         | `deploy/observability/ci-coordinator.rules.test.yml` and `scripts/prometheus_rules.py` now provide pinned native PromQL firing/clear witnesses. Exact candidate execution remains required; Python metric tests alone are not equivalent.                                                                                    |

## Selected Integration Decisions

### WP1: Historical Reconciliation

Keep one finite ledger linking every historical issue to its owner and acceptance
predicate. Retain aliases rather than multiplying work by duplicate report IDs.
Do not alter old designs to make their historical status look current. A source
correction must name the exact implementation, its falsifier and the independent
result that qualifies it. Newly discovered defects are separate rows, never
silently folded into a rejected hypothesis.

### WP2: Optional Detail Lifecycle

Reuse bounded Actions job pages, existing policy resolution, dataset scope
locks, quota accounting and the existing detail child relation. Do not retain
raw provider JSON, logs, artifacts, actor profiles or credentials. A detail
representation is versioned and allowlisted, answers a concrete step-level
drill-down question, binds exact attempt/head/job identities, and has finite
per-field, per-job, per-attempt and aggregate byte bounds.

The persistence boundary alone assigns first successful import time and applied
policy/reference, in the same transaction as detail bytes and quota. Replay,
partial failure and rollback cannot renew that time. Disabled future policy
does not delete old detail. Expired detail cannot be reimported by normal scan,
repair or a policy extension. Existing not-imported summaries remain eligible
when policy later enables first import. Provider I/O never holds a SQL transaction.

Scheduled expiry must be wired even while collection is paused. Reuse the
existing bounded cleanup instead of a second retention engine. Failure is
visible and retryable; cleanup cannot remove statistics or active evidence.
Select the smallest existing worker/maintenance integration preserving its
deadline and shutdown ownership. A new persistent scheduling relation requires
proof that existing claim/cursor authority cannot provide bounded fair progress.

The selected cleanup wiring is `RuntimeMaintenanceRound`, operation
`ci_history_detail_cleanup`, with a 10-second enclosing monotonic deadline.
`TransactionalHistoryStore.expire_details` commits one existing bounded SQL
cleanup batch only when rows change. `HistoryDetailCleanup` starts no work if
already aborted, propagates cancellation and storage failures, and creates no
detached task. The maintenance owner delays all optional operations until after
initial reconciliation and records success/failure/timeout using its fixed
metric and diagnostic label catalogs. This adds no queue, schema or privilege.
Ten seconds is a conservative bound, not measured cleanup throughput.
The existing maintenance outcome counter also supplies a service-level cleanup
warning for repeated `failed` or `timed_out` results, independent of import pause.
Reuse the history alert family's three estimated observations in fifteen minutes,
five-minute pending period and fifteen-minute recovery hold. Apply `increase`
before aggregating targets. Native fixtures isolate both failure outcomes,
successful/foreign/unrelated series, reset and eventual clear. This detects
observed cleanup failures, not silent process loss or exact expired-row counts.

#### Admitted First-Import Slice

Use a typed `HistoryAttemptObservation` sidecar containing existing statistics
and optional `ArchivedAttemptDetail`. The provider derives it from the already
admitted job pages; it performs no additional request. Existing summary-only
provider results remain valid. The optional payload is
`ci-economics-archive-detail/v1`: exact attempt/head identity, sorted unique
provider job IDs, and per-job ordered unique step ordinals, status, conclusion,
start and completion instants. Exclude step names and arbitrary labels: the
question is numbered-step outcome/timing, not unrestricted provider drill-down.
Bound each job to 256 steps, the attempt to the existing 2,000-job maximum,
and canonical detail bytes to 262,144. Preserve inconsistent raw instants;
never derive a negative duration. Unsupported or malformed optional detail
does not invalidate independently valid permanent statistics.

Import detail only for a complete, conflict-free statistical population whose
job IDs exactly equal the detail job IDs. Missing step arrays mean unavailable
detail, not an empty imported payload. A valid explicitly empty step array is
an observed empty step population. Oversized, partial or unavailable detail
leaves `not_imported`, allowing a later explicit rescan to try again.

Completion invokes the detail writer after successful statistics admission,
including the `replayed` branch, inside the existing contribution savepoint.
It loads the current parent's retention record and resolves current defaults
under the same transaction. `not_imported` plus enabled policy admits insert,
first-import anchor, applied reference and exact byte quota delta together.
Retained/expired states are no-op, never clock renewal. A detail quota refusal
rolls back the entire contribution savepoint and preserves the pending cursor;
gap/scan/recheck completion retains its existing terminal lease CAS. Conflicting
or incomparable statistics never admit a new detail sidecar.

The explicit existing rescan is the retry/backfill mechanism for previously
summary-only records after enabling detail. No new background queue or automatic
configuration-time rescan is introduced. The UI must state that enabling future
detail does not itself revisit old records. A future automatic backfill would
be a separate scheduling contract. Public detail reads must validate the stored
versioned payload, exact parent/job bindings, logical expiry and response byte
bound before exposing it; legacy unknown formats remain unavailable.

Expose content on the additive exact-attempt route
`GET /api/v2/economics/repositories/{installation_id}/{repository_id}/history/attempts/{workflow_run_id}/{run_attempt}/detail`
with required generation. Its new versioned response may carry the admitted
detail payload; the existing archive response and its `unavailable_format`
vocabulary stay byte-contract compatible. Reuse existing history read use-case
authorization, scope checks, one-statement dataset/parent snapshot, read bulkhead
and no-store/error semantics. An internal optional page payload is not emitted
by the old response projector. Admit exact current-generation parent/head/job
identity and logical expiry before projection. UI loads this small bounded
payload only for an explicitly selected attempt, never as part of a records
page or an automatic poll.

Enforce the optional sidecar budget during collection, not only at final
serialization. Once the bound is exceeded, discard the accumulated sidecar and
stop decoding further steps while continuing statistics collection. On reads,
bound the canonical job aggregate in SQL before returning arrays to the driver;
retain every identity column required by the existing statistics decoder.
This reuses its canonical/projection consistency proof instead of introducing a
second, weaker identity-only decoder. It costs a bounded SQL byte-count pass;
revisit only if measured query cost justifies an equally complete projection.
Browser admission independently binds nonnull content to retained, unexpired,
complete and conflict-free parent metadata and the same head SHA. A nullable
payload remains a valid unavailable-content result. The development proxy must
admit the exact new route with one required generation and no extra parameters.

### WP3: Dataset Erasure And Restart

Erasure is an explicit authenticated, scoped, audited operation, never a config
side effect. Preview binds generation, configuration/data revisions and cutoff;
application rechecks every operand and command identity. Erasure first fences
the dataset generation and invalidates old claims, then removes retired data in
bounded resumable transactions. Occupied quota decreases only with actual
deletion. The scope tombstone remains, and old reads/cursors cannot expose a
retired generation. Ordinary rescan cannot reactivate an erased scope.

Separate administrative authority from ordinary collection privileges. An
explicit new-dataset operation can begin a new generation only after erasure
completes. It is not restoration of deleted data. Backup restore must reconcile
the externally preserved deletion fence before reads/writes become available;
until that operational mechanism is qualified, all-copy erasure and safe backup
restore remain unavailable claims. Do not weaken audit receipts or rewrite
historical command meaning when a dataset generation changes.

The selected delivery direction is a separate maintenance CLI using an admitted
migration principal, not an online irreversible HTTP command. The current
runtime ACL deliberately excludes permanent archive DELETE and application
schema policy rejects SECURITY DEFINER routines. A new erase-capable online
principal/service would add an unneeded authority and operational boundary.
The CLI must admit the actual session/schema owner, exact database identity,
capability declaration and reviewed command before any destructive statement;
possession of an arbitrary DSN or caller-supplied actor is not sufficient.

Implementation requires a separately declared, forward-migrated erasure
capability and bounded operation/fence metadata. Do not reinterpret the existing
`ci-actions-history-archive/v1` destructive domain. Begin must atomically record
command identity, increment generation, set `enabled=false`/`erasing`, and
invalidate claims. Each resumable batch binds the same operation and retired
generation, deletes children before parents, subtracts only actual deleted
canonical bytes/counts and rolls back on discrepancy. Preserve the scope fence
and append-only audit. Existing shared webhook observations are not archive
data-erasure targets. Whether transient inbox receipts survive must follow
their ingestion/retention owner, not an archive-wide blanket delete.

An exact old command replay after restart returns its old receipt without
touching the new generation. Restart needs a new command and explicit selected
population; the UI/CLI must state whether that population permits historical
reimport. Keep a coherent bounded status projection while erasing/erased;
deleting scans without changing the current dataset/scan-pair contract is
forbidden. A new schema/status contract needs its own versioned admission.

The destructive writer is not ready until the independent deletion-fence owner
and restore admission are bound to an actual durable source outside the database
backup being restored. A digest supplied by the same database or an operator
flag is not that source. Required external coordinates are logical database
identity, authoritative fence store, authentication/integrity, durability and
restore/drain entrypoint. Neither this design nor a migration principal proves
those facts. Code for unrelated packages may proceed, but G05 remains open;
do not ship a no-op fence adapter or claim all-copy erasure.

### WP4: Observable API And UI Contracts

Use deliberately unequal nondefault scope IDs, independent revisions and
distinct timestamps in fixtures. Test semantic fields and exact transport
coordinates independently. Preserve unknown cohort provenance and absent CPU
measurements. Do not synthesize historical workflow source identity from the
current branch. Versioned purpose mappings remain query projections rather than
rewrites of measured facts.

### WP5: Release Vulnerability Evidence

Scan the final immutable OCI subject with an admitted pinned scanner. Bind
scanner identity, advisory database identity/freshness, platform, image digest,
policy and result. Missing, stale, malformed, partial, scanner-failed or
wrong-subject evidence fails closed. SBOM/provenance remain separate artifacts.
High/critical findings follow an explicit severity/fix-availability policy;
exceptions must be scoped, justified and expiring, not a blanket ignore list.
An upstream scanner is preferable to a new vulnerability database/parser.
Release publication must not be represented as passed before this gate passes.

### WP6: Targeted Causal Proof

Preserve the current temporal inequalities: provider work fits inside an item
budget, which fits inside its lease, with cooperative cancellation and a
database-time terminal CAS. Bind tests to the constants' actual owners. Do not
introduce a general configuration framework or revive obsolete drain limits.

Use independent barriers for the source/inbox race and parameter-isolated
negative cases for scope, stale claims, policy and payload limits. A generic
exception, setup failure or timeout is not a causal kill of the intended guard.
New tests run through the repository-owned GitHub route. Local checks are
restricted to admitted bounded static analysis and contract conformance.

### WP7: Qualification Boundary

Qualify exact candidate native tests and required CI jobs before claiming code
closure. Sustained import, provider failures, schema-admission queries, pool
occupancy, quota pauses and restore fences need measured workloads and named
environments. Code changes cannot manufacture those receipts. This request
does not authorize deploying to production, deleting live data, changing
secrets or weakening external policy. Any required such action must be surfaced
as a distinct authorization boundary.

## Alternatives, Costs And Revision Conditions

| Decision                       | Lower-cost alternative and why insufficient                                                   | Residual cost and falsifier                                                                      |
|--------------------------------|-----------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| Current-source ledger          | Implement every old bullet literally; introduces redundant fixes and false invariants.        | Maintain finite references; retire duplicated rows only with exact alias proof.                  |
| Existing bounded cleanup       | New lifecycle engine; adds authority without a missing predicate.                             | Shared maintenance resources; revisit on measured starvation or deadline failure.                |
| Exact generation fence         | Delete rows directly; permits stale workers and old cursors to resurrect/expose retired data. | Additional command/recovery proof; revisit only with an equivalent existing owner.               |
| Native targeted witnesses      | More coverage percentages alone; cannot distinguish field swaps or lock races.                | Fixture maintenance; simplify when an existing oracle independently falsifies the same guard.    |
| Immutable-image scanner        | Treat SBOM as a security verdict; omits advisory matching and policy.                         | Advisory availability/freshness and scanner trust; change tool only with equivalent evidence.    |
| Unknown cohort remains unknown | Infer logical identity from display names; creates false longitudinal equivalence.            | Reduced comparable population; admit richer identity only from exact historical source evidence. |

The writer must record a readiness row per changed owner: intended delta,
protected observations, derived surfaces, narrow whole-chain gate and independent
validator. Independent validation uses the current repository review policy and
the caller's explicit model override. Reviewers see a quiescent candidate;
semantic changes invalidate dependent review and test evidence.
