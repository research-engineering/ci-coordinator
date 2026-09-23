# CI Economics And Regression Telemetry

Status: accepted design
Last updated: 2026-09-04
Owner requirement: `REQ-CI-RUNTIME-038`

## 1. Decision

CI Coordinator records bounded, provenance-complete GitHub Actions evidence and
publishes repository-scoped CI economics without allowing measurements to
influence omission safety.

The capability separates four facts that are often incorrectly conflated:

| Fact                | Authority                | Admitted meaning                                                          |
|---------------------|--------------------------|---------------------------------------------------------------------------|
| Webhook observation | Verified GitHub delivery | One provider occurrence was delivered with exact payload timestamps       |
| Attempt snapshot    | GitHub REST observation  | One bounded, complete job inventory was observed for an exact run attempt |
| Measurement         | `ci_economics`           | A versioned deterministic derivation from retained observations           |
| Estimate            | Owner-admitted estimator | A non-exact value with explicit assumptions and uncertainty               |

The separation is mandatory because a webhook does not prove inventory
completeness, a REST job response does not expose job creation time, and runner
labels do not prove hardware resources.

## 2. Product Scope

Version 1 provides:

1. normalized completed `workflow_job` and `workflow_run` observations;
2. atomic delivery-claim and observation persistence;
3. fair, revision-fenced collection of complete terminal run-attempt snapshots
   from the existing reconciliation observation path;
4. exact queue, runner-occupancy, and attempt-wall measurements when their
   required evidence is complete;
5. bounded authenticated repository and per-attempt reads;
6. explicit unavailable and partial-evidence reasons;
7. finite retention with bounded cleanup and non-resurrection tombstones;
8. typed, bounded maintenance outcomes; and
9. definition-version and provenance binding for every reported value.

Version 1 does not claim actual CPU utilization, monetary cost, cache hit rate,
or saved compute. Those values require additional owner-admitted evidence.

## 3. Ownership

| Concern                         | Owner                 | Authority                                                                                     |
|---------------------------------|-----------------------|-----------------------------------------------------------------------------------------------|
| Provider payload admission      | `github_ingestion`    | Decode exact GitHub payload fields and reject malformed temporal relations                    |
| Provider REST decoding          | `integrations.github` | Decode and bind a complete bounded run-attempt job inventory                                  |
| Evidence and collection algebra | `ci_economics`        | Own provider port, collection state, exactness, derivations, and non-claims                   |
| Cross-capability orchestration  | `app`                 | Sequence reconciliation evidence, provider reads, durable transitions, and authorized queries |
| Durable storage                 | `persistence`         | Atomic writes, immutable identities, bounded queries, and retention deletion                  |
| HTTP projection                 | `api.http`            | Authentication, role admission, wire bounds, and status mapping                               |
| Process lifecycle               | `runtime`             | Compose operations, enforce per-operation budgets, and expose bounded outcomes                |

Technical Prometheus metrics remain owned by `observability`. Product CI
economics are a separate capability because their identity, retention,
authorization, and correctness rules differ.

## 4. Evidence Model

Let:

- `S = (installationId, repositoryId)` be a repository scope;
- `A = (S, workflowRunId, runAttempt, headSha)` be one attempt identity;
- `J = (A, providerJobId)` be one job occurrence;
- `D = (deliveryId, bodySha256, verifiedAt)` be admitted webhook provenance;
- `W(J)` be a completed workflow-job observation;
- `R(A)` be a completed workflow-run observation;
- `P(A)` be a complete terminal REST snapshot;
- `V` be the measurement-definition version; and
- `K` be trusted database time after the relevant row lock.

Webhook admission is closed:

```text
AdmitWorkflowJob(payload, D)
  = CompletedJob(W) xor Noop xor Rejected

CompletedJob(W)
  => W.identity = J
  and W.createdAt <= W.startedAt <= W.completedAt
  and W.status = completed
  and W.provenance = D
```

Delivery and observation commit together:

```text
NoDelivery(D.deliveryId)
  => atomically InsertDelivery(D) + InsertObservation(W or R)

ExactDeliveryReplay(D)
  => ExistingObservation = ExactNormalizedObservation(D)
  and Writes = 0

ConflictingDelivery(D)
  => Writes = 0
```

An acknowledged workflow observation therefore cannot exist without its
delivery claim, and an acknowledged delivery cannot lose its required
observation.

## 5. Complete Attempt Snapshot

The GitHub REST adapter already traverses the attempt-specific jobs endpoint
with finite page, byte, and cardinality limits. It additionally emits a
content-addressed snapshot only when every decoded job is terminal and two
consecutive bounded reads have the same canonical digest.

```text
CompleteSnapshot(P(A))
  => exact repository id
  and exact workflow run id
  and exact run attempt
  and unique provider job ids
  and observed count = provider total_count
  and terminal pagination
  and every job status = completed
  and Hash(FirstRead.canonicalJobs) = Hash(SecondRead.canonicalJobs)
  and SnapshotDigest = Hash(CanonicalJobs)
```

Snapshot recording is idempotent. An equal identity with equal canonical bytes
is a replay; an equal identity with different bytes is a conflict. Provider
mutation after a terminal snapshot is treated as evidence conflict rather than
silently rewriting history.

Process-local observation time is not part of provider evidence identity. The
database assigns the durable capture time after the stable read. Consequently,
two replicas that read byte-identical provider facts produce an exact replay,
not a conflict caused by clock skew.

Webhook observations and REST snapshots remain separate relations. This keeps
their independent provenance visible and prevents an incomplete webhook stream
from masquerading as a complete inventory.

## 6. Measurement Algebra

The initial definition is `ci-economics-measurement/v1`.

For a job with matching webhook evidence and a job in `P(A)`, define the common
projection over every fact exposed by both sources:

```text
Shared(J) :=
  identity + name + conclusion + startedAt + completedAt + labels + runner

SourcesAgree(J) := Shared(W(J)) = Shared(P(J))
```

Only then are cross-source measurements admitted:

```text
QueueMs(J) = milliseconds(W(J).startedAt - W(J).createdAt)
OccupancyMs(J) = milliseconds(P(J).completedAt - P(J).startedAt)
```

For a complete attempt:

```text
AttemptWallMs(A)
  = milliseconds(max(P.jobs.completedAt) - min(W.jobs.createdAt))

RunnerOccupancyMs(A) = sum(OccupancyMs(J) for J in P.jobs)

QueueCoverage(A)
  = count(P.jobs with exact matching W) / count(P.jobs)
```

Every value carries its unit, definition version, source identities, and
accuracy class. Queue is `exact` only when every required job webhook exists;
a non-empty proper subset yields a `partial` sum with explicit coverage, and an
empty set yields `unknown`. Attempt wall is `exact` only with complete webhook
coverage because a partial interval is not an attempt bound. If a webhook
exists but any common field contradicts the REST snapshot, every aggregate for
that attempt is `conflict`; no source is silently preferred. Occupancy remains
exact from a complete REST snapshot only when webhook evidence is absent or all
present webhook evidence agrees. A millisecond aggregate outside the exact
JSON/JavaScript integer range is `unknown` with
`duration_sum_out_of_range`; it is never published as a rounded exact value.

Actual CPU time is not derivable from GitHub timestamps:

```text
RunnerOccupancyMs != CPUUtilizationMs
RunnerLabels != AllocatedCoreCount
```

An estimated CPU value may be added only when an owner-admitted runner profile
binds the exact runner identity or label predicate to a resource allocation,
profile epoch, estimator version, and uncertainty. Unknown self-hosted hardware
therefore produces `unknown`, never an inferred core count.

## 7. Selected And Full-CI Relation

The reconciliation subject and contract bind a run attempt to exact planning
evidence. They prove a planned route, not plan consumption. Classification is
therefore deliberately named `plannedRoute`:

```text
candidateEvidence present => plannedRoute = full_ci_counterfactual
candidateEvidence absent and planningEvidence present => plannedRoute = selected
otherwise => plannedRoute = unknown
```

`plannedRoute = selected` does not prove that the caller used the returned
selection. A future consumption receipt must bind the signed plan envelope to
the jobs actually dispatched before any exact selected-versus-full savings
claim is admitted.

Two attempts are comparable only when the owner-admitted comparison key is
equal. At minimum it includes repository scope, head SHA, config epoch, policy,
diff, graph, validation catalog, planner version, verifier version, and
measurement definition.

```text
Comparable(X, Y)
  => ExactComparisonKey(X) = ExactComparisonKey(Y)
  and CompleteMeasurement(X)
  and CompleteMeasurement(Y)

SavedOccupancyMs = FullCiOccupancyMs - SelectedOccupancyMs
```

This formula remains a future rule until plan consumption is proven. Negative
savings are then valid evidence of regression. Missing or unequal evidence
returns a typed unavailable reason; the service never substitutes an unrelated
historical average for an exact counterfactual.

## 8. Durable Collection Lifecycle

Snapshot collection has an explicit state; absence of a snapshot is not a
state. The capability owns this closed transition system:

```text
pending -> leased -> captured
                  -> deferred -> leased
                  -> terminal_unavailable

captured             -> expired
terminal_unavailable -> expired
expired              -> purged
```

Registration is allowed only for an exact terminal reconciliation subject whose
immutable `createdAt` is inside the profile-owned eligibility horizon. The same
source time anchors collection deadline, evidence retention, and tombstone
retention. Purging an expired tombstone therefore cannot make the old subject
eligible again.

Each lease binds:

```text
PolicyDigest = Hash(exact operational collection policy)
Claim = subjectId + policyDigest + generation + revision + workerId + token + expiresAt

HolderTransition(Claim, K)
  => exact current claim
  and state.policyDigest = claim.policyDigest
  and K < expiresAt
  and winning CAS on the claim revision

SupervisorReclaim(K)
  => K >= expiresAt
  and winning CAS on the same prior revision
```

Registration freezes `PolicyDigest` into durable state. Every acquisition and
holder transition checks the same digest. A deployment may therefore change
collection policy only through an explicit compatibility decision for existing
non-terminal states; it cannot silently reinterpret them under ambient current
settings.

`K` comes from PostgreSQL after the state row is locked. Claim acquisition
increments attempt, generation, and revision. A retry writes a bounded reason,
advances `nextAttemptAt` with capped backoff, and releases the lease. A deadline
or attempt limit produces `terminal_unavailable`; unknown failure classes are
fail-closed into an admitted bounded reason rather than silently disappearing.
The state algebra also preserves causal history: captured evidence requires at
least one claim, `attempts_exhausted` requires the exact admitted attempt limit,
and `evidence_conflict` appears as both failure and terminal reason only on its
terminal path. Domain construction, SQL checks, and catalog attestation enforce
the same relation.

Due work is selected by `nextAttemptAt`, source creation time, and subject ID
under `FOR UPDATE SKIP LOCKED`. Every unsuccessful holder either changes state
or loses its lease. Thus a failing prefix cannot remain the unchanged prefix of
every future selection, which removes poison-head starvation. Collection uses a
profile-owned concurrency bound; adding replicas changes throughput, not
evidence identity or transition authority.

## 9. Storage And Retention

The schema uses separate immutable relations:

| Relation                            | Purpose                                                                            |
|-------------------------------------|------------------------------------------------------------------------------------|
| `ci_workflow_observations`          | Completed run/job webhook facts bound to delivery provenance                       |
| `ci_workflow_attempt_collections`   | Durable collection state, lease fencing, retries, terminal reasons, and tombstones |
| `ci_workflow_attempt_snapshots`     | Complete terminal REST snapshot headers                                            |
| `ci_workflow_attempt_snapshot_jobs` | Bounded per-job facts under one snapshot                                           |

The snapshot header is separate from jobs so repository summary pages do not
load an up-to-2,000-job document. Summary queries are keyset-paginated and
scope-filtered in SQL before decoding. A detail read deliberately materializes
the complete bounded attempt because exact aggregate derivation requires every
job and every distinct observation; only its externally returned job list is
keyset-paged. The hard 2,000-job and per-row byte limits bound rows and bytes
materialized in the service. The attempt index matches the detail filter and
ordering, but these limits do not prove a data-independent bound on PostgreSQL
tuples inspected when multiple retained deliveries describe one occurrence.
Scan-cost adequacy therefore requires an `EXPLAIN` and workload capacity receipt;
it is not inferred from the result limit. Duplicating the measurement algebra in
SQL or maintaining a second materialized aggregate is deferred until that
evidence proves the result-bounded model insufficient.

Schema admission covers behavior-bearing PostgreSQL metadata, not names alone:

```text
SchemaAdmitted
  => exact columns + constraints + indexes + tables + routines
  and exact unconditional trigger shape
  and no relation rewrite rules
  and no row-security policies
```

A trigger with a `WHEN` predicate, column-restricted update event, arguments,
constraint/parent identity, or altered enablement is not equivalent to the
retention guard. Any user-defined `pg_rewrite` row is rejected because it can
change insert/update/delete semantics without changing the checked table shape.

Snapshot retention is anchored to the immutable source-subject time, never to a
later retry or replay. Cleanup atomically deletes captured snapshot rows and
transitions their collection state to `expired`:

```text
ExpireBatch(K, limit)
  => only rows with retainUntil <= K
  and count(deleted snapshot headers) <= limit
  and child deletion follows the declared foreign-key lifecycle
  and collection state becomes expired in the same transaction

PurgeBatch(K, limit)
  => only expired tombstones past tombstoneRetainUntil are deleted
  and their source subjects are outside the registration eligibility horizon
```

Webhook observations expire independently. `webhook_deliveries` remains owned
by webhook idempotency and audit policy; CI economics cleanup does not delete or
reinterpret that parent authority. Cleanup runs inside the existing bounded
maintenance lifecycle and does not add a second ambient scheduler. Retention
conformance remains unproven until witnesses demonstrate eventual expiry,
tombstone purge, and impossibility of re-registration.

The complete retention witness must preserve one subject identity across every
transition rather than compose independently seeded end states:

```text
Captured(S, snapshot, jobs)
  -> Expire(S) and SnapshotAbsent(S) and JobsAbsent(S)
  -> Purge(S) and TombstoneAbsent(S)
  -> RegisterEligible(S) = false
```

Because production time is PostgreSQL-owned, tests may project a captured row to
a later coherent database-time epoch only through an explicit test fixture that
shifts every related retention bound by the same delta. Production clock
injection is rejected: it would add a runtime mechanism solely for test control.
The witness must also register one fresh control subject after the purge so a
zero registration count cannot pass vacuously because registration is broken.

## 10. Maintenance Execution

One scheduler remains the process-lifecycle owner, but independent operations do
not form one serial latency chain. After successful primary reconciliation, each
non-authoritative operation runs under its own profile-owned deadline and emits
one closed-cardinality outcome:

```text
MaintenanceOutcome = succeeded | failed | timed_out
RoundBound <= primaryBound + max(nonAuthoritativeBounds)
```

Operation completion and collection-item outcome are different predicates:

```text
MaintenanceSucceeded
  := collection returned one valid CiEconomicsCollectionRound

CollectionRound
  := registeredSubjectCount
     + multiset(aborted | captured | claim_lost | deferred | none_due
                | replayed | terminal_conflict)

MaintenanceSucceeded -/-> ProviderHealthy
count(deferred) > 0 -> CollectionDegradationObservable
```

The operational projection therefore exposes one label-free cumulative count of
registered subjects and one counter labelled only by the closed item-outcome
algebra. An `other` instrumentation bucket contains representation drift without
creating an unbounded label. Repository, run, subject, provider message, and
failure-reason text are forbidden metric labels. The generic maintenance outcome
remains `succeeded` when deferral was durably handled; relabelling it as failure
would conflate an expected capability outcome with scheduler failure.
Every named collection outcome must retain its identity in this projection;
only values outside the closed application algebra may enter `other`.

Operations contain ordinary failures and timeout locally; process cancellation
still propagates. A telemetry failure never changes reconciliation, planning, or
FullCI fallback, but it is observable by operation name and outcome. This keeps
one lifecycle without allowing one slow collector or cleanup task to starve all
other maintenance.

The collection application service projects its completed typed round through
the existing `RuntimeMetrics` sink. A new scheduler result abstraction or
generic event framework is rejected because no second operation has proved the
same result algebra. Instrumentation failure is contained by the metrics owner
and cannot change the collection result or the generic maintenance outcome.
Composition must pass one identical metrics registry to collection, maintenance,
and HTTP exposition; constructing a private collection registry would make the
otherwise valid projection unreachable to operators. The application and
observability owners retain separate closed inventories and a parity witness
rejects any unprojected application outcome.

## 11. HTTP Contract

The initial read surface is:

| Operation         | Method and path                                                                                                      | Role    |
|-------------------|----------------------------------------------------------------------------------------------------------------------|---------|
| Attempt summaries | `GET /api/v1/economics/repositories/{installation_id}/{repository_id}/attempts`                                      | `audit` |
| Attempt jobs      | `GET /api/v1/economics/repositories/{installation_id}/{repository_id}/attempts/{workflow_run_id}/{run_attempt}/jobs` | `audit` |

Both operations require authenticated scope authorization before data access.
Pages have an owner-defined maximum and stable keyset cursor. The external
review bot uses the same API; direct database access is not a supported
integration.

## 12. Safety Properties

Measurements are observational:

```text
TelemetryUnavailable => planning and FullCI fallback remain operational
TelemetryConflict => no omission authority and no historical overwrite
MetricsBudgetExceeded => bounded telemetry failure, never weakened CI
```

Labels, workflow names, branches, and runner names are untrusted bounded text.
Secrets, raw logs, step output, environment values, credentials, and arbitrary
payload extensions are not persisted.

## 13. Minimality Proof

The new capability is justified by independently changing predicates:

1. GitHub payload shape changes independently from measurement definitions.
2. Measurement definitions change independently from HTTP representation.
3. Retention authority changes independently from reconciliation correctness.
4. Product economics authorization differs from technical scrape authorization.
5. Pre-snapshot collection authority changes independently from immutable
   snapshot evidence and therefore requires an explicit state relation.

A single observability table is rejected because it would combine incompatible
identity, retention, and trust semantics. A generic event store is rejected
because no second admitted consumer requires arbitrary provider payloads. A
second scheduler is rejected because the existing reconciliation lifecycle can
execute independently bounded maintenance without another shutdown and health
authority. A generic job framework is rejected because no second capability has
proved identical state, retry, retention, and authority semantics.

## 14. Falsifiers

The design is false if an assigned witness permits:

- acknowledging a workflow observation without its delivery claim or vice versa;
- replaying one delivery with different normalized facts;
- accepting a job with inconsistent run, attempt, head SHA, or timestamps;
- treating a partial REST page as a complete snapshot;
- promoting terminal jobs when two consecutive complete reads differ;
- overwriting a terminal snapshot with different provider facts;
- preserving `exact` after mutating any shared webhook/REST fact;
- treating two semantically equal provider snapshots as conflicting because of
  process-local observation time;
- allowing an expired, substituted, or stale claim to commit a transition;
- applying a current collection policy to state registered under another
  policy digest;
- selecting the same unchanged failing prefix forever while later due work exists;
- reporting queue or attempt wall with incomplete webhook coverage;
- reporting actual CPU utilization from duration or labels;
- comparing attempts with unequal planning or measurement epochs;
- treating a planned route as proof that its returned plan was consumed;
- loading unbounded rows or jobs for one request;
- reading a foreign repository scope;
- retaining secrets or unbounded provider text;
- deleting non-expired evidence, exceeding a cleanup bound, or resurrecting an
  expired subject after tombstone purge;
- producing the same collection-item metric projection for an all-deferred round
  and a round with captured evidence;
- composing collection with a metrics registry other than the one exposed by
  the runtime, or admitting unequal application and metric outcome inventories;
- admitting a conditional retention trigger or any relation rewrite rule;
- letting one non-authoritative operation consume another operation's deadline
  or hide its outcome; or
- changing plan selection, omission proof, or FullCI fallback when telemetry fails.

## 15. Proof Registration

The capability is incomplete unless the same requirement is reachable through
the human context map, machine ownership profile, import policy, documentation
graph, runtime requirements, Proofkit routes, and executable witness matrix.
Each gate remains authoritative only for its declared projection; a locally
green subset cannot substitute for registration completeness.

## 16. Non-Claims And Revision Conditions

Version 1 does not prove webhook delivery completeness, provider clock
synchronization, physical CPU utilization, monetary savings, cache efficiency,
energy use, or organization-wide SLO conformance. It records enough evidence to
state those values as unavailable rather than fabricate them.

Revise this design when owner-admitted runner resource profiles, cache
telemetry, dynamic shard allocation, billing inputs, or external review-bot
commands are introduced. Each addition needs its own authority, failure model,
retention classification, and comparison semantics.
