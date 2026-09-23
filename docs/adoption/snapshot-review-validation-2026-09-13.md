# Snapshot Review Validation

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

Status: bounded source adjudication and future-work intake, not a waiver or
production certificate.

## Scope

The supplied 159-line report has SHA-256
`0fb3f4dfda957a66673c31926fcfd75e1550366eeeec40fc573bb27571ef2520`.
Its source is `7e4ef975500eb340fba5bad172c3e1dfc2303df1`, tree
`599c7eca51ae674940ddb976d19c09d58728df52`, relative to base
`fefdeb385c84ae062f8bec04ea0299eafaf35e85`. Git confirms 182 changed files,
21,701 additions and 191 deletions. Its later route-label, alert-label and
type-only-import clarification is restated below as separate claims.

This is a historical source adjudication, not a finding or acceptance receipt
for the public repository. Earlier source changes mean the old report cannot
establish that a missing recovery path remains absent. Former private CI
receipts are removed; revalidate every applicable claim against the exact
public source. Neither prior static evidence nor CI proves production load,
every edge case or global optimality.

The bounded universe is the 20 numbered claims, seven atoms from the rejected
list, and the later assertion about alert fixtures. Source evidence below
supports this universe, not the external agents' claimed whole-repository
coverage. Their execution and 509 unresolved applicability triggers are not
independently certified here.

```text
Defect = applicable owner predicate + reachable violating source trace
ProofGap = a required distinction lacks an inspected sensitive witness
Risk = a plausible hazard with unresolved materiality or acceptance premises
Refuted = the stated counterexample contradicts current source or its owner

missing proof != proved failure
one proved failure + other unknowns != proof of overall correctness
```

The result is three current code/policy defects: `ARCH-01`, `API-SEC-01` and
the previously rejected `ROUTE-01`. Other rows include repaired paths,
existing roadmap work, scoped proof gaps and conditional improvements.
No new runtime, schema, dependency, test, workflow or deployment change is
authorized by this planning record. [ROADMAP](../../ROADMAP.md#8-next-work)
owns execution order; existing feature specifications retain product authority.

## Numbered Findings

| Supplied ID    | Current disposition and decisive evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Planning disposition                                                                                                                                                                                                                                                                                                                                               |
|----------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| ARCH-01        | Confirmed boundary defect. Both [history collection](../../backend/src/ci_coordinator/app/ci_history_collection.py) and [observation scanning](../../backend/src/ci_coordinator/app/ci_observation_scanning.py) import `_observation_values.digest`, contrary to [public-domain-only use cases](../architecture/modules/app-use-cases.md#7-forbidden-imports). The history occurrence was new in PR160; the other pre-existed. This is not a god-file/co-ownership verdict.                                                                                             | D1/D9: use a deliberately public capability-owned worker-identity validator without duplicating its predicate or losing fail-fast admission. Align the import gate and its negative cases; do not merely rename every private helper or remove validation.                                                                                                         |
| API-SEC-01     | Confirmed application response-policy defect. `_accepted` and `_error` in [operator controls](../../backend/src/ci_coordinator/api/http/routers/operator_controls.py) omit `no-store`; [app admission](../../backend/src/ci_coordinator/api/http/app.py) does not add it for ordinary override responses. The [identity contract](../architecture/modules/control-plane-identity-and-repository-attestation.md) requires it. Some outer error/body-limit paths already add it. No observed cache disclosure is claimed.                                                 | D1: preserve status/body/WWW-Authenticate and add complete personalized-response cache protection, with mounted-route success/error witnesses. Leave immutable public assets cacheable.                                                                                                                                                                            |
| D02            | Split the claim. New-run missed-webhook recovery was missing at the old epoch, but [independent discovery](../../backend/src/ci_coordinator/app/ci_history_collection.py), [atomic handoff](../../backend/src/ci_coordinator/persistence/ci_history_completion.py) and [PostgreSQL recovery tests](../../backend/tests/integration/persistence/test_history_recent_recovery.py) now exist, including a full active-evidence queue. Old-run reruns outside creation-time coverage still need event recovery or rescan; that limitation does not refute new-run recovery. | Keep the delivered path; retain D4-H1/E1 old-rerun delivery/recovery qualification. Do not recreate the producer.                                                                                                                                                                                                                                                  |
| G03/G05        | Confirmed open capabilities, not a demonstrated corruption: [detail cleanup](../../backend/src/ci_coordinator/persistence/ci_history_detail_cleanup.py) exists, but richer detail import/read and generation-fenced erasure/restore are not delivered end to end. [The existing register](../features/actions-history-recovery-hardening.md) already says so.                                                                                                                                                                                                           | Existing D4-H1 detail lifecycle, archive reads, retention preview/apply and erasure work. No duplicate feature or silent retention activation.                                                                                                                                                                                                                     |
| R04            | Confirmed open history-alert capability. The [rule set](../../deploy/observability/ci-coordinator.rules.yml) has no history failure/backlog alert; generic webhook and scrape alerts do not substitute for background progress.                                                                                                                                                                                                                                                                                                                                         | Existing D4/E1 alert work, joined with OBS-01 instrumentation and named rule fixtures. Enabled/paused, freshness, no progress, quota and normal bounded turns must remain distinguishable.                                                                                                                                                                         |
| P03            | Confirmed release-qualification gap. [Release artifact publication](../../.github/workflows/release-artifact.yml) binds provenance/SBOM, not a final-image vulnerability verdict. Dependency audits are not an OS-image advisory assessment. No particular vulnerability is proved.                                                                                                                                                                                                                                                                                     | D9/E1: digest-bound image/advisory policy, scanner/data freshness, exceptions and failure outcomes before qualified release.                                                                                                                                                                                                                                       |
| DOC-01         | Partly confirmed provenance gap; the supplied conclusion is too broad. The old 19-row summary omits a self-contained source/digest/row-evidence package, but public source, Git history and native witnesses still allow independent evaluation of concrete dispositions. Missing original review provenance does not make every disposition unverifiable.                                                                                                                                                                                                              | D9: retain a bounded redacted claim/evidence index at intake. The older 19-row input digest is `727ef08b4d5085aecbab473066358c659e4707e94396d0ff5eb78ba0d30a2b4b`; this number alone does not make its bytes retrievable. Preserve original hypotheses, exact epochs, counterexamples and later supersession without copying private scratch paths into authority. |
| DOC-02         | Qualified budget-ownership/proof gap, not current drift. Constants 20/45/55/60 agree; [composition tests](../../backend/tests/unit/runtime/test_runtime_dependency_composition.py) already assert 55. The bare inequality does not prove safety because provider, drain, maintenance and per-item lease clocks start at different events.                                                                                                                                                                                                                               | D1/D4: document origins and settlement reserves, then test the intended temporal relation using existing owners. Preserve terminal SQL-time checks. Do not centralize unrelated clocks or claim every single-literal change currently escapes tests.                                                                                                               |
| PERSIST-01     | Confirmed physical-schema limitation, not a 61-second application lease bypass. [DDL](../../backend/src/ci_coordinator/persistence/_schema_ci_history_control.py) requires ordered times; [ObservationLease](../../backend/src/ci_coordinator/ci_economics/observation_scan.py) requires exactly 60 seconds and [scan decoding/CAS](../../backend/src/ci_coordinator/persistence/ci_history_state_store.py) binds the canonical claim.                                                                                                                                  | D4/D9: evaluate a matching database duration constraint and direct-SQL malformed-state witnesses against the owner threat/upgrade contract. Additional DDL is defense in depth, not automatically the best fix. Any adopted schema change is forward-only after merged revision `0011`.                                                                            |
| PERSIST-02     | Confirmed physical projection limitation, not demonstrated misdelivery. The [inbox FK](../../backend/src/ci_coordinator/persistence/_schema_ci_history_delivery.py) binds delivery identity; [ingress](../../backend/src/ci_coordinator/persistence/ci_history_delivery_ingress.py) derives the projection and [transfer](../../backend/src/ci_coordinator/persistence/ci_history_delivery_transfer.py) revalidates source fields/fingerprint. Restricted writers cannot freely update immutable projection fields.                                                     | D4/D9: compare the existing refinement with composite-FK/index cost only if independent writers or DB-only readers require stronger physical enforcement. Test isolated scope and identity corruption. A wider FK would still not prove fingerprint, source-time or whole-body equality.                                                                           |
| TEST-01        | Needs corrected subjects. Ephemeral observations parent the inbox by CASCADE; [archive details](../../backend/src/ci_coordinator/persistence/_schema_ci_history_archive.py) parent-reference permanent attempts with RESTRICT. There is no ephemeral-source-to-detail cascade. Guard/cleanup races exist, but the inspected suites do not close every named child-insert/parent-delete ordering.                                                                                                                                                                        | D4: add the precise supported inbox-producer versus source-cleanup overlap. Detail-import versus attempt-erasure belongs with G03/G05, not an invented current detail/source relationship.                                                                                                                                                                         |
| TEST-02        | Superseded. [The current integration test](../../backend/tests/integration/persistence/test_history_recent_recovery.py) invokes the actual service callable with TransactionalHistoryStore/PostgreSQL and exercises the free/full active queue. [Composition](../../backend/src/ci_coordinator/runtime/composition.py) wires that same callable. This does not prove deployed throughput.                                                                                                                                                                               | Retain the native bridge; E1 still owns sustained workload and deployment evidence.                                                                                                                                                                                                                                                                                |
| TEST-03        | Confirmed narrow oracle gap, not an interpolation defect. [History API tests](../../frontend/tests/historyApi.test.ts) have foreign-response negatives, but successful URL/body cases use 1/1. [The client](../../frontend/src/api/ciEconomics/historyClient.ts) interpolates both supplied IDs correctly.                                                                                                                                                                                                                                                              | D7/API regression work: parameterize successful unequal non-default IDs, exact GET URL and POST body/receipt; cover swapping or hardcoding without duplicating entire suites.                                                                                                                                                                                      |
| TEST-04        | Confirmed component-binding proof gap. [HistoryProgress](../../frontend/src/features/ciEconomics/HistoryProgress.tsx) projects `<time>` values; [formatter tests](../../frontend/tests/format.test.ts) and browser layout/axe tests do not establish every field-to-element binding.                                                                                                                                                                                                                                                                                    | D7: use distinct timestamp values and assert the intended label, text, dateTime and title for each owned field. Preserve raw precision and separately tested display formatting.                                                                                                                                                                                   |
| TEST-05        | Confirmed targeted callable-oracle gap, not an empty-loop bug. `_drain` returns on `none_due`; existing [runtime drain tests](../../backend/tests/unit/app/test_ci_history_collection.py) do not directly assert one empty claim for every current lane. The callable now has four lanes, while `run` intentionally retains three.                                                                                                                                                                                                                                      | D1/D4 test maintenance: one parameterized four-lane empty-work witness, no provider/completion effects and no 16-claim spin.                                                                                                                                                                                                                                       |
| UI-CONTRACT-01 | Not established as a current contract defect. [HistoryStatus](../../backend/src/ci_coordinator/ci_economics/history_administration.py) validates both scan authorities, and [one joined query](../../backend/src/ci_coordinator/persistence/ci_history_queries.py) supplies a coherent projection. The browser is not another database authority. Extra fields from the same server are not independent truth.                                                                                                                                                          | Conditional D7 trigger: independently cached, streamed or combined fragments, per-fragment APIs or a client freshness obligation require explicit epoch provenance before composition. Do not add duplicated fields solely to make the client re-prove the current server predicate.                                                                               |
| PERF-01        | Valid qualification gap. Bounded work and exception isolation do not prove throughput, rate-limit headroom or critical-request progress under shared-pool contention. The current callable has four lanes, not the old three.                                                                                                                                                                                                                                                                                                                                           | Existing asynchronous-processing/E1 matrix: combined HTTP, reconciliation, history, observation and delivery workloads, pool waits, provider quotas, fairness and failure recovery. No broker follows merely from missing measurements.                                                                                                                            |
| RACE-02        | No current write-fencing defect established. Claims commit before provider I/O; [completion](../../backend/src/ci_coordinator/persistence/ci_history_completion.py) independently rejects stale generation/configuration/lease authority. A bounded in-flight read may outlive a configuration change; the current owner explicitly fences writes, not instantaneous external-read revocation.                                                                                                                                                                          | D1/E1: make pause/read/commit boundaries explicit and test configuration changes before/during reads. A stricter no-read-after-pause requirement needs separate product admission and a provider-capable mechanism.                                                                                                                                                |
| RACE-03        | Cooperative-stop clarification, not a demonstrated illegal commit. The owner says an observed abort prevents the next effect; an event becoming set is not atomic cancellation of a transaction already entered through its async awaits. Cancellation and COMMIT ambiguity have separate UoW outcomes.                                                                                                                                                                                                                                                                 | D1/E1: witness abort before an operation, during acquisition/transaction entry and near COMMIT; state which admitted work may settle. Do not promise zero effects after a process Event without a matching linearization mechanism.                                                                                                                                |
| OBS-01         | Valid observability improvement. The inner45-second turn may end normally while [maintenance](../../backend/src/ci_coordinator/runtime/maintenance_round.py) reports callback success. That result is not an assertion that the backlog is empty or the provider succeeded.                                                                                                                                                                                                                                                                                             | D4/R04: bounded per-lane stop reasons such as time/count budget, no work, capacity, abort and error. Do not redefine normal budget exhaustion as a maintenance failure or page on it alone.                                                                                                                                                                        |

## Rejected Hypotheses Rechecked

The identifiers below are scoped to this intake; the clarification's `R03`
and `R04` names must not alias archive reuse and history-alert rows above.

### ROUTE-01: Reopened Defect

[Request observation](../../backend/src/ci_coordinator/observability/request_observation.py)
selects an actual registered route template. The
[metrics sink](../../backend/src/ci_coordinator/observability/runtime_metrics.py)
then permits only two placeholder names. Existing jobs, measurements, source,
report, configuration and cutover routes contain other legal placeholders.

```text
T = /api/v1/economics/repositories/{installation_id}/{repository_id}
    /attempts/{workflow_run_id}/{run_attempt}/jobs

MatchedTemplate(request) = T
_admitted_route(T) = false
RecordedMetricRoute(request) = unmatched
```

This loses a matched-route distinction under the
[template-source contract](../architecture/modules/observability.md#5-cardinality-contract).
The cardinality upper bound alone would not require distinct labels; the
defect is misclassifying a matched template, not exceeding that bound.
Other counterexamples include `{epoch_id}`, `{authority_id}`, `{report_id}`
and `{source_id}`. No sensitive-path leak or production incident is asserted.

D1 repair: derive label admission from the captured code-owned route registry
and preserve `unmatched` for genuinely unmatched traffic. Do not grow another
handwritten placeholder list or accept raw request paths. Native acceptance
must compare the independently enumerated mounted routes with exported labels,
use multiple concrete IDs per template, and preserve the route-count bound.
The old rejection quantified only over implementation-accepted routes, hiding
the very false negatives being investigated.

### Other Rejections

| Hypothesis                                                                | Result and overturn condition                                                                                                                                                                                                                                                                                                                                                                                                                           |
|---------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Truncation gap receives a forbidden attempt subject                       | Rejection sustained. `complete_history_page` first calls `accept_page`, which requires no pending source; `_gap` receives the prior claim, not its pending successor. Therefore a page-truncation gap has null run/attempt IDs. Reopen if page admission or gap subject construction changes.                                                                                                                                                           |
| Inner drain timeout must become aggregate maintenance `timed_out`         | Rejection sustained as a correctness demand. Inner-turn exhaustion and outer-operation deadline have different owners and meanings. OBS-01 retains the useful stop-reason improvement.                                                                                                                                                                                                                                                                  |
| UI must say worker is awaiting recovery after lease expiry                | Rejection sustained. Lease expiry is scheduling/authority evidence, not current worker liveness. A clearly timestamped expired-lease observation may be useful; do not invent a live worker state from stale status.                                                                                                                                                                                                                                    |
| `scan.revision == snapshot.configurationRevision`                         | Rejection sustained. Claim/progress transitions increment scan revision without a configuration edit. The actual server relation compares scan configuration revision and generation with the dataset, not the independent progress counter.                                                                                                                                                                                                            |
| Prometheus `max` drops instance/job and is therefore incorrect            | Detection defect not established for a service-level page. Scrape target labels exist even when the exporter metric has no intrinsic labels, so that part of the rejection rationale was invalid. Preserve the service alert while adding a clear failed-replica query/drill-down; changing to per-instance alerts changes grouping, notification volume and `for` behavior and requires explicit admission.                                            |
| `dependency_graph -> planning_input -> dependency_graph` fails at runtime | Rejection sustained for current source. The reverse import in [planning_input](../../backend/src/ci_coordinator/repo_context/planning_input.py) is under TYPE_CHECKING with deferred annotations. The forward import is real, the reverse runtime edge is absent. Keep type/build dependencies visible for architecture review; do not discard all type-only edges globally. Reopen for an executable backedge or actual runtime annotation resolution. |

The existing [runbook](../how-to/operate-production-observability.md) requires
per-replica scrape coverage and diagnoses terminal failure, but does not require
one page per replica. An unlabelled service-level alert can still lead to a
labelled diagnostic query. The least invasive improvement preserves detection
and adds attribution; `max by(job, instance)` is not a purely cosmetic rewrite.

The later assertion that alert fixtures are absent is refuted as stated.
[Fixtures](../../deploy/observability/ci-coordinator.rules.test.yml),
[the promtool runner](../../scripts/prometheus_rules.py) and its
[CI invocation](../../.github/workflows/python-persistence.yml) exist. Static
YAML inventory finds 11 alert rules and four named rule-test identities. Seven
rules have no named fixture in this file:

- `CICoordinatorNotReady`
- `CIPlanAvailabilityFastBurn`
- `CIPlanAvailabilitySlowBurn`
- `CIPlanLatencyFastBurn`
- `CIReconciliationTerminalFailure`
- `CIReplayMismatchObserved`
- `CIUnsafeOmissionObserved`

This is a precise E1/D9 test-coverage task, not proof these rules fire wrongly.
Add meaningful threshold, missing-series, reset, multi-replica, persistence and
recovery cases where applicable; named presence alone is not semantic coverage.

## Sequencing And Acceptance

1. D1 before the next qualification claim: repair `ARCH-01`, `API-SEC-01` and
   `ROUTE-01` through their existing capability/HTTP owners. Preserve worker
   admission, authentication, response bodies, static-asset caching and metric
   cardinality. Group only overlapping owner work; this record does not demand
   one PR per helper or combine all findings into a repository rewrite.
2. During those owner changes, close TEST-03/04/05 and meaningful temporal-budget
   cross-checks. For DOC-02 bind each clock origin and necessary settlement
   reserve: `20 < 45 < 55 < 60` is descriptive, not a sufficient safety proof.
3. D4-H1 delivers the already planned read/detail/retention/erasure lifecycle,
   old-rerun recovery and precise parent/child races. Reevaluate stronger DDL/FKs
   at that boundary with explicit writer/read assumptions and migration cost.
   Keep physical mutation tests and application fail-closed tests distinct.
4. D4/E1 couples history stop-reason instrumentation with state-aware alerts,
   named rule fixtures, diagnostic attribution and shared-resource qualification.
   Preserve no-failure/no-data/paused/deferred distinctions and existing planning
   authority. A global broker or error framework is not a consequence.
5. D9/E1 closes input/evidence provenance and digest-bound release advisories.
   Store enough normalized claims and source/run coordinates for another clone
   to reproduce the reasoning without private scratch paths or unlimited raw
   logs. Actual load, rollout, backup/restore and provider receipts remain their
   existing workstreams, not results of this source-only intake.

Rejected or conditional rows are not permanent exemptions. A changed owner,
consumer, data lifetime, runtime import, trust boundary or hard budget reopens
the relevant row under [decision reuse](../decisions/review-decision-reuse.md).
The established response is smaller owner-bound corrections and meaningful
oracles, not redundant epoch fields, blanket FK expansion or another architecture.

## Retrospective And Limits

The route-label miss is an input-universe error: testing only values admitted by
the implementation cannot expose over-rejection of owner-valid values. Existing
quantifier, spec/test alignment and predicate-coverage invariants already require
an independent expected set. Add that executable route correspondence and the
missing private-import enforcement rather than inventing another universal rule.

Historical reasons for every external review omission, model execution quality,
all 509 unknown triggers and all applicable engineering invariants are not proved.
The report's valid non-certification conclusion is retained; failure to prove
global SOTA alone is not proof that every proposed replacement is superior.
This task changes planning/evidence documents only and does not resume the
paused implementation or authorize a deployment.
