# Adoption And Assurance Completion

Status: outstanding acceptance within existing delivery batches; no new runtime authority.

## Scope

The [roadmap](../../ROADMAP.md#cohesive-delivery-batches) owns priority and
status. This plan owns only the remaining adoption and assurance criteria
below; current module contracts own behavior. Source delivery, native execution
and provider qualification are separate. Completed repairs and expired run
receipts are not another backlog or prerequisites for understanding this plan.

## Unfinished Candidates And Delivery Order

| Candidate | Existing batch | Required admission before implementation |
| --- | --- | --- |
| Review of an already registered policy epoch | B3 API-first adoption; required before B1 activates a custom policy | The current attestation start rediscovers a proposal and requires its manifest identity; a separately registered custom dynamic-CI policy cannot substitute for that proposal. Bind actor, scope, provider revision, exact epoch, baseline, one-use handoff, audit identity and activation freshness. Complete HTTP/runtime wiring, forward migration and independent replay/race tests together. Do not bypass review or activate through direct database writes. |
| Structured decision provenance in reconciliation evidence | B5 selective CI and B7 audit closure | Compare the proposed policy/decision/provenance relation with current codecs and evidence owners. Admit complete identity, canonical bytes, retained-data migration, replay and verifier tamper oracles. Do not remove current policy rules or default-branch semantics. |
| Critical runtime mutation cohort and owner-class coverage | B1 own-CI assurance and B7 audit closure | Rebind unknown-path fallback, omission-proof independence, JWKS expiry and numeric repository identity mutants to current witnesses. Demonstrate meaningful kills and compare current tuple/risk gates before adding another coverage mechanism. |
| Same-repository reusable workflow grammar | B3 whole-workflow discovery; required for B1's own workflow graph | GH11 admits `$/` on GitHub.com, and the execution registry uses it, but discovery's graph parser only recognizes `./`. Resolve both forms at the caller commit with identical path, missing-target, cycle and depth checks; preserve the GHES boundary, reject expressions and local `@ref`. Other parser relaxations remain unadmitted candidates. |

These are outstanding candidates, not implemented capabilities or automatically
accepted designs. Implement only the smallest missing behavior, with a focused
design, preserved observables, falsifiers and current native qualification.
Reject a candidate when a current owner already provides an equivalent result.

## Detailed Acceptance

The source assessment on 2026-09-23 binds
`c5a9be38928f27e2709472852307b864aab07cf2`. Revalidate changed owners before
implementation; an old finding or prototype is not current authority.

The two B1 prerequisites above have current source counterexamples:
`workflow_discovery/graph.py` classifies `$/` as invalid, whereas GH11 in the
[provider semantics owner](../architecture/cross-cutting/github-actions-semantics.md)
and the [GitHub contract](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
admit it on GitHub.com; `app/repository_attestation.py` binds review to the
new discovery proposal, not an arbitrary registered epoch. Their correction
does not require all B3 product work before a read-only baseline or shadow
measurement. Pull forward only the path needed for admitted policy activation.

Registered-policy review must read and re-admit the exact retained epoch, not
trust an ID-shaped value. Bind the current repository scope and provider
revision, complete workflow inventory and its digest, source hash, active
baseline and authority profile. Missing, stale, forbidden and unavailable
remain distinct; a successful review does not itself activate. Verify start,
callback, registration and activation together, including foreign scope,
changed branch/inventory, concurrent baseline updates, expired/replayed
handoff and unavailable storage. Preserve the current rule/default-branch and
trust-mode contracts; do not silently copy a prototype's mode restriction.

Structured provenance must bind request, repository, diff, config epoch,
compiled policy, effective policy, graph and selected decision coordinates.
Use a bounded canonical projection of planner/verifier reasons, not raw
source, diff or test inventory. FullCI state and its exact fallback reason
must agree; selective state cannot carry fallback evidence. Isolated changes
to any bound coordinate or reason must change the content identity, and stored
hash tampering must reject. Decide versioning and retained-data migration with
the codec owner before writing new records; arbitrary prototype count/byte
limits and another receipt abstraction are not automatically admitted.

The four critical negative controls are unknown-path selection, independent
omission rejection even when planner replay shares the defect, expired JWKS
reuse after refresh failure, and mismatched numeric repository identity at
issuance. Each must fail through its actual guard, with a passing baseline and
no setup-error substitution. Reuse current witnesses; add a mutation cohort
only where these causal controls are missing. A finite four-case selection is
not an exhaustive threat or mutation universe.

| Retained predicate | Existing owner and batch | Acceptance and simpler alternative |
| --- | --- | --- |
| Pre-CI preparation includes a usable candidate, not only fetched context | B5 precomputed dynamic CI | Current `app/planning_preparation.py` prepares context; `app/candidate_planning.py` still plans and verifies on demand. If adding unsigned candidate preparation, key the complete planning input and policy operands, share the retained-byte budget and revalidate current identity, policy, capacity, overrides and authority at consumption. Miss, expiry or cancellation uses the ordinary path without waiting for speculative work. Keep context-only preparation where it meets the measured budget; do not fabricate a run/request/signature or duplicate the graph cache. |
| Measured savings require execution, not merely a signed plan | B4 economics O3 and B1 paired pilot | Join an exact target/run/attempt/source/registry/plan-bound completion receipt with independent provider jobs. Preserve requested, admitted and completed routes separately. Distinguish measured CPU, job occupancy and estimates; bind process/service coverage and reset/missing counters. Compare qualified cohorts, retain negative savings and unknowns; do not add another executor or collector. |
| CI evidence describes consumed inputs and independent expectations | B1 native assurance and B7 blueprint comparison | Reconcile assigned inputs with native consumed/collected inputs where the tool exposes them; otherwise state the weaker claim. Bind tool/query/plugin and input identities. Expected manifests are independent of observed output; quiet success needs an admitted contract and required artifacts. Empty, malformed, missing or truncated evidence cannot satisfy a stronger completeness claim. Reuse current native inventories and adapters. |
| Proofkit feedback is version- and consumer-specific | B7 dependency and proof-tooling qualification | Revalidate only relevant feedback against the locked CLI and the seven used commands, including malformed input, unsupported flags and exact route/shape checks. Keep resolved, producer-only, current and unknown dispositions separate. Compact source contracts and existing producer capabilities precede another manual generator; authoring helpers require a measured gap. Historical feedback counts are not current service defects. |
| Authority-transfer mechanisms are conditional | B7 contract/testing comparison; B4 remote effects | Per-copy live/WAL/backup/quarantine/key conservation is required when durable authority is transferred; independent full-row inventories apply to actual representation-owner transfer. Generation-fenced uncertain-effect recovery is required before mutating provider effects, not for every read-only API. Existing boundaries and native browser proof owners precede new registries or frameworks. |

The practical blueprint identified in ROADMAP supplies candidate predicates,
not a mandate to install every named tool. Existing Hypothesis, Schemathesis,
connected fixtures, dependency/import checks, secret scanning and image
evidence must be evaluated before adding overlapping tooling. The older
research blueprint does not mandate kernel enforcement, a new broker, DSL or
central dispatch without an admitted requirement and a cheaper-alternative
comparison.

The consolidated audit is already owned by the
[hardening design](assurance-audit-hardening.md) and
[acceptance plan](assurance-audit-hardening-plan.md). Source repairs, native
qualification and production evidence remain different states. The former
Go cgo inventory gap is addressed by native `go list` admission; package SCCs,
file size and old SARIF counts alone do not reopen architecture defects.

## Source Of Truth And Material Retirement

ROADMAP owns priorities, linked feature plans own acceptance, module contracts
own behavior, and native source/tests supply implementation evidence. An
external report is an input, not a parallel source of current status. Transfer
its applicable requirement, counterexample, uncertainty or decision rationale
to that existing owner before retiring the report. Keep deliberate rejections
scoped and falsifiable under the existing review-decision policy.

Historical age, a prior successful run or the possibility of future curiosity
does not justify an archive. Retain an original only for a named unresolved
dependency that a smaller owner record cannot preserve, or a specifically
requested recoverable source point. Record purpose, consumer and retirement
condition; do not retain every ancestor or duplicate. Shared inputs owned by
another project stay outside this project's deletion authority.
Keep active external review inputs and their working indexes separate from
historical recovery archives; an unfinished review is not archived work.

Delete only an explicitly inventoried project-owned path after content and
active-use checks. Reject drift and symlink traversal; remove a directory only
when empty, never recursively absorb unknown neighbors. Cleanup does not close
product work or qualify an old receipt on a new source.

## Superseded Work

Do not restore token-bearing browser sessions over the current separated
control-plane and repository-review identities, old read-model session fences
over the coherent statement snapshot, an old dependency lock, or draft
developer-experience documents over their integrated successors. Transport
ownership, generator noninterference and budget-policy behavior are already
represented by current source and native witnesses.
