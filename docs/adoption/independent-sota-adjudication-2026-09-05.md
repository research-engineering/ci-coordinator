# Independent Audit Adjudication

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

Status: bounded source-review evidence, not a production certificate

Date: 2026-09-05

Source epoch: `master` / `origin/master` at
`e791fad2ed9e68ccc73a130ded6f517f0109310e`.

## 1. Decision

The two supplied reports identify useful risks but do not establish their
overall ranking, severity distribution, or proposed replacement architecture.
The second report corrects several errors and introduces others. Its agent
count and self-score are not independent evidence for this adjudication.

The immediate priorities are temporal write authority, runtime schema admission,
bounded ingress and probe behavior, and measurable database/CI cost. Retain the
modular monolith, PostgreSQL, deterministic safety boundaries, and the
owner-selected Python runtime. Do not replace them to satisfy technology or
line-count checklists.

This review identifies a lease-expiry counterexample that survives the second
report's dismissal of multi-replica concerns. Conversely, CodeQL is configured,
GitHub supports PKCE, and a logout without both subject and session identity is
rejected before the SQL adapter. These distinctions materially change repair
order.

Execution is routed by the
[successor repair plan](../features/evidence-led-operational-closure-implementation-plan.md)
and the [roadmap](../../ROADMAP.md). This report owns evidence disposition only;
it does not replace module specifications or authorize deployment.

## 2. Evidence And Decision Rules

| Input                           | Identity and treatment                                                                                                                                                                                                                                              |
|---------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Original report, R1             | SHA-256 `40a1810a616a2dd35f4805d06b3fab40ca8c5e3b4963f286b788afb8f0e682fd`; 33 top-level compound bullets, numbered by appearance below.                                                                                                                            |
| Reassessment, R2                | SHA-256 `70ad01cae255d3b5a3b28497b08ff617eb150bf35cb0cd38d9d8a3e251b5b0c1`; 71 numbered findings. Its claimed 38-item original inventory is not supplied as an atomic ledger.                                                                                       |
| Repository                      | Direct source and current owner contracts at the source epoch; metrics are navigation, not semantic proof.                                                                                                                                                          |
| GitHub CI | Former private run receipt removed. Public exact-head qualification is required. |
| Provider security configuration | Unverified for the public repository; fresh owner-authorized inspection is required. |
| Validation boundary             | Source inspection and static checks only. No local behavioral tests, containers, load tests, live webhook probes, or new independent reviewer agents were run for this adjudication.                                                                                |

The candidate set is finite; whole-program correctness is not claimed. A row
may contain both a true mechanism and a false consequence. Dispositions apply
to those propositions separately:

| Code | Meaning                                                                                               |
|------|-------------------------------------------------------------------------------------------------------|
| D    | Confirmed defect or concrete missing protection in the reviewed source.                               |
| R    | Credible risk or operational evidence gap; deployment impact or the best repair still needs evidence. |
| I    | Improvement candidate requiring cost, ownership, or behavioral parity proof.                          |
| C    | Deliberate scope or supported-contract decision, not a defect by itself.                              |
| X    | The stated inference is contradicted or does not follow from its evidence.                            |
| U    | A measurement, environment claim, or broad assertion was not independently reproduced.                |

```text
Defect(c) := ApplicableOwnerRule(c)
             and ReachableCounterexample(c)
             and ViolatesProtectedObservation(c)

MissingEvidence(c) != ProvenFalse(c)
DocumentedNonClaim(c) != ViolatedRequirement(c)
ImplementedMechanism(c) != OperationallyQualified(c)

AdmitRepair(r) := PreservesProtectedObservations(r)
                  and ClosesNamedCounterexample(r)
                  and FitsOwnedCostAndTrustBudgets(r)
```

The reports' conjunction named `SOTA` can describe their chosen checklist, but
does not prove that every criterion is required here or that any proposed
alternative dominates the current design. A supported maintenance branch is
not disqualified merely because a newer minor version exists. A documented
non-goal is not a false required conjunct. A real false conjunct, however,
cannot be hidden behind unrelated unknowns: unconditional correctness is
already falsified by the temporal counterexample below, while review coverage
and production assurance remain incomplete.

## 3. Highest-Value Counterexamples

### A1. Lease Authority Uses A Pre-Wait Clock Sample

The [reconciliation contract](../architecture/modules/reconciliation.md)
requires token, generation, and unexpired lease for every new durable mutation.
In [runtime adapters](../../backend/src/ci_coordinator/persistence/runtime_adapters.py),
`append_observation` supplies `now` before awaiting the repository; result
recording samples time even before entering its unit of work. The
[state repository](../../backend/src/ci_coordinator/persistence/reconciliation_state_repository.py)
locks the row and then evaluates this earlier sample in
`reconciliation_claim_is_active` or `release_terminal_claim`.

```text
t0 < lease_expiry <= t1
t0 = supplied application time
t1 = time after acquiring the row lock
stored token and generation remain unchanged

check(token, generation, t0) = accepted
AuthorizedOwner(subject, claim, t1) = false
```

No clock skew or competing reclaimer is necessary: waiting for another
transaction suffices. Generation CAS excludes replaced ownership, not elapsed
ownership. This is a temporal correctness defect, not proof that every
multi-replica operation is unsafe. Rebind write admission to a database-owned
instant at the declared linearization point. Preserve duplicate/conflict
semantics and distinguish event timestamps from current write authority.
The detailed race witness belongs in the first repair batch.

### A2. Declared Economics Capability Is Not Runtime-Attested

[Compatibility admission](../../backend/src/ci_coordinator/persistence/compatibility_admission.py)
dispatches schema checks for other capability families but never invokes the
existing asynchronous
[economics schema attestor](../../backend/src/ci_coordinator/persistence/ci_economics_schema_attestation.py).
Economics and webhook units of work require the economics declaration.
Migration-result attestation is a different execution path.

```text
DeclaredCapability = present
RequiredEconomicsConstraint = absent
RuntimeDeclarationCheck = accepted
RuntimeEconomicsSchemaCheck = not executed
```

The [compatibility profile](../specs/ci-coordinator-core/database-compatibility-profile.v1.json)
explicitly rejects treating declaration as schema proof. Wire the existing
attestor and cover every required capability route; do not create another
independent schema description. A migration success does not close this
post-migration drift counterexample.

### A3. Override Admission Rechecks Historical Cardinality

`admit_operator_override_state` in the
[override repository](../../backend/src/ci_coordinator/persistence/operator_override_repository.py)
runs an anti-join over `audit_events` to detect an applied override without its
state projection. In the normal no-orphan case, absence must be established
over historical candidates. The event type has no dedicated index in
[_schema_audit.py](../../backend/src/ci_coordinator/persistence/_schema_audit.py).

This proves history-dependent work, not a measured query plan or a particular
latency. An event-type index may reduce irrelevant rows but does not make
checking an unbounded number of override events constant-cost. Measure the
query and replace the repeated global proof only with a maintained integrity
relation or certified incremental check under the existing compatibility fence.
Do not delete the check or cache startup success without a drift model.

### A4. Ingress Rejection Needs An Owned Recovery Contract

[ProcessWebhookAdmission](../../backend/src/ci_coordinator/runtime/webhook_admission.py)
admits one process-worker preparation at a time. A concurrent call gets an
unavailable result, mapped to 503. The lock is released before
`complete_webhook_ingestion`: it does not serialize the subsequent database
transaction. Bounded rejection is an intentional memory-safety mechanism.

Nevertheless, failed deliveries are not automatically retried by GitHub.
[GitHub's delivery documentation](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries)
requires manual or explicitly implemented redelivery. Therefore a recovery
claim based only on returning 503 is invalid. Acknowledge only durably admitted
work; choose bounded ingress plus explicit redelivery/reconciliation, or a
durable inbox if its added lifecycle is justified. An outbox, Kafka, or Redis is
not implied. Keep the administrator-blocked live webhook untouched.

### A5. Liveness And Revocation Share Hostile Admission Budgets

[Uvicorn startup](../../backend/src/ci_coordinator/runtime/__main__.py) applies
the same concurrency limit before health-route dispatch. Uvicorn documents the
limit in terms of [connections or tasks](https://www.uvicorn.org/settings/).
Saturation can consequently make an otherwise live process return 503 to its
HTTP probe. Whether this becomes a restart loop depends on the deployment.
The [Docker probe](../../Dockerfile) also imports `runtime/__init__.py`, which
eagerly imports the application graph. The reported 4.8 seconds and 118 MB
were not reproduced and are not an accepted budget measurement.

Separately, the public path bucket runs before logout authentication. Invalid
traffic and a valid Keycloak backchannel logout consume the same path budget;
the valid logout can be rejected before revocation. Session expiry and fresh
authorization guards limit consequences but do not restore delivery. Separate
pre-auth abuse protection from admitted-provider revocation capacity, preserving
finite memory and explicit trust of any proxy identity. Do not replace this
with unbounded client-key dictionaries or accept unauthenticated logout.

## 4. Complete Reassessment Ledger

Backend paths below are relative to `backend/src/ci_coordinator/`, unless a
repository prefix is shown. Source lines are orientation at the frozen SHA,
not identities across future commits. Batch identifiers refer to the successor
plan. An `R`, `I`, or `U` row schedules validation, not unconditional replacement.

### Runtime And Operations

| R2 ID | Disposition | Independent finding and repair route                                                                                                                                                                                                                                                                |
|-------|-------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1.1   | D/R + X     | A4: fail-fast preparation is real; whole-transaction serialization and critical severity are not established. B2 owns bounded admission plus recoverable failed delivery.                                                                                                                           |
| 1.2   | R           | A5: shared pre-ASGI concurrency cap admits probe starvation; actual restart cascade needs Swarm/proxy evidence. B2 then E1.                                                                                                                                                                         |
| 1.3   | C + X       | `runtime/readiness.py:97` intentionally requires background health; first-round failure is fail-closed startup. One replica's failed round does not prove every replica fails. Do not remove the safety predicate; consider journey-specific readiness only through an owner-approved change in B2. |
| 1.4   | D/R         | `deploy/observability/ci-coordinator.rules.yml` has burn/NotReady rules but no missing-target rule. Empty series need separate detection; do not remove statistically justified minimum-volume guards. B2; deployment-wide alert coverage remains E1 evidence.                                      |
| 1.5   | X + R       | Uvicorn handles SIGTERM and graceful shutdown. No custom handler is not unhandled SIGTERM. Pre-drain readiness, proxy propagation, and termination budgets still need a Swarm lifecycle witness, B2/E1; Kubernetes preStop is not this platform's requirement.                                      |
| 1.6   | R + C       | A bare `docker run` recipe lacks automatic restart; fail-closed initial reconciliation itself is correct. Complete the supported deployment restart/backoff contract in E1, not a blanket restart policy for disposable development commands.                                                       |
| 1.7   | D + U       | A5: eager application import is unnecessary probe work under a five-second budget. Import-light probe in B2; reported timings, RSS, and module count require a new exact-artifact measurement.                                                                                                      |
| 1.8   | R/I         | Runtime metrics do not yet expose a complete pool/inflight/event-loop saturation view. Add decision-useful bounded metrics and overload attribution in B2/B3; metric count is not proof of observability completeness.                                                                              |
| 1.9   | I           | Custom JSON logging and default Uvicorn logging have different formats; the claim that both necessarily use stdout is too narrow. Normalize sinks/fields in B2 only with redaction and collector contracts, without restoring sensitive access logs.                                                |
| 1.10  | C + I       | `api/http/correlation.py` owns a fresh request ID, not distributed trace continuation. OTel absence is documented. Add cross-boundary correlation when an owned journey, sampling and privacy budget justify it; B2/E1, not universal header trust.                                                 |
| 1.11  | D + X       | Scheduler and registration swallow exception class into `FAILED`/`False`. Reuse bounded diagnostics in B2. The outer dynamic-plan handler is not wholly dead: other exceptions still reach it. Cleanup suppression must be classified by lifecycle before alteration.                               |
| 1.12  | C + R       | Process-local limits are real; aggregate limits scale with replica count. That does not forbid multiple replicas. E1 must own aggregate admission and rotation; no speculative distributed limiter before that contract.                                                                            |
| 1.13  | R + X       | Backup/restore and deployment qualification remain gaps. Swarm, not Kubernetes, is the target; missing Helm/Terraform/PDB is not a defect. Production container hardening is already documented. E1.                                                                                                |
| 1.14  | C + X       | Restart-based secret rotation is explicit, not absent rotation semantics. `.env.example` separates required mode-specific values from optional identity configuration. Validate all-replica rotation in E1 rather than inventing hot secret reload.                                                 |

### Persistence

| R2 ID | Disposition | Independent finding and repair route                                                                                                                                                                                                                                                       |
|-------|-------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 2.1   | I + U       | UoW admission has sequential catalog/ACL checks; counts vary by capability path. The universal 25/36 round-trips and 100 tx/s ceiling were not measured and do not follow from RTT alone. B3 measures per-path round-trips, lock time, pool wait and p95/p99 before changing admission.    |
| 2.2   | C + I + X   | `audit_repository.py:232` serializes the hash-chain head during append, as required for one total order. It is not held during the earlier UoW preamble. B3 measures the bottleneck; per-repository chains require a separately versioned semantic change.                                 |
| 2.3   | D           | A3: admission cost grows with retained history. B3 closes the cost defect while retaining orphan detection and schema/ACL fencing.                                                                                                                                                         |
| 2.4   | D           | A2: existing economics attestor is disconnected from runtime admission. B1.                                                                                                                                                                                                                |
| 2.5   | I + X       | DDL, SQLAlchemy metadata, immutable byte contracts and live attestation have different roles. Similar tuples suggest consolidation, not proof of four competing semantic owners or 35% removable code. B3 evaluates one schema family with independent drift falsifiers.                   |
| 2.6   | C           | Audit timestamp text participates in exact canonical evidence. Different query columns may legitimately use `timestamptz`. Preserve signed/hash bytes; add query projections only for a proved query need, B3/E1.                                                                          |
| 2.7   | R + X       | Unbounded audit retention needs an explicit archival/replay policy. Self-FKs constrain possible layouts but do not prove all partitioning impossible. E1 owns preservation, privacy, checkpoints and restore; no destructive retention shortcut.                                           |
| 2.8   | R + X       | Bulk revocation can affect many sessions. The issuer-only case is unreachable: `BackChannelLogoutTarget` rejects missing `sid` and `sub`. B2 validates revocation cost; bounded physical cleanup must not leave residual sessions authorized.                                              |
| 2.9   | R           | `persistence/connection.py` admits the driver, not a deployment TLS/primary policy. Production DSN, trust roots, network and primary routing require E1 evidence; do not infer an existing plaintext deployment or add irrelevant local TLS constraints.                                   |
| 2.10  | D, narrowed | A1: the real counterexample is stale pre-lock application time, not the mere existence of three clock functions. B1 defines authoritative instants and evaluates other temporal paths individually.                                                                                        |
| 2.11  | I           | `app/ci_economics.py:248` creates bounded claim tasks, including empty polls. B3 can use a bounded worker loop stopping on no-due-work, but must preserve retry fairness, cancellation and per-round limits; do not scale work only by newly registered count.                             |
| 2.12  | R/C + X     | Autovacuum may be the correct starting policy; absence of a custom script is not absence of vacuum. Measured maintenance and restore belong to E1. Online-only migration and rejecting destructive downgrade with retained data are explicit safety constraints, not inherently defective. |

### Architecture And Libraries

| R2 ID | Disposition | Independent finding and repair route                                                                                                                                                                                                                                                         |
|-------|-------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 3.1   | X           | `parser.py` and `_job_parser.py` import the `_syntax_fields` submodule, not a partially initialized public service symbol. That submodule depends on syntax/model helpers. The cited text does not prove a runtime cycle or order-dependent failure; no cycle refactor is admitted from it.  |
| 3.2   | I + D       | Composition's 439-line function is a wiring concentration signal, not a proved god-function. The app docstring is stale. B5 fixes current explanatory/version projections; owner-scoped factory extraction is conditional on complete wiring/parity proof.                                   |
| 3.3   | I + X       | Same helper names do not establish same semantic value. Different byte limits, trimming and integer policies may be required by different protocols. No cross-layer hash mismatch is demonstrated. B3/B5 consolidate only an actually identical owned relation.                              |
| 3.4   | I + X       | Literal order, `match`, `TypedDict`, cast and isinstance counts are not quality invariants. Exact union ownership and unreachable-branch handling can be improved on touched paths; do not replace idioms mechanically or merge different event vocabularies.                                |
| 3.5   | I           | Exact collaborator checks restrict substitution; a concrete wrapper-compatible observation/contract is needed to prove a forbidden restriction. B2/B3 may introduce a narrow observer protocol if required, while preserving exact value-object trust checks.                                |
| 3.6   | I           | `plan_requests.py` reconstructs a mapping for domain admission; the HTTP contract intentionally preserves a separate wire boundary. A typed mapper can reduce repetition if it preserves missing/null/default, aliases, validation and status behavior. B5, not bypassing domain validation. |
| 3.7   | I + X       | A Prometheus adapter can coherently own many metrics. Method count and common injection do not prove SRP failure. Split only a proven consumer contract when B2 instrumentation needs it, without creating one interface per counter.                                                        |
| 3.8   | I + X       | Exceptions, domain outcomes and transport sentinels serve distinct boundaries. A universal Result framework is not mandated. Remove redundant wrappers only with error/cancellation parity; B5.                                                                                              |
| 3.9   | I + U       | Eager imports are visible; external cold-start numbers are not accepted measurements. B2 isolates the probe path first. Blanket lazy imports may defer startup errors and are not automatically safer.                                                                                       |
| 3.10  | I + X       | Library substitution must preserve exact canonical bytes, resource bounds, unknown outcomes and ownership rules. Some custom mechanisms have existing specifications. B3/B5 performs bounded build-versus-buy comparisons, not a rewrite based on LOC.                                       |
| 3.11  | C + I       | Failed GitHub reads conservatively cause FullCI, preserving the product safety law. B3 evaluates deadline-bounded retries/conditional reads for repeatable operations, scoped cache identities and rate-limit budget; no blind retry of remote effects.                                      |

### Security

| R2 ID | Disposition | Independent finding and repair route                                                                                                                                                                                                                                  |
|-------|-------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 4.1   | D/R         | A5: unauthenticated traffic can starve valid logout admission. This proves a revocation availability path, not arbitrary session takeover. B2/E1.                                                                                                                     |
| 4.2   | R/I         | UI hardening does not uniformly cover OAuth/API responses; correct headers depend on content, redirects and TLS edge ownership. B2 inventories routes and E1 verifies edge policy; do not copy CSP/HSTS indiscriminately onto every response.                         |
| 4.3   | R           | Offline zizmor is deterministic but does not provide current GitHub Action vulnerability intelligence. Dependency audits cover other ecosystems. B5 adds or verifies a separately owned online advisory source without weakening offline analysis.                    |
| 4.4 | R/U | Former provider configuration observations are revoked. Inspect current CodeQL setup, scan status and alerts only with fresh authority; workflow-file absence alone proves neither presence nor absence. Disclosure guidance remains useful; Scorecard is optional. |
| 4.5   | X           | GitHub's current GitHub App OAuth documentation specifies S256 `code_challenge` and `code_verifier`; the code sends them. Do not remove PKCE. Live-provider conformance remains E1, not inferred from documentation.                                                  |
| 4.6   | R + C       | The router allows absent metrics authentication; connected production composition supplies it. This is not demonstrated unauthenticated production metrics. Make any laboratory exception explicit if the public construction API is tightened, B2.                   |
| 4.7   | R + X       | Development bootstrap passes passwords as psql arguments, visible in process/inspection surfaces. B2 evaluates file/stdin custody. Keycloak hostname and client ID are public identity configuration, not leaked credentials; their exact deployment profile is intentional. |

### Tests And Oracles

| R2 ID | Disposition        | Independent finding and repair route                                                                                                                                                                                                                                                                                                                                                |
|-------|--------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 5.1   | D, conditional + U | `consumer_contract_lab/node_runtime.py:157` can resolve a mise shim and then run it in a scrubbed temporary environment lacking its tool-selection context. Resolve the admitted executable before isolation in B3. The 20/3730 failures are not reproduced here. Git/TCP/POSIX tests need explicit integration prerequisites, not automatic skip/xfail.                            |
| 5.2   | C + I              | Curated mutations prove named guards; they do not claim a whole-repository generated mutation score. Preserve the named inventory and add targeted generation only where it detects new faults, B3/B5.                                                                                                                                                                              |
| 5.3   | I + X              | Hypothesis absence does not prove no property tests: canonical JSON tests exercise permutation laws. Add generated parser/codec properties where they expand meaningful cases. xdist/timeout/randomly/schemathesis are tools, not universal requirements. B3/B5.                                                                                                                    |
| 5.4   | R                  | Real sleeps and elapsed-time assertions select potentially flaky tests. Some verify actual subprocess/network timeout behavior. B3 replaces incidental timing with synchronization and reserves wall-time budgets for correctly classified integration witnesses.                                                                                                                   |
| 5.5   | R + X              | Fake HTTP transports already check boundary contracts; no VCR package does not mean no contract layer. Actual GitHub/Keycloak receipts remain external gaps, E1/E2; recording secrets or stale provider responses is not an automatic improvement.                                                                                                                                  |
| 5.6   | D/R + X/U          | Coverage excludes feature TSX and has aggregate thresholds; this leaves component coverage outside the measured denominator. Two files and one browser spec contain many tests, not two/one cases. The reported hook percentage is not exact-head evidence. B4 admits component-risk coverage and a browser/a11y scope; axe without explicit tags does not prove WCAG rules absent. |
| 5.7   | I + X              | Error message matching and golden hashes can prove public diagnostics and canonical bytes. Fixture-file count does not prove duplication. B3/B5 remove implementation-coupled assertions only after inventorying protected oracles; no blanket parameterization.                                                                                                                    |
| 5.8   | R + X              | Global coverage floors are coarse regression controls, not completeness proof. Capacity tooling exists but load qualification is unproved. B3 adds risk-specific cost/race witnesses and E1 supplies scale evidence; neither 84% nor 100% establishes semantic adequacy.                                                                                                            |

### CI And Delivery

| R2 ID | Disposition | Independent finding and repair route                                                                                                                                                                                                                                                                                                                                              |
|-------|-------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 6.1   | C + I + U   | FullCI is deliberately conservative until selection is sound. Latest exact-head job timings show material cost, but report-wide daily frequency/cancellation statistics were not reproduced. B3 removes duplicate work first; D2 admits selection only with full fallback and exact required-check coverage.                                                                      |
| 6.2   | I + X       | The job budget is 258 minutes, but each mutant has its own timeout and process cleanup. One hung mutant does not imply a 4.3-hour wait. B3 derives finite job limits from complete manifest costs and observed distributions; one green run is not p99.                                                                                                                           |
| 6.3   | D/I         | The persistence workflow runs persistence tests and then coverage over the full test inventory, including persistence again (`scripts/python_witness.py:134,160`). B3 preserves both required evidence products in one covered execution where equivalent, then considers isolated sharding. Latest persistence job: 18m33s; mutation job: 15m40s. These are wall times, not CPU. |
| 6.4   | C + I       | Ubuntu/CPython 3.13.15/linux-amd64 is the current CI/release scope. README ARM64 concerns local development, not a published multi-arch release promise. B3 evaluates safe build caching; expand platforms only after an owner decision.                                                                                                                                          |
| 6.5   | I           | Runtime identity appears in machine sources, generated projections, tests and historical evidence. B5 checks authoritative projections and expiry policy; never rewrite historical receipts to reduce literal counts.                                                                                                                                                             |

### Frontend

| R2 ID | Disposition   | Independent finding and repair route                                                                                                                                                                                                                                                                                                             |
|-------|---------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 7.1   | D             | `frontend/src/main.tsx` has no rendering error boundary. An uncaught render exception removes the UI. B4 adds bounded recovery preserving authentication/scope; this does not catch every event-handler or async failure.                                                                                                                        |
| 7.2   | D/R, narrowed | ScopeForm has an alert but no per-field error association. Some live regions mount with their message; discovery field validation already has associations. B4 adds persistent announcements and keyboard/screen-reader witnesses; universal silence across assistive technology is unproved.                                                    |
| 7.3   | I + X         | Hooks repeat request state, but mount keys intentionally prevent stale scope evidence. `replaceState` does not create per-selection Back entries; absence of popstate alone does not prove normal navigation desynchronization. B4 first defines navigation/cache/auth invalidation semantics, then chooses the smallest data-layer abstraction. |
| 7.4   | I             | Duplicated selectors, spacing and colors are maintenance opportunities within the planned visual system. Dark mode and a particular styling library are not existing hard requirements. B4 consolidates owned tokens and verifies responsive behavior.                                                                                           |
| 7.5   | R/I + U       | Chunk size is not a demonstrated budget failure; disabling public sourcemaps can be intentional. B4 makes full identifiers accessible without hover and aligns display conventions; measure loading before splitting bundles or adding a monitoring service.                                                                                     |
| 7.6   | I + X         | Several inner schemas compose into typed outer schemas; no missing contract follows from counting annotations. Assignability alone is not full semantic parity. Add exact type and behavioral witnesses as needed. The TypeScript compatibility shim delegates to locked 6.0.3; it is not a failed 6.0.2 install. B4/B5.                         |

### Documentation And Product Scope

| R2 ID | Disposition | Independent finding and repair route                                                                                                                                                                                                                                                                                          |
|-------|-------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 8.1   | D + X       | README's zizmor values disagree; current Python routing still exposes older dual-version prose. But Proofkit 0.5.0 is a rejected-alternative example, PostgreSQL 18.4 is explicitly superseded, and 3.13.14 references are historical witnesses. B5 corrects current routes/projections without rewriting historical designs. |
| 8.2   | R/I + X     | End-to-end onboarding and API/UI procedures need completion. File age, heading proportions and paired design/plan files do not establish staleness or duplicate authority. B4/B5 organizes task-oriented navigation and validates commands.                                                                                   |
| 8.3   | I + X       | A glossary and shorter routes would help. Formal prose is not claimed as a theorem-prover result; executable requirements, Proofkit routes and native tests exist. B5 connects every normative claim to its owner/oracle or non-claim; do not delete safety predicates because they contain formal notation.                  |
| 8.4   | R/C + X     | License/disclosure/contributor/release policy need owner decisions before broader distribution. One author, version 0.1.0, no Conventional Commits, templates or pre-commit do not prove bad code. Preserve the explicit sole-maintainer/zero-approval policy. B5, no invented governance mutation.                           |
| 8.5   | I + X       | ADR quality depends on decision, alternatives, falsifiers and consequences, not literal heading names. B5 adds a concise current route/successor where genuinely missing; existing designs remain unchanged.                                                                                                                  |
| 8.6   | R/C         | Real selected-versus-full savings remain unproved. FullCI fallback and local implementation are explicitly weaker claims. E2 supplies paired measurements. Synthetic fixture/build identities must remain barred from production; zeros alone do not prove bypass.                                                            |
| 9.1   | C + R       | Python 3.13.15 is explicitly owner-selected. Newer minor availability does not falsify quality. B5 retains a security-patch response policy and validated projections; external release-calendar claims are not independent freshness evidence here.                                                                          |
| 9.2   | R, narrowed | GitHub confirms `$/` on github.com requires runner 2.336.0+. B5 records the support prerequisite. That source alone does not prove a universal current GHES prohibition; do not claim GHES support without its own version evidence.                                                                                          |

## 5. Original Report Reconciliation

R1 labels below follow its 33 top-level bullets. Compound metrics and unique
subclaims remain visible; the later report is not allowed to silently erase
them. No confirmation percentage is computed from incompatible inventories.

| R1 ID | Topic                                 | Disposition and R2 reconciliation                                                                                                                                                                                        |
|-------|---------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| O01   | NIH, bespoke infrastructure           | I/C: R2 3.8/3.10; library replacement needs exact semantics and cost proof. Script LOC is not an economic measurement.                                                                                                   |
| O02   | Attestation duplication               | I/D: R2 2.4/2.5/2.12; missing runtime dispatch is concrete, wholesale replacement is not.                                                                                                                                |
| O03   | Raw SQL and offline migration         | X/C: AST counts 111 `text` calls and 91 `select` calls in tracked production Python at this epoch, not 807. Catalog SQL and online migration guards have legitimate roles.                                               |
| O04   | God composition and app metadata      | I/D: R2 3.2. Size does not satisfy repository semantic-failure admission.                                                                                                                                                |
| O05   | Type checks and casts                 | X/I: static typing does not validate untrusted runtime JSON, exact integers or canonical values. `bool` is an `int` subclass. R2 3.4/3.5.                                                                                |
| O06   | Repeated helpers                      | I/X: R2 3.3; same spelling is not identity of meaning.                                                                                                                                                                   |
| O07   | UoW overhead and pool five            | I/X: R2 2.1; five is the library default, not the admitted connected-runtime deployment value.                                                                                                                           |
| O08   | Bytea, indexes and retention          | C/R: R2 2.3/2.6/2.7. Exact signed bytes cannot be replaced by normalizing JSONB without compatibility proof.                                                                                                             |
| O09   | No horizontal scaling                 | X/R: durable CAS/locking exists; aggregate admission/HA still requires qualification. R2 1.12 and A1.                                                                                                                    |
| O10   | Sparse observability and swallowing   | D/I: R2 1.8-1.11. Count of emit calls is not logging coverage.                                                                                                                                                           |
| O11   | Synchronous webhook and absent outbox | D/R/X: A4. The process worker keeps CPU work off the event loop; absence of automatic provider redelivery matters more than presence of a named queue technology.                                                        |
| O12   | No GitHub retry                       | C/I: R2 3.11, preserve FullCI on uncertainty.                                                                                                                                                                            |
| O13   | Cold imports                          | I/U: R2 1.7/3.9, measure exact artifact before a numeric claim.                                                                                                                                                          |
| O14   | Agent advice without an LLM           | C: monotonic advice admission exists; model execution/UI chat is explicitly optional/deferred. Do not implement an agent to satisfy naming criticism.                                                                    |
| O15   | Mise-dependent failures               | D, conditional/U: R2 5.1; resolve executable identity instead of skipping tests.                                                                                                                                         |
| O16   | Mypy/pnpm wrapper prerequisites       | U/C: no clean declared-environment failure was reproduced. Native wrappers intentionally own source roots and exact dependencies; improve discoverability, not bypass admission.                                         |
| O17   | Frontend coverage                     | D/R: R2 5.6; excluded components are not all untested.                                                                                                                                                                   |
| O18   | Coverage floors                       | R/X: R2 5.8; percentages do not establish oracle adequacy.                                                                                                                                                               |
| O19   | No properties and 41 golden hashes    | X/I: law tests exist; binary-cache grep is not source evidence. Add useful generated cases, retain canonical byte oracles.                                                                                               |
| O20   | CI cost, cache and portability        | I/X: R2 6.1-6.4; setup-uv enables caching automatically, per-mutant timeouts exist, and local ARM64 is not a release-image promise.                                                                                      |
| O21   | Exact Python and pyc residue          | C/X: R2 9.1. Ignored caches describe a workstation, not the supported product tree.                                                                                                                                      |
| O22   | TypeScript shim and overrides         | C/I: locked compatibility projection and a security patch with a removal trigger; not proof of broken installation. R2 7.6.                                                                                              |
| O23   | Hooks, schemas and React features     | I/D/X: R2 7.1/7.3/7.6. Fix error recovery; new libraries require a concrete state-management need.                                                                                                                       |
| O24   | Styles                                | I: R2 7.4; visual work remains in B4.                                                                                                                                                                                    |
| O25   | Ruff rule families and nesting        | I/X: rule names and maximum nesting are review signals, not violations by themselves. B5 adds a rule only for a recurring owned defect and checks false positives.                                                       |
| O26   | Deployment descriptions               | R/X: R2 1.13; product Swarm recipes and recovery evidence matter, not Kubernetes artifacts.                                                                                                                              |
| O27   | Compose hardening and zero build ID   | R/C/X: development topology differs from production instructions. Zero build identity is not production-eligible by default. Review secret argv in B2; prove published image/probe behavior in E1.                       |
| O28   | Initial migration rewritten           | X as argued: dates and LOC do not prove rewrite. A forward 0003 now exists. Do not squash applied migrations; upgrade and retained-data recovery witnesses remain required.                                              |
| O29   | Private vocabulary and version drift  | D/I/X: R2 8.1-8.3; preserve historical evidence, improve current navigation.                                                                                                                                             |
| O30   | Governance and local branches         | C/R/X: R2 8.4. Worktree/branch counts and author concentration are not source correctness evidence; cleanup is not in this task.                                                                                         |
| O31   | Value, sentinels and publication IDs  | R/C: R2 8.6. Publication repository/workflow IDs bind trust intentionally. General target support does not require loosening release identity. Paired savings remain E2.                                                 |
| O32   | Example identity values               | X: client ID and issuer URL are public identity values, not secrets. R2 4.7.                                                                                                                                             |
| O33   | JOSE stacks, locks and `$/`           | C/R/X: PyJWT is development-only; Authlib and joserfc are related production dependencies. Exported requirements lock is a verified projection of uv, not a second solver authority. Runner/GHES support follows R2 9.2. |

## 6. Structural And Operational Non-Claims

No blockable god-file conclusion is admitted by this report. The source epoch
has no new/worsened PR delta, and size does not prove forbidden co-ownership
or a strictly preferable safe decomposition under the
[repository policy](../architecture/cross-cutting/module-ownership-and-decomposition.md).
This is not a claim that every file is architecturally ideal.

Specific review-selection signals, not decomposition orders:

| Carrier                                       | Physical lines at the source epoch | Review question                                                                                  |
|-----------------------------------------------|------------------------------------|--------------------------------------------------------------------------------------------------|
| `persistence/ci_economics_schema_contract.py` | 990                                | Can one schema projection be generated without sharing the independent oracle's failure mode?    |
| `persistence/config_epoch_repository.py`      | 836                                | Are mutation, lifecycle and read contracts genuinely independent owners or one atomic lifecycle? |
| `persistence/proposal_review_repository.py`   | 817                                | Does a proposed split preserve transactional admission and temporal authority?                   |
| `runtime/composition.py`                      | 738                                | Which bounded factory reduces wiring cost without moving policy into composition?                |
| `runtime_settings/contracts.py`               | 785                                | Are exact admitted values and dependent invariants kept together?                                |

Unknown production conditions include current release/deployment identity,
capacity and restore, provider delivery/revocation behavior, aggregate admission,
edge policy, current installations and permissions, and measured selected CI
savings. Webhook status for the public deployment is unverified; do not probe,
repair or activate any endpoint without new explicit administrator authority.

## 7. Causal Retrospective

The reports' repeatable failure mechanisms are evidence-scope errors: grep over
helper names or caches, repository-file absence mistaken for provider absence,
historical version mentions mistaken for current authority, library names
mistaken for required capabilities, and CAS mistaken for time-valid authority.
The earliest useful checkpoint is atomic claim formation: name the owner,
reachable input/state, observation and evidence horizon before assigning a
severity or selecting a replacement.

For future batches, inspect every time sample crossing an await/lock, every
required-capability-to-attestor edge, and every failure path's recovery owner.
Use one main review and normally one control review on a frozen change, not
unbounded repeated whole-repository reviews. Existing academic-engineering
scope, state-machine, boundary and proof-completeness rules already cover these
mechanisms; this review does not justify a duplicate universal invariant.
Compiler/bundle validity is not a runtime correctness certificate.

This adjudication also incurred preventable tool cost from oversized inventory
output and guessed paths. Use structured count/ID projections and bounded
source reads before broad output. The first selective-plan check correctly
rejected the new unbound implementation-plan path; its documentation route was
then added under the existing requirement, without changing product claims.
Include that derived route in writer readiness before adding the next plan.
A prose retrospective is not an accepted skill amendment or proof that a
proposal was staged.

## 8. External References

- GitHub explicitly documents [PKCE for GitHub App user access](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app); this refutes R2 4.5, without substituting for a live-provider witness.
- GitHub documents [failed delivery handling](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries); this grounds A4's missing-recovery obligation.
- [Uvicorn settings](https://www.uvicorn.org/settings/) define connection/task admission; [React error boundaries](https://react.dev/reference/react/Component#catching-rendering-errors-with-an-error-boundary) define render-error recovery.
- The [GitHub self-repository syntax announcement](https://github.blog/changelog/2026-07-30-reference-same-repository-actions-with-self-repository-syntax/) specifies github.com availability and runner 2.336.0+. Do not infer other provider editions' support without their version evidence.
