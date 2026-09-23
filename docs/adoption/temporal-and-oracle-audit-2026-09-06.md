# Temporal And Test-Oracle Audit Adjudication

Status: bounded source adjudication and open-work register

Date: 2026-09-06

## Scope And Decision Rule

The supplied 331-line report has SHA-256
`bf0d94f7db784a973210a0229dc84364e6399ef433941e27f66dfe463dc5f5cb` and
audited `4025cdd692b54df6d01e9b9a73a002bfde3c9e51`. This adjudication rebinds
its concrete claims to `master` at
`341a8972d6301fa9fd5cdad9331aae1494645892`; PRs #128 and #129 did not repair
these cache, session, receipt-reader or pytest-evidence paths.

```text
Defect = applicable owner rule + reachable violating trace
ProofGap = required distinction not established by the inspected oracle
Risk = reachable hazard whose materiality/acceptance premise remains open
Closed = repaired source + relevant exact-head native evidence
```

A report score, agent agreement and a green predecessor CI establish none of
these implications by themselves. Rows below are not file-level waivers.
Reuse an earlier argument only under the
[decision-register predicate](../decisions/review-decision-reuse.md).

## Finding Dispositions

| ID / supplied claim                                    | Independent result and decisive evidence                                                                                                                                                                                                                                                                            | Closure route                                                                                                                                                                                                                                                                                   |
|--------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| T1: stale detached refresh becomes fresh               | Confirmed defect. The three loaders in `identity_admission/jwks_provider.py`, `integrations/keycloak/discovery.py` and `integrations/keycloak/jwks.py` assign acquisition age at later waiter publication. Cancelling every waiter admits `publication >= response + TTL` while a lookup/probe reports usable data. | B2 now: [response freshness](../features/refresh-result-freshness.md), original-age and all-waiter cancellation witnesses.                                                                                                                                                                      |
| T2: session fence test lacks transaction ordering      | Confirmed proof gap, not a demonstrated session defect. `test_session_operation_uses_time_after_compatibility_fence` uses `_wait_past_expiry` without observing the transaction waiting on the actual fence. A late-starting transaction lets the forbidden transaction-time implementation pass.                   | B3: observe waiter PID/lock before expiry, then release after expiry; assert native result and a causal transaction-time mutant. Reuse the lease-authority test's existing barrier pattern.                                                                                                     |
| T3: pytest exit 1 implies mutation kill                | Confirmed evidence gap. `classify_mutation_execution` accepts exit 1 without a pytest phase/node outcome; baseline success cannot exclude a mutant-run fixture failure. No actual false kill in a published run is established.                                                                                     | B3: bounded machine reports bind designated node IDs and admitted failure phase/class to the exact baseline/mutant execution. Unexplained setup/teardown/collection/infrastructure failures are invalid, not kills. Explicit fixture falsifiers need owner admission.                           |
| T4: Node crypto-negative witness absent                | Bounded proof gap in the inspected bootstrap and consumer-lab suites. Noncanonical signature tests reach encoding rejection, not necessarily negative `crypto.verify`. The production verification call exists.                                                                                                     | B3: first check remaining executable consumers for equivalent evidence; otherwise add real Node wrong-key/same-key-ID and canonical changed-signature-byte cases, asserting fallback and rejection. Python-only crypto tests do not substitute.                                                 |
| T5: FIFO blocks receipt admission                      | Confirmed malformed-configuration defect. `production_admission/file.py` opens with `O_RDONLY` before checking `S_ISREG`; a writer-less FIFO can block before rejection. Actual malicious/local deployment configuration is not established.                                                                        | B2: nonblocking no-follow descriptor admission, a bounded real-FIFO child witness and existing regular/symlink/change tests. Do not claim that `O_NONBLOCK` bounds faulty network filesystems.                                                                                                  |
| T6: README versions drift                              | Confirmed documentation defect. README still names Proofkit 0.6.0 and one zizmor 1.28.0 occurrence; current admitted pins are 0.10.1 and 1.30.0.                                                                                                                                                                    | B5 with the next documentation correction: eliminate redundant exact-version prose or project existing lock authority; no new universal generator.                                                                                                                                              |
| T7: JWKS URI changes during load                       | Confirmed asymmetry: cache hits use current discovery, `_settle` does not. The report correctly leaves global immediate revocation unproved.                                                                                                                                                                        | B2 now: make the post-await path obey the existing current-URI predicate; document this exact consistency refinement, not a new global revocation promise.                                                                                                                                      |
| T8: Keycloak failure has no cooldown                   | Confirmed amplification risk: failed refresh clears the task and successive requests can start successive remote calls. Single-flight bounds concurrency, not frequency. No measured DoS or existing numeric retry SLO violation is claimed.                                                                        | B2: admit one failure retry budget for discovery/JWKS and background callers, preserve valid cached keys, specify the recovery-delay tradeoff, and falsify all-waiter cancellation plus rapid sequential failures.                                                                              |
| T9: pre-logout login may finish later                  | Confirmed reachable ordering, unresolved product-policy defect. Callback exchange may precede logout commit and session insertion may follow it; the current 15-minute residual window does not clearly define this case.                                                                                           | B2/D1: explicitly decide whether pre-logout evidence may create a later session. If forbidden, use a durable sid/subject revocation fence checked atomically on insertion; a process mutex is insufficient. No silent policy change.                                                            |
| T10: pre-admission webhook rejection can lose an event | Confirmed recovery obligation. The process gate can reject before commit and GitHub does not automatically retry failures. Successful ACK remains commit-bound; unsafe omission is not established.                                                                                                                 | B2/D2: enumerate actual downstream consumers, existing REST reconciliation coverage and nonrecoverable event kinds; choose operator redelivery, provider delivery reconciliation or a bounded inbox only for a proved need. Mutating provider recovery requires D6-style uncertainty authority. |
| T11: primary failure suppresses cleanup                | Confirmed liveness coupling. `MaintenanceReconciliationRound` returns/raises before maintenance when primary fails. The required deletion lag and read-expiry semantics need owner clarification.                                                                                                                   | B2/D4: separate read eligibility, physical cleanup deadline/lag and startup admission. After successful startup, prove eligible cleanup can progress through later provider failures without masking the primary failure.                                                                       |
| T12: global audit/override queries are costly          | Measurement obligation, not proved SLO failure. Audit-head serialization is intentional; orphan-event rejection protects integrity.                                                                                                                                                                                 | B3/E1: measure lock wait, query plans, transaction latency and full-retention cardinality. Accept a cheaper equivalent query/attestation only with corruption, contention and oracle preservation.                                                                                              |
| T13: readiness disconnects the entire UI               | Not proved by the described code. The container healthcheck uses liveness `/healthz`; routing impact depends on actual edge configuration.                                                                                                                                                                          | E1: inspect deployed routing/health policy before making an availability claim. Do not weaken authentication or readiness from this allegation.                                                                                                                                                 |
| T14: economics deferred outcomes are invisible         | Obsolete for the current inspected application path: collection outcomes have their own bounded metrics.                                                                                                                                                                                                            | Retain D4/E1 alert-delivery and operational evidence work; do not reimplement the already closed metric path.                                                                                                                                                                                   |
| T15: architecture needs mass decomposition             | Not proved. Counts and fan-out do not establish forbidden co-ownership or a safe preferable alternative. Existing planning/capacity owners and schema/oracle separation have distinct obligations.                                                                                                                  | D9: retain the modular monolith and perform measured owner-scoped reductions only. Moving the retention adapter alone has no demonstrated benefit.                                                                                                                                              |
| T16: verifier shares planner assumptions               | Valid non-claim: different algorithms may consume the same incomplete catalog/graph. No specific omitted dependency was demonstrated.                                                                                                                                                                               | D2/E2: target-owned inventory/input closure and independent full/selected pilots must validate the shared model. Do not describe algorithm separation as independent premise correctness.                                                                                                       |

## Coverage Of The Broader Review Map

The report's quality map is a useful set of questions, not a second verified
full-repository audit. Its remaining observations are retained as follows:

| Questions retained                                                                                                    | Existing owner/exit path                                                                                                         |
|-----------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| Product usefulness, complete input model, safe omission, AI authority, inter-language behavior                        | D2/D3/D6 and E2/E3; no unsafe omission or bot downgrade authority.                                                               |
| Time, cancellation, transactions, ambiguity, message order/delivery, provider errors, rotation and session revocation | T1-T11 and B1/B2; qualify real provider behavior at E1/E2.                                                                       |
| Tenant authorization, backpressure/fairness, per-request and aggregate resource bounds                                | B2/D5/D9 plus E1 load/capacity evidence; local bounds do not prove aggregate capacity.                                           |
| Tail latency, retention growth, metrics, user SLOs, savings and coordinator overhead                                  | B3/D4/E1/E2, with compatible units and matched workload/cache/runner/attempt. Duration does not establish CPU or energy savings. |
| Schema evolution, mixed versions, restore, RPO/RTO, deployment/rollback and fault domains                             | D3/D9/E1/E3; no destructive migration or topology change inferred from an audit concern.                                         |
| Supply chain, reproducibility, dependencies, keys, privacy/deletion, backups and residency                            | B5/D9/E1; exact source/artifact/secret owners and external evidence remain necessary.                                            |
| UX, accessibility, cognitive cost, change amplification, team ownership and reversible decisions                      | B4/D5/D7/D9; preserve scenario evidence and re-open decisions on current falsifiers, not size alone.                             |
| Licensing, organization constraints, supported language/platform/browser scope                                        | D9 release scope; unknown compliance is not an established violation.                                                            |

The referenced ISO/OWASP/SLSA/WCAG materials are orientation, not admitted
whole-standard certification. Energy/carbon accounting and organizational
staffing are not silently promoted into mandatory product features.

## Progress And Delivery

The overall planning estimate is approximately 70%, with substantial uncertainty
because the roadmap has no effort-weighted accepted denominator. It is not a
measured acceptance or production-readiness percentage. Core implementation,
native proof, environment qualification and measured benefit are distinct.

The complete remaining sequence is [ROADMAP section 8](../../ROADMAP.md#8-next-work):
B2/B3/B5 repair, D2 dynamic execution, D3 authority, D4 economics, D5 API-first
adoption, D6 optional bot channel, D7 UI/documentation, D8 remaining capabilities,
D9 release qualification, then E1 environment, E2 pilots and E3 enforcement.
Independent code work continues while administrator prerequisites are blocked.
Optional UI chat, generic DSL and central dispatch remain conditional.

## Retrospective Boundary

The concrete cache causal error is assigning an evidence timestamp at
consumption instead of acquisition. The existing cancellation oracle kept a
surviving waiter and therefore did not distinguish the detached-completion trace.
Existing Academic Engineering age/authority, interleaving and guard-falsifier
rules already cover this case; this observation alone does not prove a missing
skill invariant or justify another framework. Historical reasons why an earlier
review omitted the trace remain unproved without its exact delivered evidence.
