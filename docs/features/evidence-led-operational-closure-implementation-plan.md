# Evidence-Led Operational Closure Implementation Plan

Status: proposed execution plan derived from independent adjudication

Date: 2026-09-05

Baseline: `e791fad2ed9e68ccc73a130ded6f517f0109310e`

## 1. Scope And Authority

The [independent audit](../adoption/independent-sota-adjudication-2026-09-05.md)
owns finding disposition and bounded counterexamples. This plan owns their
execution dependencies and acceptance work. The [roadmap](../../ROADMAP.md)
owns product order and capability state. Existing module specifications own
behavior; this document does not silently change them or rewrite older designs.

The plan replaces stale pending-work ordering, not completed implementation.
Python-only runtime projection, compact Proofkit v2 routes, configuration
lifecycle APIs, economics v1 and thin-client generation must not be rebuilt.
FullCI fallback evidence is retained but is not selected-CI savings evidence.

Design each affected capability before changing its code: record as-is behavior,
intended delta, protected observations, alternatives, falsifiers and revision
triggers in a new owner-routed successor where the current contract is
insufficient. A module's code, native falsifiers and Proofkit bindings land in
the same implementation batch. This planning update changes none of them.

## 2. Ordering Argument

```text
B1 durable correctness -> B2 recoverable service boundaries
B1 -> B3 measured database and CI cost
B2 -> B4 operator recovery and usable administration
B1/B2 -> remaining planning and authority work in ROADMAP D2/D3
B3 -> credible savings measurement in ROADMAP D4/E2
B5 current routing/security hygiene accompanies B1 through B4
E1 administrator/deployment evidence -> E2 live shadow pilot -> E3 activation
```

B1 precedes performance changes because making an invalid authority check faster
does not improve correctness. B2 precedes wider adoption because no choice of
front-end framework repairs dropped delivery or unhealthy probes. B3 precedes
speculative rewrites because measured cost can select a smaller effective fix.
Feature work without external dependencies continues while E1 is blocked;
waiting for an administrator is not a reason to restart an architecture audit.

Choose bounded business-objective batches, not one PR per finding. Usually use
one main independent review and one exact-head control review per implementation
batch; additional passes need a new material defect or changed scope. One
correctness witness plus an open performance question is not a reason to repeat
all previous reviews. No fixed PR-count or universal perfection claim is made.

## 3. B1: Durable Correctness

Findings: A1/R2 2.10 and A2/R2 2.4. The implementation design is
[Durable Admission Linearization](durable-admission-linearization.md).

| Owner and affected files                                                                                                                          | Design and implementation                                                                                                                                                                                                                                                                                                             | Required acceptance                                                                                                                                                                                                                                                                                        |
|---------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Reconciliation: `persistence/runtime_adapters.py`, `reconciliation_state_repository.py`, `reconciliation_queries.py`, associated pair/claim paths | Define the database-owned mutation admission instant after acquiring the relevant lock. Audit acquisition, append, defer, release and terminal recording together. Keep event occurrence time distinct from lease authority. A pure domain predicate may receive an explicitly admitted timestamp; it must not own clock acquisition. | PostgreSQL witness pauses across expiry before lock acquisition; former owner cannot create a new observation, defer, release or terminal result. Cover before/at/after expiry, both reclaim/terminal orderings, generation/token mismatch, duplicate replay, cancellation and atomic paired audit writes. |
| Compatibility admission: `compatibility_admission.py`, existing `ci_economics_schema_attestation.py`, economics/webhook UoW capability sets       | Connect the existing attestor to every applicable runtime admission path under the compatibility fence. Establish complete required-capability dispatch; avoid copying migration-only checks into another owner.                                                                                                                      | Valid schema accepted; declaration retained while each protected schema/ACL fact is independently mutated must reject before repository use. Exercise both economics and webhook transactions and upgrade from the prior applied revision.                                                                 |

Reject merely removing `now`, checking time before an await, or relying on CAS
alone. A database timestamp sampled too early can reproduce the same defect.
State the linearization point and cancellation/rollback behavior explicitly.
Only a new mutation needs current authority; existing duplicate/conflict
classification must not be accidentally redefined.

Behavior change visible to operators: an expired worker that previously could
write after waiting is rejected. A database that merely declares economics
support but lacks required facts is rejected earlier. No valid selected-plan
policy, historical bytes or credential authority is broadened. If a schema
change is needed, use a forward migration, never rewrite an applied revision.

## 4. B2: Recoverable Service Boundaries

Findings: A4/A5, R2 1.1-1.14, 2.8, 4.1/4.2/4.6/4.7, restricted to the admitted
subclaims in the audit ledger.

| Boundary                | Work and alternatives                                                                                                                                                                                                                                                                              | Required acceptance                                                                                                                                                                                                                                   |
|-------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Liveness and shutdown   | Make the healthcheck import-light. Separate process liveness from serving readiness and reconcile HTTP admission with the supported Swarm health mechanism. Prefer a narrow probe/lifecycle solution over a second general application server. Preserve fail-closed initial reconciliation.        | Exact image probe start/RSS/elapsed budget; healthy process under request/keep-alive saturation is not spuriously restarted; genuine dead process is detected. SIGTERM drains within the single container stop budget.                                |
| Webhook intake          | Compare bounded ingress plus explicit failed-delivery recovery against a durable inbox. Freeze body/CPU/memory limits, duplicate identity, ACK-after-durability and bounded retry/redelivery policy. Do not assume GitHub retries 503 and do not introduce an outbox without an outbound consumer. | Concurrent valid deliveries, overload, crash before/after durable claim, worker cancellation, poison input and provider redelivery. No false ACK; retry cannot duplicate effects or mint omission authority. Live webhook tests remain blocked by E1. |
| Logout and public abuse | Separate pre-auth protection from valid-provider revocation capacity. Evaluate trusted edge admission, bounded verification and durable revocation barriers before selecting storage or distributed coordination. Bound physical session cleanup without leaving undeleted sessions authorized.    | Invalid traffic cannot silently consume the entire valid-logout path under the admitted threat budget; issuer-only target rejected; sid/sub scoping, duplicate token, expiry, role revocation and replica visibility remain exact.                    |
| Diagnostics and alerts  | Preserve the existing bounded/redacted observer; carry scheduler/registration failure class to it. Add expected-target absence/scrape-failure detection separately from burn-rate statistics. Admit only useful saturation metrics with finite labels.                                             | Error, cancellation, no-data, successful empty round, provider failure and database failure remain distinguishable. Secret/header/body values never become labels or logs; unavailable metrics cannot appear healthy.                                 |
| HTTP and secret custody | Inventory route-specific cache/security headers, explicit metrics-auth policy and development bootstrap secret argv. Keep Keycloak identifiers public but secret values out of command arguments where feasible.                                                                                   | HTTP/redirect/static/API profiles, lab-vs-connected construction, adversarial proxy headers and bootstrap failure/cleanup witnesses; no accidental new public endpoint or relaxed credential admission.                                               |

Behavior changes requiring explicit design admission: overload status/queue
policy, health/readiness routing, logout delivery/recovery and session cleanup.
Removing bounds, accepting spoofed forwarded identity, or acknowledging before
durable admission are rejected alternatives. New distributed infrastructure is
conditional on a proved multi-replica requirement, not an automatic fix.

## 5. B3: Measured Cost Reduction

Findings: A3; R2 2.1-2.3/2.5/2.11, 3.3/3.5/3.9-3.11, 5.1-5.4/5.7/5.8,
6.1-6.5. Risks and improvement candidates are measured before adoption.

1. Capture per-capability SQL count, compatibility cost, lock duration, pool wait,
   rows examined and application latency under representative history sizes.
   Explain the override anti-join in the healthy no-orphan case. Separate
   intrinsic cost from network, cold cache and test instrumentation.
2. Replace history-wide hot-path proof only with an equivalent maintained
   relation or incrementally certified proof. Assess index-only improvement
   honestly. Never cache startup admission across arbitrary schema/ACL changes;
   the compatibility generation and lock must invalidate stale certification.
3. For a touched schema family, compare owner-authored metadata plus generated
   projections with today's repeated tuples. Keep independent tamper oracles;
   a generator and a test reading the same wrong declaration are not independent
   proof. Retain canonical evidence bytes and total-order audit semantics.
4. Reuse one coverage-enabled persistence execution for required results and
   coverage where they are semantically equivalent. Preserve exact test/fixture,
   denominator and required-command inventories before removing duplicate work.
   Then evaluate independent PostgreSQL shards and safe build caching.
5. Resolve the real admitted Node executable before creating the hermetic lab
   environment. Bind path/version identity and permitted subprocess environment;
   preserve no-ambient-authority behavior. Cover real binary, mise shim,
   missing tool, wrong version, hostile PATH and interrupted cleanup.
6. Keep per-mutant deadlines and process-group cleanup. Right-size job budgets
   from manifest bounds and observed distributions, not one run or arbitrary
   p99 multiplication. Preserve named guard mutations and add property-based
   cases only where they add distinct parser/codec/interleaving pressure.
7. Evaluate bounded economics claim loops and deadline-scoped GitHub retries or
   conditional reads. Preserve retry fairness, maximum work per round, exact
   installation/subject/configuration cache identity and unknown-to-FullCI.

Acceptance: before/after measurements plus test/oracle parity and unchanged
authority. No dropped tests, coverage exclusions, skipped semantic tests, weaker
FullCI gates or speculative package consolidation count as savings. Runner wall
time, billed time, CPU time and modelled counterfactual savings remain distinct.
Pure code-size reduction without measured maintenance or execution benefit is
not a mandatory optimization.

## 6. B4: Operator Experience

Findings: R2 5.6, 7.1-7.6 and the user-requested UI completion.

Start with a rendering error boundary, scope-preserving recovery and persistent
accessible status/error announcements. Associate field errors with inputs and
make full identifiers usable from keyboard, touch and assistive technology.
Admit component coverage and behavior oracles for these specific risks.

Complete the API-first task journeys before introducing shared frontend state:
organization inventory, workflow inspection, adaptation proposal, semantic diff,
registration, activation/rollback, run evidence, economics and optional bot
escalation. Define cache identity, fresh authorization, cancellation, scope
changes, retry and Back navigation. Adopt a query/router library only when its
semantics reduce proven duplication without weakening those boundaries.

Then consolidate visual tokens, responsive tables/panels, loading/empty/error
states and explanatory evidence hierarchy. Choose an explicit browser and
accessibility scope and verify screenshots, keyboard operation and production
bundles in GitHub CI. Package presence, a dark theme, arbitrary component count
or an axe invocation alone is not an acceptance oracle.

Deliver a short runnable tutorial, operator/API/CLI how-to paths, generated API
reference routing, a glossary and current architecture diagrams. Keep models
advisory: the optional configuration chat cannot become a second mutation
authority and does not block the external review-bot command/read APIs.

## 7. B5: Current Authority And Maintenance Hygiene

Findings: R2 3.2-3.10, 4.3/4.4, 8.1-8.5, 9.1/9.2 and the original report's
remaining scope-sensitive claims. Apply incrementally with touched batches.

- Synchronize the renamed `deployment-admitted CI Coordinator` App profile and current routes;
  a profile change is not permission to repair or exercise its deferred webhook.
- Correct README's current zizmor value and route Python instructions to the
  singleton 3.13.15 owner. Preserve historical Proofkit/PostgreSQL/Python witness
  values and all existing design/plan tree entries. Add a successor when current
  normative prose actually needs to change.
- Keep PostgreSQL 18.6 and the owner-selected Python 3.13.15 stack until an
  admitted update. Review security patches and toolchain compatibility through
  one current machine profile and checked projections, not newest-number rules.
- Confirm CodeQL scan/alert state and existing dependency admission; add Action
  advisory coverage only if no existing owner closes it. Document disclosure,
  license, contribution and release policy through owner decisions. Keep the
  sole-maintainer zero-approval model unchanged.
- Document github.com/runner prerequisites for `$/`; GHES, Windows, extra
  browsers and additional release architectures require separate admission.
- Compare custom libraries and port boundaries only for touched mechanisms.
  A proven co-ownership defect requires a complete safe decomposition; size,
  method count, helper spelling and rule-family absence only select review.
- Keep a causal review ledger and Proofkit adoption feedback. Do not add skill
  invariants duplicating existing scope, state-machine or oracle rules.
- Implement the [review-decision reuse contract](../decisions/review-decision-reuse.md)
  with an owner-reference register and strict Pydantic admission in the existing
  repository JSON gate. Preserve semantic review, expose reference drift as
  review-required, and never emit an automatic waiver or suppression verdict.
  Add independent malformed-input, path and drift witnesses before admission.

## 8. Retained Product And External Work

The complete product sequence remains in ROADMAP D1-D9. This audit does not
remove pre-CI planning, deploy/Compose input closure, sound reuse, adaptive
shards, successor target authority, real CPU/savings statistics, optional bot
escalation/read/report APIs, draft orchestration, governance, release evidence,
environment bindings or the developer-instance lifecycle.

Webhook configuration, installation consent and operational reachability must
be admitted afresh for the public deployment. A synthetic pilot profile is not
installation authority. No live probe, repair, target PR or successful selected
execution is authorized merely by this plan.

| External stage    | Evidence required before completion                                                                                                                                                                                                                                                                                                    |
|-------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| E1: Environment   | Working administrator-provided ingress/webhook and installations, exact App permissions, current immutable Swarm artifact, PostgreSQL 18.6 migration/ACL/TLS/primary policy, Keycloak admin access, secrets/signing, aggregate admission, termination, backup/restore, retention, alerting and measured capacity. Start non-enforcing. |
| E2: Product pilot | pilot target signed FullCI and representative shadow cases with the native gate retained, then paired selected/full measurements and fallback/rollback faults. Bind source, runner, cache, attempt, target and policy identities; extend to the other repositories with authorization.                                                        |
| E3: Activation    | Non-vacuous external receipts, completed successor relation/persistence, exact generation fencing, old-authority/execution/replica drain, independent gate evidence and explicit gradual activation with a kill switch.                                                                                                                |

A shared deployed control plane can use one database for its replicas and
client deployment environments. Independent test coordinators require isolated
database/credentials/authority; an `environment_id` label is not isolation.
Database-copy conservation, independent full-row inventories and remote-effect
uncertainty recovery apply when their actual authority-transfer triggers occur.

## 9. Batch Completion Contract

For each implemented batch, freeze exact base/head and map every affected
finding to its code, normative owner, negative witness and outcome. Obtain
green required GitHub CI for that head before squash publication. Local work
remains static-only under the current user constraint. Review code, tests,
contracts and derived documentation together, preserving existing oracles.

This plan's own acceptance is narrower: all 71 R2 rows and 33 R1 compound
bullets have dispositions and routes; current roadmap preserves completed work,
remaining goals and blockers; repository links and JSON/Proofkit routes pass
their static gates. The new plan is routed under `REQ-CI-PROOFKIT-009` for
documentation admission, not as proof that its proposed features exist.
No runtime, schema, CI workflow or old design changes.
Completing this documentation task does not close B1-B5 or production admission.
