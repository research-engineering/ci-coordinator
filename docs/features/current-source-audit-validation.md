# Current-Source Audit Validation

Status: validation and repair recipe; not a finding verdict or a second backlog

Date: 2026-09-25

## Scope And Authority

[ROADMAP](../../ROADMAP.md#8-next-work), primarily CI-054, owns priorities and
completion. This recipe extends the existing
[audit plan](assurance-audit-hardening-plan.md). Its cohorts are investigation
and delivery boundaries, not additional tasks or a commitment to one PR each.
The existing 180 unresolved legacy mappings remain distinct obligations.

The new external report contains exactly 471 distinct IDs in 13 lanes. Its
SHA-256 is `05e48c44e63d56d18f82a0493d1434e16940347cd5275a96e1cf828738a1650f`;
its source is `b09f831a9712bb30ce7175b27512495ec09680bc`. Intake was bound to
master `2f1c505711f93b2de92b6b4c2ab5658df400fcd5`, after PR #12. Rebind source,
owners, dependencies and provider facts before each actual validation batch.

The external materials directory holds the exact report, available original
probe evidence, checksums and the existing `review-inputs.json` register.
Do not commit raw reports, logs, credentials or corporate material. The
report's severity, CONFIRMED labels, benchmarks and claimed test results are
author assertions until independently checked. Its approximate deduplicated
count is not an admitted finding population.

The intake conserved these raw populations, not this many defects:

| Lane | IDs | Lane | IDs |
| --- | ---: | --- | ---: |
| LOGIC | 28 | SEC | 32 |
| GH | 35 | OPS | 37 |
| DB | 32 | ERR | 40 |
| ARCH | 38 | TEST | 35 |
| FE | 42 | CICD | 37 |
| TOOL | 38 | PY | 38 |
| DOCS | 39 | Total | 471 |

## Conservation And Per-Finding Contract

Each original ID retains its report digest, exact line interval, block digest,
original heading/severity, provisional cohort and candidate ROADMAP owners.
Existing legacy records must remain unchanged. Cohort assignment routes
investigation; it neither adjudicates a finding nor proves the exact owner.

Before disposition, read the entire finding, cited code, contract and strongest
counterargument. Split a compound finding into named child claims, preserving
its parent. One valid subclaim cannot confirm every allegation in that row;
one false subclaim cannot reject the rest. Separately check summary-only
assertions, the report's exclusions and positive claims. Give any unmapped
atomic assertion a source-bound child record rather than silently dropping it.

For every atomic claim, record:

1. Exact target and applicable owner; asserted behavior versus intended behavior.
2. Preconditions, affected actor/profile, trusted inputs and protected outcomes.
3. Source facts and a reachable counterexample, or the exact missing evidence.
4. Strongest defense/refuter, including current downstream guards and prior fixes.
5. Independent classification, severity rationale and affected scope.
6. Least costly sufficient remedy, credible alternative and regression risks.
7. Reproducer/falsifier, native test route and any separate provider/load proof.
8. Final source/test/PR evidence, unresolved limitations and reopening triggers.

Classify independently as `defect`, `proof-gap`, `conditional-risk`,
`improvement`, `refuted`, `already-fixed` or `unknown`. Track delivery separately
as `unvalidated`, `validated`, `repairing`, `source-fixed`, `qualified` or
`blocked`. A defect is not closed by validation alone. A proof gap is not proof
that the product fails. A benchmark is not an exploit or production capacity.

```text
ConfirmedDefect(f, e) := ApplicableOwner(f, e)
  and ReachableScenario(f, e)
  and ViolatedProtectedOutcome(f, e)
  and CounterargumentsResolved(f, e)

RepairClosed(f, e) := ConfirmedDefect(f, e)
  and AuthorizedBehaviorDelta(f)
  and IndependentRegressionWitness(f, e)
  and RequiredNativeChecksPassed(e)
  and RequiredExternalEvidenceSatisfiedOrNotApplicable(f, e)

ParentClosed(p) := every atomic child has an evidence-bound terminal disposition
```

Keep `unknown`, unavailable reproduction and `blocked` open. Refutation needs
an applicable owner and a refuter, not an absent test failure. `already-fixed`
needs the current repair plus its discriminating witness; an unrelated green
CI run is insufficient. Required external evidence cannot be declared N/A
merely because no server or credentials are available.

Deduplicate only after establishing equivalent violated predicates, reachable
preconditions and repair/verification obligations. Preserve every original ID
as an alias with its distinct evidence. Related symptoms can share a patch but
retain different oracles. Report raw intake, validated atomic claims, canonical
defects, repaired defects and qualified defects separately; no weighted project
percentage follows from 471 raw rows.

## Ordered Cohorts

All rows below also belong to CI-054. Exact per-ID routing is conserved in the
private input register; examples here identify mechanisms, not the full scope.
Counts are provisional routing counts before atomic splitting or deduplication.

| Cohort | Raw rows | Validate and, if confirmed, repair | Existing owners | Closure focus |
| --- | ---: | --- | --- | --- |
| Trust | 31 | Head-controlled graph authority; event-specific diff semantics; planner/verifier independence; OIDC-to-plan binding; omission, shadow attribution and lifetime. Start with LOGIC-1/2/3, ARCH-2 and TEST-1/2/3/4. | CI-004, CI-040, CI-044, CI-045, CI-047, CI-061 | Known-edge removal, graph/generator edit, ancestor/diverged push, stale/missing/truncated operands, wrong identity and unrelated failed job each exercise their own causal oracle. No unsafe skip; fallback actually executes or fails explicitly. |
| Supply chain | 49 | Trusted base versus candidate scanner/policy; release eligibility and repeated successful runs; dated repair admission; delivered libraries/SBOM; public security disclosure; actual CI costs. | CI-001, CI-002, CI-006, CI-060, CI-061, CI-064 | Candidate cannot approve its own exception. Exercise release rerun, expiry, malformed evidence and unpublished/rejected artifact paths. Revalidate dated repairs; never simply extend expiry or weaken scanning. |
| Identity | 31 | Actions OIDC headers/claims, Keycloak machine/logout compatibility, authorization scope, secret transport, credential/key rotation and current explicit operator choices. | CI-015, CI-018, CI-051, CI-062, CI-068 | Version-bound provider examples plus hostile variants, signature/issuer/audience/identity retained; session and rotation races; no token or key in logs/fixtures. Live interoperability remains separate. |
| Provider | 33 | Installation lifecycle, inventory revocation, missed webhooks, pagination drift, rate-limit classification, credential lookup and expensive repeated remote work. | CI-007, CI-008, CI-009, CI-013, CI-014, CI-021 | Duplicate/out-of-order events, deletion/denial/404 distinctions, primary/secondary limits, changing pages, sparse/dense history and durable recovery; caches cannot preserve revoked authority. |
| Runtime | 84 | Bounded ingress before authentication, JSON CPU/memory costs, event-loop blocking, worker isolation/supervision, readiness, cancellation/drain, safe diagnostics and complete outcome metrics. | CI-014, CI-035, CI-059, CI-066, CI-068, CI-069 | Slow headers/body, saturated independent tenants, invalid signatures, poison records, process/COMMIT/ack cuts, timeout versus cancellation and secret-safe distinguishable diagnostics. Liveness and readiness have separate owners. |
| Database | 49 | Audit verification/readiness progress, lock contention, transaction attestation, indexed hot paths, retention, uncertain commit, migrations and database-time authority. | CI-010, CI-011, CI-016, CI-058, CI-067 | Real PostgreSQL plans at representative cardinality, deterministic races and injected failures; preserve chain integrity, leases and participant attestations. Existing-data forward migration and restore evidence are separate from a fresh database. |
| Workflow | 12 | Actual supported YAML language, call graph complexity, anchors, secrets, expressions and workflow identity. | CI-017, CI-018, CI-020, CI-022, CI-040, CI-043 | Provider-pinned corpus, cycles/depth/alias bounds and adversarial DAGs; candidate library must preserve resource and trust boundaries. Unknown semantics cannot authorize omission. |
| Frontend | 43 | Delivered asset identity and double bootstrap, deployment/version skew, session/draft continuity, uncertain mutations, contract/route drift, accessibility and rendering cost. | CI-015, CI-019, CI-023, CI-025, CI-032 | Real packaged ASGI asset path, not Vite alone; one bootstrap and effect, refresh/logout/expired/changed principal, old-tab deployment, keyboard/mobile and table equivalents. |
| Analytics | 9 | Comparable cohorts, missing versus quiet days, forecast calibration, degradation/budget signals, measurement upload and shard-history fallbacks. | CI-027-CI-031, CI-042 | Independent small reference datasets, time-ordered backtests, sample/effect thresholds, missing and negative outcomes; no causal savings or runner attribution from correlation. |
| Engineering | 96 | Import-boundary counterexamples, oracle quality, library replacement candidates, validation/canonicalization, duplication, typing, test isolation and justified decomposition. | CI-006, CI-053, CI-056, CI-057, CI-061, CI-063, CI-065 | Per-rule bypass and negative controls; exact contract/resource/performance comparison before replacement; preserve test population, independent oracles and module boundaries. Metrics select review, not a god-file verdict. |
| Documentation | 34 | Remaining executable setup/CLI/API instructions, maturity, local-check policy, navigation/history drift, lifecycle and release guidance. | CI-026, CI-053, CI-064, CI-070 | Exercise recipes under their supported scope; source/export privacy, current links and honest readiness. Relevant runbooks accompany earlier repairs instead of waiting for this cohort. |

This is eleven investigation cohorts, not eleven guaranteed PRs. An 84-row
runtime cohort must split when independent owners or proof costs require it;
one small change can close aliases across several cohorts. Keep a coherent
behavioral contract, its tests and documents in the same implementation PR.

### Dependencies And Fast Path

1. Freeze intake and current contracts; inspect the reported Critical and all
   High rows for reachability and prerequisite impact before cosmetic work.
2. Trust and supply-chain controls are the first repair priority. Pull forward
   disclosure guidance and release-expiry investigation immediately. Preserve
   non-enforcing observation and independent FullCI while omission safety is
   unresolved; no automatic activation or new permission is part of this plan.
3. Identity/provider, runtime/database and frontend delivery are parallelizable
   only across disjoint writes and explicit contracts. Pull any confirmed defect
   ahead of the pilot scenario it invalidates; a low severity does not excuse a
   hard prerequisite. Do not wait for 471 verdicts before repairing a proved risk.
4. Workflow closure precedes affected selective/reusable cases. Complete
   analytics on admitted populations and cohorts, not on silently missing data.
5. Apply remaining engineering simplifications and documentation corrections
   with causal witnesses, then perform composed failure, package and deployment
   qualification. Security/performance fixes from these cohorts are not deferred
   merely because their default cohort appears late in the table.

Read-only own-CI observation is not blocked by unrelated Nits or optional
product decisions. Selected execution waits for its trust/identity/fallback
conjunction; production omission retains every CI-047 prerequisite. No server
is needed for source adjudication, workflow fixtures, negative controls or
hosted CI. Missing authorized live infrastructure blocks only its named
operational proofs, never becomes implicit approval to use former deployments.

## Design And Verification Discipline

Before each repair, record the confirmed predicate, the simplest sufficient
alternative, protected observables and the intended business/API/security
delta in the existing owning design. Extend its plan rather than creating one
document per finding. Use a new design only for a genuinely new owner decision.
Policy changes such as tenant rights, retention, accepted syntax, failure
semantics or key custody need explicit admission; do not hide them as cleanup.

Prefer existing Pydantic, FastAPI, standard-library and admitted dependency
capabilities when they preserve the actual contract. Library presence, newer
versions, KMS, GraphQL, a new broker or a named architecture pattern are not
proof of an improvement. Compare duplicate keys, canonical bytes, integer and
Unicode rules, bounded reads, cancellation, errors and compatibility before
replacing a parser/validator. A verified digest is not graph completeness.

For tests, first show that the current behavior violates the independently
specified expectation. Where a test cannot safely run against the baseline,
retain an explicit static proof and its execution limitation. A candidate fix
must pass the same witness. Separately mutate each independent operand and
ensure the intended assertion catches it, not setup/import errors or unrelated
timeouts. Multi-check mutants and unchanged aggregate pass counts alone do not
establish the coverage or absence of a particular defense. Reproduce third-
party probes only after reading them, in an isolated authorized environment.

Performance acceptance uses comparable workload, hardware, warm/cold state,
cardinality, budgets and distributions. Measure wall time, CPU/RSS, queueing and
provider requests where relevant. Sum of timeout caps is not measured runtime;
a missing index, cache, async keyword or extension is not alone a performance
defect. Use real query plans and concurrency witnesses before tuning the DB.

Use the repository's current native routes, Proofkit contracts and command
inventory. Static lint/type/ownership/docs checks cannot substitute for
behavioral tests. Run behavioral/provider/container work where current owner
policy permits; do not infer local-test permission from the auditor's probes.
Freeze each completed batch for one independent reviewer under current model
policy, adjudicate its claims, and use a bounded control pass only after
material changes or uncovered scope. No automatic infinite review loop.

Before merge, require exact-head affected checks and required CI, unchanged
reviewed source and scoped closure evidence. Use squash; preserve the
distinction between reviewed head, merged tree, packaged digest and deployment.
If an independent task changes an owner, invalidate affected dispositions and
recheck their predicates rather than resetting the whole intake.

## Prior Decisions And Honest Closeout

[Review decision reuse](../decisions/review-decision-reuse.md) remains applicable:
re-read RD-001 through RD-005 and their exact arguments. Stale pins invalidate
reuse; they do not automatically falsify the underlying business decision.
Never suppress a topic or a new counterexample because an earlier argument was
rejected. The report's suppressed arguments are a coverage limitation; retrieve
their concrete forms if needed, without inventing missing findings.

The owner explicitly selected alerts-only dependency automation, a specific
independent review model and synthetic README imagery including Bart Simpson.
Revalidate actual exposure, discoverability and usability concerns, but do not
silently enable security PRs, remove the review requirement or replace that
image because an auditor prefers another policy. A deliberate global-admin
profile is distinct from an unintended cross-tenant escalation. No business
choice excuses a demonstrated violation of a hard security requirement.

For each merged batch update the existing ROADMAP task and per-ID evidence
register with exact scope, verdicts, fixes, tests, remaining blockers and next
eligible cohort. Capture preventable misses and their actual causes; propose
a skill change only when the current rule was missing or defective, not merely
because it was not applied. Do not auto-edit active skills or add process layers
without evidence that they prevent recurrence at acceptable cost.

Intake completeness closes only when every original ID and every discovered
atomic child is accounted for. Audit remediation closes only when all confirmed
required repairs and proof gaps are qualified, rejections have scoped evidence,
and unknown/blocked items are explicitly resolved. Product completion still
requires the rest of ROADMAP; neither this plan, its review nor a score proves
global SOTA, absence of all defects or production readiness.
