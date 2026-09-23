# CI Economics And Regression Telemetry Implementation Plan

Status: implemented locally; production qualification remains external
Last updated: 2026-09-04
Design: `docs/features/ci-economics-and-regression-telemetry.md`
Owner requirement: `REQ-CI-RUNTIME-038`

## 1. Completion Predicate

```text
Complete :=
  DesignAccepted
  and WebhookFactsStrictlyAdmitted
  and DeliveryAndObservationAtomic
  and CrossSourceContradictionsCannotBeExact
  and CollectionLifecycleDurableAndFair
  and CollectionPolicyEpochBound
  and LeaseAuthorityRevisionFencedByDatabaseTime
  and EquivalentReplicaObservationsReplay
  and TerminalSnapshotComplete
  and MeasurementsDeterministic
  and MissingEvidenceExplicit
  and RepositoryReadsBoundedAndScoped
  and RetentionDeletionExecutable
  and ExpiredSubjectsCannotResurrect
  and MaintenanceBudgetsIndependentAndObservable
  and ArchitectureRegistrationComplete
  and PlanningIndependentFromTelemetry
  and ProofBindingsComplete
  and ExactHeadFullCheckGreen
  and IndependentCloseoutHasNoUnresolvedP0P2
```

Actual CPU utilization, saved compute, cache efficiency, regression alerting,
and external review-bot commands are not completion predicates for this first
evidence-bearing telemetry batch.

## 2. Ordered Implementation

### Step 1: Freeze Normative Contracts

1. Add blocking `REQ-CI-RUNTIME-038`.
2. Add a versioned telemetry profile with bounds, retention, measurement
   definition, accepted events, and persisted fields.
3. Add an exact HTTP profile for the two bounded read operations.
4. Update control-plane event and operation inventories.

Exit condition: every persisted field, derivation, and endpoint has one owner,
one unit, one bound, and one explicit non-claim.

### Step 2: Admit Provider Facts

1. Add `workflow_job` completed-event normalization.
2. Extend `workflow_run` normalization with only admitted temporal facts.
3. Reject cross-field identity and timestamp inconsistencies.
4. Replace delivery-only commit with a webhook-ingestion commit that atomically
   records the delivery and any required normalized workflow observation.

Exit condition: no successful workflow ingestion can commit only one half of
the delivery-observation pair.

### Step 3: Add Durable Collection Authority

1. Add a capability-owned collection state and pure transition algebra for
   `pending`, `leased`, `deferred`, `captured`, `terminal_unavailable`, and
   `expired`.
2. Register only terminal subjects inside the immutable source-time eligibility
   horizon.
3. Acquire fair claims under `FOR UPDATE SKIP LOCKED`; bind exact subject,
   collection-policy digest, generation, revision, worker, token, and
   database-time lease.
4. Persist every retry, unknown failure, terminal outcome, and reclaim through a
   winning CAS; do not infer state from snapshot absence.
5. Reject causal histories that no transition can construct, including
   retryable evidence conflict, premature attempt exhaustion, and capture
   without a claim.
6. Define capability-owned provider port and typed unavailable outcomes.

Exit condition: every eligible subject either progresses or has a durable
bounded reason, and an unchanged failing prefix cannot starve later work.

### Step 4: Record Complete Attempt Snapshots

1. Extend strict REST job decoding with bounded timing, labels, and runner facts.
2. Factor complete attempt loading from signal projection.
3. Record a snapshot only when every job is terminal, pagination is complete,
   and two consecutive bounded reads have the same canonical digest.
4. Bind snapshot identity to the exact reconciliation subject and contract.
5. Exclude process-local observation time from evidence identity; assign capture
   time from PostgreSQL at commit.
6. Treat equal semantic replay as no-op and unequal replay as a conflict.

Exit condition: every terminal snapshot is complete under provider response and
local resource bounds; no partial observation is promoted.

### Step 5: Add Measurement And Query Algebra

1. Add immutable `ci_economics` value objects.
2. Compare every shared webhook/REST field before deriving any exact attempt
   aggregate; one contradiction makes all attempt aggregates `conflict`.
3. Derive queue, occupancy, wall, coverage, and planned-route metadata from
   exact retained evidence; do not claim execution or comparison readiness.
4. Return typed unavailable reasons for incomplete or unequal evidence.
5. Add a scope-first SQL keyset query for attempt summaries. For detail reads,
   materialize the complete result-bounded attempt exactly once to derive its
   aggregate, then keyset-page only the returned job projection. Align the
   attempt index with the exact filter and order; leave data-independent scan
   cost to an explicit capacity receipt rather than claiming it statically.

Exit condition: equal inputs produce byte-identical projections, and missing
inputs cannot become zero-valued measurements.

### Step 6: Add Retention And Bounded Maintenance

1. Anchor snapshot retention to immutable source time.
2. Atomically delete expired snapshot evidence and transition collection state
   to `expired`; retain a bounded tombstone and then purge it.
3. Compose collection, expiry, and purge into the existing lifecycle with
   independent per-operation deadlines rather than serial budget addition.
4. Preserve generic operation completion separately from collection-item
   outcomes. Project the exact collection round through the existing
   `RuntimeMetrics` sink into a label-free registration counter and a
   closed-cardinality item-outcome counter; do not generalize the scheduler
   result type or add a generic event framework.
5. Bind collection, maintenance, and HTTP exposition to the same composed
   metrics registry. Keep separately owned application and metric inventories
   equal through an executable parity witness.
6. Contain metric projection failure so it cannot change collection state,
   maintenance completion, plan selection, or FullCI fallback.
7. Prove the complete captured-to-expired-to-purged lifecycle under one subject
   identity, including snapshot-child deletion, exact tombstone state, no
   resurrection, and a fresh registration control that excludes vacuous success.
   Attest the complete projected state and restored trigger mode before expiry.
8. Preserve non-expired rows and webhook-delivery ownership independence.

Exit condition: finite retention is an executable postcondition and no second
ambient scheduler exists.

### Step 7: Publish Authenticated API And UI

1. Add `audit`-role repository and per-attempt routes.
2. Reuse control-plane authentication and scope authorization.
3. Add exact Pydantic wire projections and generated OpenAPI/TypeScript clients.
4. Keep measurement policy outside routers.
5. Add generated-client-backed attempt economics views without client-side
   reconstruction of domain facts.

Exit condition: direct database access is unnecessary for UI and external bots,
and every page is finite before materialization.

### Step 8: Register, Prove, And Close

1. Add unit mutation/property witnesses for every shared source field, identity,
   timestamp, coverage, comparison, lifecycle transition, and unknown-estimate
   field.
2. Add PostgreSQL witnesses for atomicity, semantic replay, lease fencing,
   reclaim races, fairness, retry closure, paging, retention, no resurrection,
   unconditional trigger shape, rewrite-rule absence, policy-epoch binding,
   capabilities, privileges, and migration compatibility.
3. Demonstrate that an all-deferred collection round emits both generic
   `succeeded` completion and a non-zero `deferred` item outcome, while a metrics
   backend failure remains non-authoritative. Preserve every member of the
   closed application outcome algebra one-to-one and map only foreign values to
   the bounded `other` bucket.
4. Add a composition witness that rejects a collection-private registry and a
   privileged time-projection witness that checks affected relations, restored
   trigger mode, decoded state invariants, parent source time, snapshot retention,
   and child presence before lifecycle execution.
5. Add provider-adapter witnesses for pagination, terminal completeness, exact
   conclusions, stable reads, and typed unavailability.
6. Register the capability in the context map, ownership profile, import rules,
   docs index, runtime requirements, Proofkit routes, source digests, and witness
   plans; make missing registration fail closed. The installed-wheel witness must
   prove exact CI-economics profile inventory, byte parity, and semantic admission.
7. Run allowed local static gates only.
8. Publish one additive branch and obtain exact-head GitHub checks.
9. Run one `gpt-5.6-sol/max` frozen-head review, fix valid findings, rerun checks,
   and squash merge.

## 3. File Topology

New ownership units:

| Path                                                | Single responsibility                                                 |
|-----------------------------------------------------|-----------------------------------------------------------------------|
| `ci_economics/model.py`                             | Evidence identities and measurement result algebra                    |
| `ci_economics/measurement.py`                       | Pure deterministic derivations                                        |
| `ci_economics/collection.py`                        | Pure collection lifecycle and claim authority                         |
| `ci_economics/ports.py`                             | Capability-owned provider, storage, and query ports                   |
| `app/ci_economics.py`                               | Collection sequencing, authorization, and bounded query orchestration |
| `api/http/ci_economics_contracts.py`                | Versioned wire DTOs                                                   |
| `api/http/routers/ci_economics.py`                  | HTTP admission and result projection                                  |
| `persistence/_schema_ci_economics.py`               | SQLAlchemy metadata for immutable evidence                            |
| `persistence/ci_economics_collection_repository.py` | Registration, fair claim, CAS transitions, expiry, and purge          |
| `persistence/ci_economics_repository.py`            | Atomic immutable evidence writes and bounded reads                    |
| `persistence/ci_economics_schema_contract.py`       | Declarative exact capability facts                                    |
| `persistence/ci_economics_schema_attestation.py`    | PostgreSQL catalog acquisition and exact comparison                   |
| `runtime/maintenance_round.py`                      | Lifecycle-only independent deadlines and outcomes                     |

Existing files grow only for existing responsibilities: webhook normalization,
GitHub REST decoding, unit-of-work assembly, schema capability registry,
runtime composition, and HTTP router assembly.

## 4. Witness Matrix

| Property                       | Counterexample killed                                                                      |
|--------------------------------|--------------------------------------------------------------------------------------------|
| Delivery-observation atomicity | Either row survives without its required sibling                                           |
| Exact replay                   | Same delivery or snapshot identity accepts changed facts                                   |
| Cross-source consistency       | Any common webhook/REST field changes while an aggregate remains exact                     |
| Replica identity               | Equal provider facts conflict only because local clocks differ                             |
| Lease fencing                  | Expired, substituted, stale-generation, or stale-revision claim commits                    |
| Fairness                       | A failing prefix remains unchanged and permanently hides later due work                    |
| Retry closure                  | Failure leaves no durable defer or terminal reason                                         |
| Failure partition              | `evidence_conflict` enters a retryable transition                                          |
| Policy epoch                   | State registered under one collection policy is interpreted under another                  |
| Job identity                   | Run, attempt, repository, head SHA, or job ID is substituted                               |
| Temporal admission             | `created > started`, `started > completed`, or naive time passes                           |
| Pagination completeness        | Truncated, duplicate, count-changing, unstable, or over-budget page is promoted            |
| Coverage                       | Missing workflow-job webhook produces an exact queue or wall value                         |
| Unit correctness               | Milliseconds are confused with seconds or CPU utilization                                  |
| Estimate honesty               | Self-hosted labels mint a core count without a profile                                     |
| Route versus consumption       | A planned route is reported as consumed without a receipt                                  |
| Comparison identity            | Different head, policy, diff, graph, catalog, or definition is compared                    |
| Scope safety                   | A foreign repository row is materialized or returned                                       |
| Cardinality                    | API or cleanup exceeds its owner-approved bound                                            |
| Retention                      | Non-expired evidence is deleted, expiry cannot converge, or a purged subject resurrects    |
| Maintenance budgets            | One operation consumes another's budget or its outcome is unobservable                     |
| Failure independence           | Telemetry failure changes selection, omission, or FullCI fallback                          |
| Transport parity               | Runtime routes, exact profile, OpenAPI, or generated client diverge                        |
| Compatibility                  | Previous schema capability cannot attest after expand migration                            |
| Trigger authority              | Conditional, column-restricted, inherited, constrained, or argument-bearing trigger passes |
| Rewrite authority              | A relation rewrite rule changes write semantics while attestation remains green            |

## 5. Migration And Rollback

One additive expand migration introduces the four telemetry relations and a
new immutable capability descriptor. Existing capability descriptors are not
widened. An older binary remains valid and has no privileges on the new tables.

Code rollback is admissible while predecessor capability attestation passes.
Database downgrade is allowed only before retained telemetry exists; afterward
recovery is forward-only. No historical observations are synthesized because
missing webhook provenance and timestamps cannot be reconstructed honestly.

## 6. Review Checkpoints

Review the coherent PR at these semantic checkpoints:

1. contracts and migration authority;
2. webhook atomicity and provider decoding;
3. snapshot completeness and retention;
4. measurement/API projections;
5. generated artifacts and Proofkit closure;
6. exact-head independent closeout.

No checkpoint may promote an estimate to a measurement, infer resource facts
from labels, or add an abstraction without an independently changing contract.
