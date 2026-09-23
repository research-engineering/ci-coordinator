# CI Economics Module Specification

Status: implementation contract

Owner: `ci_economics`

Detailed design:
[CI economics and regression telemetry](../../features/ci-economics-and-regression-telemetry.md)

Collection result and PostgreSQL proof amendment:
[Collection proof closure](../../features/ci-economics-collection-proof-closure.md)

Source-aware v2 and measured reports:
[Measured comparisons](../../features/ci-economics-measured-comparisons.md),
[implementation plan](../../features/ci-economics-measured-comparisons-implementation-plan.md).
Requirements: `REQ-CI-RUNTIME-038` retains the reconciliation observation
contract; `REQ-CI-RUNTIME-042` owns independent sources and reported measurements.
`REQ-CI-RUNTIME-043` owns bounded retained-source and report-pointer catalogs;
the [operator console](../../features/economics-operator-console.md) defines
their read semantics and task-level use.

Persistent budgets: [design](../../features/economics-budget-policies.md),
[implementation plan](../../features/economics-budget-policies-implementation-plan.md).
`REQ-CI-RUNTIME-044` owns stored policies and atomic retained signals;
`REQ-CI-UI-018` owns their browser administration and evidence admission.

Proposed continuous observation: [design](../../features/repository-observation.md)
and [implementation plan](../../features/repository-observation-implementation-plan.md).
These add durable enable/pause and discovery progress; they are not implemented
by the existing manual source and attempt-collection contracts.

## 1. Owned Invariant

The module owns bounded collection state, immutable provider attempt evidence,
deterministic measurement, and finite evidence retention for one exact GitHub
Actions run attempt.

```text
ExactCollectionSource
and StableCompleteProviderSnapshot
and WinningDatabaseTimeClaim
and RetainedProvenance
=> DeterministicBoundedEconomics
```

The result is observational. It cannot select checks, authorize omission,
broaden credentials, or weaken FullCI fallback.

## 2. Boundary

```text
github_ingestion webhook facts -----------+
                                           v
reconciliation terminal subject -> app collection -> ci_economics
provider run -> authorized source registration -----------^
                                  |            |             |
                                  v            v             v
                         integrations/github persistence observability
                                                   |
                                                   v
api/http <- app query <- bounded immutable evidence and derived measurements
```

Dependencies point toward capability-owned contracts:

```text
app, api/http, integrations/github, persistence -> ci_economics
runtime -> app operations and persistence unit-of-work factories
app -> observability for non-authoritative bounded outcome projection
ci_economics -/-> api/http, app, integrations, observability, persistence, runtime
```

## 3. Public Algebra

The capability exposes only:

- exact attempt, job, runner, snapshot, and measurement values;
- a closed collection lifecycle and revision-fenced claim transitions;
- provider, collection-store, retention-store, and bounded-query ports;
- a strict versioned profile; and
- pure cursor and measurement functions.

V2 additionally exposes an inline legacy/provider source discriminant,
immutable scoped job reports and origin, pure compatibility/difference/budget
algebras, and bounded source/report ports. Legacy source cardinality remains
many-to-one per attempt. It is not converted to the unique provider-run source
identity. V1 reads remain reconciliation-shaped; v2 reads require the exact
installation, repository, run, attempt and head identity.

Budget policies add bounded create/update/disable CAS with paired audit replay.
The first accepted report and every matching policy signal share one transaction;
replays preserve the original evaluation. Signal retention follows the report,
not the current policy. The v3 storage capability retires v2 writers through a
fenced forward transition; it does not replace the existing v2 source wire API.

Collection states are closed:

```text
pending -> leased -> captured
                  -> deferred -> leased
                  -> terminal_unavailable

captured | terminal_unavailable -> expired -> purged
```

Every durable state and claim binds the exact operational collection-policy
digest. Every holder transition requires that digest and the exact current
claim before lease expiry. Every reclaim requires trusted database time at or
after expiry. Both compete for one prior revision, so at most one transition
can commit. A retryable failure cannot represent the terminal
`evidence_conflict` outcome. Persisted causal history additionally requires a
claim before capture, exact attempt-limit exhaustion, and paired conflict
failure and terminal reasons.
Claims additionally bind the complete source value. A changed source with the
same queue key cannot inherit a holder's write authority.

The public `TransactionalCiEconomicsStore.record_snapshot` result is
`captured | claim_lost`. Public `captured` describes a collection transition
and its immutable evidence after successful commit and required cleanup.
Private repository outcomes are pre-commit: evidence insertion returns
`captured | replayed`, and either success becomes internal collection
`captured` after the holder CAS. A commit or cleanup failure cannot publish
success and does not imply that no durable effect occurred. A repeated
completion with an old claim is `claim_lost`.
The six public collection-item outcomes are `aborted`, `captured`,
`claim_lost`, `deferred`, `none_due`, and `terminal_conflict`; observability
adds only its bounded `other` fallback. An executable equality witness binds
the independently owned application and metric inventories.

## 4. Evidence Semantics

A REST snapshot is complete only after two equal bounded terminal reads. Its
identity excludes process-local observation time. A retained snapshot contains
provider-only facts; webhook delivery identity remains in the independently
owned observation relation.

Before any cross-source exact value is emitted, every field visible in both
sources must agree. One contradictory webhook fact or webhook job identity not
present in the complete provider snapshot makes all attempt aggregates
conflict. Missing webhook evidence makes only the values requiring webhook
creation time partial or unknown. Any duration aggregate outside the exact
JSON-safe integer range is unavailable rather than rounded at the HTTP/client
boundary.

Reports are producer assertions, not independently attested CPU truth. A
separate Actions audience and fresh scope admission precede provider job
membership checks and the source-locked write. The immutable report slot is
`attempt + providerJobId + sampleKey`; different payloads conflict. Counters
declare microseconds, waited-child/reporter-interval scope and explicit
unavailability. Provider time and job occupancy are not CPU substitutes.
Comparison and one-report caller-threshold results preserve the underlying
evidence and its uncertainty; neither a threshold breach nor missing telemetry
can alter planning. See the design for the exact compatibility operand set.

## 5. Resource And Retention Bounds

The bundled profile owns page, job, text, canonical-byte, retry, lease,
concurrency, maintenance-deadline, cleanup-batch, evidence-retention, and
tombstone bounds. PostgreSQL source time anchors eligibility and retention;
later retries cannot extend either.

```text
PersistedEvidence(x, K) => K < x.retainUntil
Expired(x) => not SnapshotExists(x)
Purged(x) => K >= x.tombstoneRetainUntil
Purged(x) -/-> EligibleAgain(x)
```

## 6. Ownership Projection

| Surface                                                      | Responsibility                                                               |
|--------------------------------------------------------------|------------------------------------------------------------------------------|
| `model.py`                                                   | immutable evidence and result values                                         |
| `collection.py`                                              | pure lifecycle, lease, retry, and terminal transition algebra                |
| `measurement.py`                                             | pure cross-source consistency and duration derivation                        |
| `ports.py`                                                   | capability-owned process-boundary contracts                                  |
| `sources.py`, `discovery.py`, `read_models.py`               | exact source, bounded discovery population and retained read evidence        |
| `reports.py`, `report_payload.py`, `report_ingestion.py`     | immutable report values, shared data-boundary admission and job provenance   |
| `comparison.py`, `budget.py`                                 | pure paired compatibility/differences and one-report threshold evaluation    |
| `budget_policy.py`, `budget_commands.py`, `budget_signal.py` | selector/configuration, CAS command identity and immutable report evaluation |
| `payload_model.py`, `budget_payload.py`                      | shared strict Pydantic boundary and closed budget wire admission             |
| `budget_ports.py`, `app/ci_economics_budgets.py`             | capability-owned storage contract and current scope authorization            |
| `profile.py` and bundled JSON                                | strict resource and retention policy                                         |
| `app/ci_economics.py`                                        | cross-capability collection and query sequencing                             |
| `integrations/github/ci_economics_provider.py`               | bounded stable provider reads                                                |
| `persistence/ci_economics_*`                                 | immutable evidence, claim CAS, retention, and attestation                    |
| `api/http` projections                                       | authenticated bounded transport only                                         |
| `target_artifacts/resources/ci_measurement_reporter.py`      | separately exported, bounded, optional command measurement/upload client     |
| `runtime/maintenance_round.py`                               | independent operation deadlines and outcomes only                            |
| `observability/runtime_metrics.py`                           | bounded non-authoritative collection projections                             |

## 7. Proof Obligations

The capability is not complete until executable witnesses prove:

- provider pagination completeness and stable terminal reads;
- exact semantic replay and contradiction rejection;
- database-time lease boundary races and stale-claim rejection;
- retry progress, fair selection, and bounded terminalization;
- source-time retention, child cleanup, and non-resurrection;
- scope-first keyset reads and finite materialization;
- migration attestation and least-privilege runtime access;
- rejection of conditional or restricted retention triggers, relation rewrite
  rules, and policy-epoch substitution;
- independent maintenance deadlines and fixed-cardinality outcomes; and
- unchanged planning, omission, and fallback behavior on telemetry failure.

V2 adds per-operand report/source/credential/retention falsifiers, legacy
backfill multiplicity, database lock barriers for quota and expiry, strict
JSON before framework parsing, real browser request-integrity admission,
exact receipt identity, command-exit preservation and deterministic API
projection. Static success does not discharge the native GitHub witnesses.

Persistent budgets additionally require both policy/report lock orderings,
complete rollback after signal insertion, historical replay under changed
configuration, v2-to-v3 migration rollback and preserved report bytes, bounded
signal reads, and report/signal expiry followed by tombstone purge without
recollection. Browser tests cover configuration, conflict, unknown outcome,
read-only access and historical signal inspection.

## 8. Non-Claims

The module does not prove webhook completeness, provider clock accuracy,
actual CPU utilization, billing cost, cache efficiency, saved compute, plan
consumption, production capacity, deployment, or organization-wide SLO
conformance. Those claims require separately owned evidence and versioned
relations.
