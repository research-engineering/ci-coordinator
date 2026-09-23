# CI Economics Measured Comparisons Implementation Plan

Status: implementation plan; native and deployment evidence remain open

Design: [measured comparisons](ci-economics-measured-comparisons.md).
Owner: `ci_economics`, with explicit transport, persistence and producer owners.

## 1. Completion Boundary

Deliver one useful observational path: a repository attempt can be represented
without reconciliation, its exact provider facts and optional scoped report can
be retained, and an authorized caller can inspect measurements and an explicit
comparison without UI. Absence of reports or economics infrastructure must not
alter selection or the target gate.

This source batch does not claim external deployment, actual organization-wide
savings, every-workflow counter instrumentation or inferential significance.
The [roadmap](../../ROADMAP.md) retains those qualification and UI obligations.

## 2. Source Admission Before Coding

| Question                                                    | Required evidence                                                                                                                                       | Blocking condition                                                                                |
|-------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| Can existing collection IDs map uniquely to exact attempts? | No: sources are many-to-one; preserve every legacy row and the existing single-snapshot conflict semantics                                              | A unique-attempt source constraint, alias capture or silent row deletion is introduced            |
| How is an independent attempt anchored in time?             | Explicit provider run-created population window; attempt start remains a separate measurement; old-run reruns outside the window are visible exclusions | Mutable update/report time is used as an anchor, or bounded run population is called all attempts |
| Can all supported producer instances join exact jobs?       | OIDC/check-run/static-job/matrix relation and the current final-gate contract                                                                           | Display name or partial `needs` is mistaken for a complete instance universe                      |
| Can the current queue accept the new source algebra safely? | Inline discriminated source; exact legacy FK and untouched lifetimes; source-aware claims and new capability epoch                                      | Fake reconciliation, removed guard or unowned schema rewrite                                      |
| What are the resource bounds?                               | Design section 8 fixes the initial profile and distinguishes independent-source quota from external total-capacity proof                                | Per-request limits are presented as a total storage proof                                         |
| What is the API evolution?                                  | New v2 route family; unchanged v1 required hash and reconciled-only population                                                                          | A v1 required hash silently becomes optional or changes meaning                                   |

Use direct source and primary provider/library contracts. Compilation or graph
navigation does not resolve these questions. The independent source-consistency
review exposed the same admitted multiplicity and legacy-time obligations;
design section 5 now fixes their intended outcomes. Freeze each changed owner
and its writer-readiness row before implementation. A material contradiction
reopens that owner, not the entire unchanged roadmap.

## 3. Ordered Work

### A. Source And Interpretation Contracts

1. Implement the inline source algebra with exact optional legacy provenance.
2. Define immutable report, measurement, comparison and budget outcomes, their
   exact identities, nullable/unknown semantics and public integer bounds.
3. Keep `planned`, `reported_consumed` and `provider_corroborated` distinct.
4. Bind closed profile values and requirements to the native witness routes.
5. Freeze forbidden dependency edges: economics cannot call planning, signing,
   provider mutation, HTTP, SQL or process environment directly.

Expected capability files, subject to source admission:

| File or existing owner                                         | Single responsibility                                                                  |
|----------------------------------------------------------------|----------------------------------------------------------------------------------------|
| `ci_economics/sources.py`                                      | Exact collection source and optional reconciliation provenance                         |
| `ci_economics/reports.py`                                      | Bounded immutable producer assertions and measurement scopes                           |
| `ci_economics/comparison.py`                                   | Pure compatibility, paired difference and budget outcomes                              |
| Existing `collection.py`, `model.py`, `ports.py`, `profile.py` | Evolve their current lifecycle/value/process/resource contracts; do not duplicate them |

Do not implement these names as empty scaffolding. A new file must own an
independent invariant; reuse a current owner when that removes a redundant
boundary without concentrating unrelated policy.

### B. Durable And Provider Path

1. Add the design's required forward sequence after `20260906_0005`: retire
   economics `v1`, then install/backfill and admit `v2`. Preserve the existing
   single-transaction migration fence, published revisions, retained payload
   bytes and historical attestors; do not add a generic replacement transition.
2. Backfill exact legacy links for every collection without inventing provenance;
   validate many-to-one states, missing/orphan rows and transactional restart
   before switching FKs. Do not deduplicate or merge retention epochs.
3. Adapt the current single collection lifecycle and store to admitted sources.
   Keep database-time claims, generation/revision fencing and bounded cleanup.
4. Reuse the checked attempt reader inside the GitHub integration owner; do not
   expose a reconciliation-shaped provider contract to independent economics.
5. Add bounded discovery/explicit-registration paths and retain their coverage
   window, truncation and failure state. Discovery returns one explicit page
   without a write; registration resolves one attempt again after authorization.
   No cross-page completeness claim or hidden scan accompanies an API read.
6. Store immutable reports with exact conflict semantics; expire dependencies
   together and prove no retry/report can resurrect purged evidence.

Concrete adapter changes belong to existing `persistence/ci_economics_*`,
`integrations/github/ci_economics_provider.py` and their schema/UoW owners.
The source discriminator belongs to the existing collection table; reports
have their distinct immutable payload/producer boundary. Discover the current
Alembic head before naming the forward revision. Keep all predecessor revisions
unchanged and prove incompatible writer fencing through the real UoW.

### C. Authenticated Producer And API

1. Add an operation-scoped report route with bounded strict Pydantic input,
   duplicate-key admission, exact Actions identity and producer binding.
2. Reuse existing verifier/provider/bulkhead components where semantics match;
   keep scope authorization and report write authority separate from audit read.
3. Export the optional standalone producer through existing exact-file tooling.
   Wrap the unchanged target final gate or test command, preserving selection,
   error behavior and exit code; do not add a reporting dependency to the gate.
   Return a closed receipt with request-matched ID/digest in the inherited job
   output; reject missing, conflicting or additional fields without capturing
   process output or changing the command exit.
4. Add optional CPU/counter producers only for an explicitly supported narrow
   accounting scope. Never convert unsupported collection to a zero total.
5. Add scope-first bounded measurements, comparison and signal reads through
   `app/ci_economics*` and the existing HTTP economics route family.
6. Project versioned OpenAPI and client contracts from the admitted boundaries.
   UI is not required to configure or inspect the initial path.

Do not grant a permanent target bearer, introduce a privileged runner monitor,
or require successful telemetry to finish CI. The direct endpoint is the
initial transport; artifact retry is not implicitly included.

### D. Proof And Documentation

| Predicate                                      | Smallest causal witness                                                                                                                                        | Proof environment                                          |
|------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------|
| Independent source without fake reconciliation | Capture a provider attempt with no reconciliation row; reject wrong attempt provenance                                                                         | PostgreSQL integration in GitHub                           |
| Legacy coexistence preserves meaning           | Every row of design section 5's predecessor matrix, including unequal source times, zero/one snapshots and both overlap registration orders                    | Migration and PostgreSQL in GitHub                         |
| Claim/retention remains authoritative          | Exact PID/lock and database-time races, old credentials, expiry, purge, attempted resurrection                                                                 | PostgreSQL in GitHub                                       |
| Report origin and scope are exact              | Wrong audience/repository/attempt/check-run, expired credential, read-only token                                                                               | HTTP/provider unit and component tests in GitHub           |
| Input contract is total                        | Required/extra/duplicate/null/domain/cross-field mutants plus minimal/maximal valid controls                                                                   | Native contract tests in GitHub                            |
| Counters retain scope and units                | Waited-child scope, unavailable counters, detached-child non-claim, mixed/overlapping scopes, unsafe integers, negative/zero baseline and unsupported platform | Parameterized pure and executable producer tests in GitHub |
| Comparison is not arbitrary                    | Change each protected operand independently; allow only declared treatment differences                                                                         | Pure and API tests in GitHub                               |
| Telemetry cannot weaken CI                     | Failed and successful gates with absent/slow/rejected reporting retain identical exits                                                                         | Target executable witnesses in GitHub                      |
| Resources remain finite                        | Full page/cap/window boundaries, old-run rerun exclusion, conflict storms, scope-first cursor and bounded cleanup                                              | Static contracts plus native integration in GitHub         |

Register the requirement-to-predicate-to-oracle relation in the existing
Proofkit authorities. Route presence is not a passed witness. Reuse factories
and parameterization, but keep each independent operand falsifiable.

The frozen batch review adds four concrete acceptance controls to these rows:

- The response union's discriminator must name `sourceKind`, required in both
  serialized variants; regenerate OpenAPI and the client from Pydantic owners.
- Two distinct job IDs sharing the requested check-run in either page order
  must reject before report storage; unrelated valid check-runs remain valid.
- Migration commit, injected rollback and repeated upgrade must preserve two
  legacy sources for one attempt with unequal anchors and zero/one snapshot.
  Real-UoW legacy/provider capture in both orders must preserve the first
  snapshot and independently retained source lifetimes.
- Exact replay started before expiry, observably blocked on the source lock,
  and released after expiry must reject without renewing the first receipt.
  Include pre-expiry replay and both cleanup orderings. Moving the database
  time sample before the lock must be distinguishable by the replay-first case.

These are bounded correctness repairs and missing regression oracles, not new
planning policy. They remain unverified behavior until exact-target native CI.

Native qualification also requires all async witnesses to execute through the
bundled AnyIO pytest plugin with the repository's asyncio backend. Strict pytest
configuration remains enabled; collection or a missing runner is not execution.
Retained report, comparison and budget reads must serialize valid nested typed
payloads and reject independently mutated fields at every payload boundary.
REST-only measurement fixtures must retain unknown queue/wall results rather
than invent webhook observations. Keep the exact requirement, response-alias,
request-protocol and concrete-service import inventories synchronized; their
negative cases remain mandatory. These controls preserve existing semantics
and close the complete portable failure set before another publication.

Local verification is limited to admitted Ruff, mypy, schema/contract metadata,
documentation graph, import/ownership and no-emit client checks. Behavioral,
coverage, migration, container and browser execution is GitHub-only.

Update the current module/routing projections, API reference and an operator
how-to with one explicit baseline/treatment example and its limitations. Keep
old design/plan bytes unchanged. Later UI consumes the same API and must show
evidence quality, missing population, negative differences and expiry.

## 4. Review And Publication

Use the reviewer selection in `AGENTS.md`, normally one frozen main pass and
one control pass only after a material finding. Supply the full new source
path, contracts, migration states and independent operand matrix together;
avoid serial reviews of isolated happy-path fragments.

Before publication, bind the actual post-PR137 master and revalidate all changed
owners. Do not publish a stacked history merely to overlap CI waiting. Require
complete exact-head native gates, additive repair commits, squash merge and
separate postmerge verification. Preserve failed observations in the review
and measurement record; a rerun does not erase a contrary result.

## 5. Remaining External Qualification

Public-repository App delivery and target installation qualification remain
open. After separately authorized deployment, qualify report loss/recovery,
provider page limits, runner counter scope/overhead, database capacity and
retention, then compare representative baseline/treatment runs. No five-minute
or CPU-saving promise is made before that exact-subject evidence exists.

## 6. Native Diagnostic Repair

Native run34204334022 reached test-loop completion at2079.11 seconds and
session completion at2096.32 seconds, with aggregate coverage85.16%. Its
duration output contains a 1048733-character node-ID line and several 131k
lines. Those are diagnostic payload copies, not long-running test cases.
The overall job was still unresolved at this observation; do not infer a
passed gate from its coverage subtotal.

Add short explicit IDs to the five observed large-payload parameterizations.
Verify that removing only this metadata gives the identical before/after AST,
that each IDs list matches its cases and is unique, and that governed routes
do not select the former raw-value IDs. Run the unchanged static gates and
exact native Full Check. Retain the original run as cost/failure evidence;
do not raise a timeout, change data, skip a case or lower an oracle.
