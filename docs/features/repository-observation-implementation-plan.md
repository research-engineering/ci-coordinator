# Repository Observation Implementation Plan

Status: implementation in progress; native qualification remains open.

Pure policies, source batch registration, provider workflow-ID projection,
observation persistence, a forward migration, capability/catalog/ACL projections
and a commit-owning adapter have source implementations and partial native test
definitions. Current-admin application operations, bounded background discovery,
independent runtime maintenance, fixed outcome metrics, configuration/status/gap
HTTP routes and generated OpenAPI/TypeScript projections are implemented in
source. A bounded workflow-choice catalogue, current authorization, deadline
and HTTP projection now also have source and native witness definitions. Their
native qualification remains pending. The frontend enable/pause/filter journey,
bounded polling, uncertain-write replay, lazy gap display and development proxy
have source and native witness definitions. PostgreSQL race, quota, claim,
gap-retention and full cleanup-batch witnesses are also defined. Compact proof
routing passes structural admission. Primary and repair-focused independent
implementation reviews have completed; their confirmed source and fixture
issues are corrected. GitHub execution and rollout remain open. No stage is closed
merely by file existence or static checking.

Design owner: [continuous repository observation](repository-observation.md).
Delivery objective: one usable enable/pause/filter/progress journey, including
its storage, failure recovery, API/UI and native proof. Do not publish controls
that promise background collection before that collector is wired.

## 1. Order And Acceptance

1. Freeze the design, owner map and predicate/falsifier matrix. Independently
   review them under the current repository reviewer policy. Resolve every
   material ambiguity before implementation; model consistency is not proof of
   repository compatibility.
2. Implement pure configuration, selector, bounded scan state and claim
   transitions with strict transport models. Preserve existing source digests.
3. Implement forward schema/capability evolution, atomic page registration,
   configuration replay, scan transitions and finite retention.
4. Add the bounded provider projection and application operations, then wire
   independent maintenance and observability. No database transaction over I/O.
5. Expose API parity, generate OpenAPI/client projections and implement the
   focused Observation view plus historical retry-label correction. First bind
   user-visible workflow choices to provider IDs: the existing YAML discovery
   report supplies paths, not numeric IDs, and cannot be used as that authority.
6. Bind requirements and native witnesses; run allowed static gates, then exact
   GitHub native/mutation/frontend/DB qualification. One frozen implementation
   review follows writer stop; reopen only materially affected proof after fixes.
7. Squash only after exact-head required checks. Separately admit post-merge
   Full Check, immutable release and safe Swarm schema transition. Enable a
   bounded pilot only after live login and observation controls work.

## 2. Owner-To-File Map

Paths below are implementation destinations, not evidence that files exist or
that merely creating them closes the requirement. Existing files are extended
only at the named responsibility. Published migration and design bytes remain
unchanged.

| Owner                             | Files                                                                                                                                                                                                                                                   | Intended delta                                                                                                                                                                                            |
|-----------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Observation policy and identity   | `backend/src/ci_coordinator/ci_economics/observation.py`, `observation_commands.py`                                                                                                                                                                     | One scope configuration, selector, command identity/replay and finite bounds.                                                                                                                             |
| Scan policy                       | `ci_economics/observation_windows.py`, `observation_scan.py`, `observation_schedule.py`, `_observation_values.py`                                                                                                                                       | Window partition/progress, cycle preparation and exact lease transitions; small shared value admission, no I/O.                                                                                           |
| Gap identity                      | `ci_economics/observation_gaps.py`                                                                                                                                                                                                                      | Exact configuration/selector/cycle provenance, immutable expiry and bounded-detail loss watermark.                                                                                                        |
| Boundary and ports                | `ci_economics/observation_payload.py`, `observation_ports.py`                                                                                                                                                                                           | Strict Pydantic wire admission and capability-owned store/provider contracts.                                                                                                                             |
| Provider discovery                | `integrations/github/ci_economics_sources.py`, `ci_economics/discovery.py`                                                                                                                                                                              | Exact workflow/source projection; preserve manual discovery and source digest contracts.                                                                                                                  |
| Workflow choices                  | `ci_economics/observation_workflows.py`, `integrations/github/ci_observation_workflows.py`                                                                                                                                                              | One bounded, exact-scope provider page, finite inert metadata and explicit partial traversal. Reuse the GitHub client and response-binding helpers, not execution inventory admission.                    |
| Batch source admission            | `persistence/ci_economics_source_registration.py`, `ci_economics_collection_repository.py`, `ci_economics/sources.py`                                                                                                                                   | Bounded page admission and shared single-source path, finite capacity backed by measurements. Keep the query algorithm separate from the collection transition owner, within the same transaction.        |
| Observation storage               | `persistence/_schema_ci_observation.py`, `ci_observation_repository.py`, `ci_observation_codec.py`                                                                                                                                                      | Configuration, fixed scan rows, gaps, indexes, DB-time CAS and strict decode.                                                                                                                             |
| Observation transitions and reads | `persistence/ci_observation_config.py`, `ci_observation_claims.py`, `ci_observation_completion.py`, `ci_observation_scan_state.py`, `ci_observation_gaps.py`, `ci_observation_queries.py`, `ci_observation_lock.py`                                     | Keep configuration/audit replay, lease acquisition/completion, gap lifecycle and bounded reads under their distinct transaction responsibilities; the repository owns one shared error/rollback boundary. |
| Transaction adapter               | `persistence/ci_observation_adapters.py`, `ci_observation_unit_of_work.py`                                                                                                                                                                              | Atomic page/source/progress and committed public outcomes; a distinct observation capability set without changing ordinary collection UOW requirements.                                                   |
| Compatibility                     | One additive forward Alembic revision, `persistence/schema_capabilities.py`, schema-contract/attestation owners and runtime-role resources                                                                                                              | Exact observation capability/relations/grants; preserve economics-v3 writers and unchanged old data unless a proved shared-write conflict requires retirement.                                            |
| Application commands              | `app/ci_observation.py`                                                                                                                                                                                                                                 | Current command/read authorization and exact scoped results.                                                                                                                                              |
| Background application            | `app/ci_observation_scanning.py`                                                                                                                                                                                                                        | Bounded scan orchestration, fresh App membership and fixed outcomes without browser identity.                                                                                                             |
| Runtime                           | `runtime/maintenance_round.py` and existing composition/dependency owners                                                                                                                                                                               | Inject provider/store and independent bounded maintenance; startup reconciliation remains unchanged.                                                                                                      |
| HTTP                              | `api/http/ci_observation_contracts.py`, `api/http/routers/ci_observation.py` and route admission/dependencies                                                                                                                                           | Configuration/status/gap/workflow-choice API, exact body, role, CSRF, timeout and error projection.                                                                                                       |
| Browser API                       | `frontend/src/api/ciEconomics/observationClient.ts`, `observationSchema.ts`, `observationStatusSchema.ts`, `observationWorkflowSchema.ts`, generated OpenAPI projection                                                                                 | Bounded requests and separate command, status/gap and provider-catalogue admission; exact scope/revision binding.                                                                                         |
| Browser feature                   | `frontend/src/features/ciEconomics/ObservationPanel.tsx`, `ObservationEditor.tsx`, `ObservationWorkflowPicker.tsx`, `ObservationProgress.tsx`, `ObservationGaps.tsx`, `useObservationStatus.ts`, existing economics view owner and `SourceEvidence.tsx` | Focused durable controls, retained drafts, uncertain-write replay, visibility-gated single-flight reads, truthful progress/capacity/gaps and historical retry wording.                                    |
| Development proxy                 | `frontend/src/api/development/economicsProxyPolicy.ts`                                                                                                                                                                                                  | Exact method/path/query projection only; preserve existing session/CSRF forwarding and origin admission.                                                                                                  |
| Navigation and operations         | `ROADMAP.md`, `docs/INDEX.md`, module specification, HTTP reference and how-to                                                                                                                                                                          | One current entrypoint and executable operator steps; retain independent pilot/production non-claims.                                                                                                     |
| Proof                             | Runtime/UI requirements, compact Proofkit routes and existing native/mutation manifests                                                                                                                                                                 | Every changed authority and derived path routes to its causal witness.                                                                                                                                    |

The shorthand `ci_economics`, `persistence`, `app`, `runtime`, `integrations` and
`api` denotes packages under `backend/src/ci_coordinator`. Confirm exact existing
owner entrypoints before edits; do not create guessed parallel registries.
Native test locations follow the corresponding existing capability cohorts.

## 3. Predicate And Falsifier Matrix

Each row requires a non-vacuous valid control and the stated independent adverse
case. Table coverage is planned proof, not passed evidence.

| Predicate                 | Required causal witness                                                                                                                                                                                                                                    | Native class                              |
|---------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------|
| Exact command identity    | Replay same operation after response loss; change each actor/scope/payload operand and reject conflict.                                                                                                                                                    | Unit and PostgreSQL                       |
| Paired command/audit      | Replay command A after committed command B returns A's receipt without rewriting B or appending an audit event; fail between each audit/config/lane write and before commit.                                                                               | PostgreSQL                                |
| Selector admission        | All/selected forms, empty set, duplicates, unsafe IDs, missing/extra/null fields and valid extremes.                                                                                                                                                       | Unit and HTTP                             |
| Current authority         | Current admin succeeds; missing role, expired identity, wrong scope and bad CSRF cause no write/provider effect.                                                                                                                                           | HTTP and composed runtime                 |
| Durable intent            | Browser/session termination does not stop committed work; restart reads identical configuration.                                                                                                                                                           | PostgreSQL and browser                    |
| Pause fence               | Both actual DB-lock orderings: page then pause succeeds before boundary; pause then page rejects all source/progress writes.                                                                                                                               | PostgreSQL                                |
| Exact claim               | Mutate scope, lane, generation, revision, worker, token, outer interval, cycle start, window and page independently; each fails.                                                                                                                           | Unit and PostgreSQL                       |
| Hard expiry               | Real DB-time rejection after lock and stale early samples; exact acquired/expiry boundary SQL cases with controlled clock operands, separately from live holder/reclaim races.                                                                             | PostgreSQL                                |
| Lease consumed by DB work | Hold the source quota lock until lease expiry, then release; final single-clock CAS rejects and rolls back new sources, progress and gaps. Exercise the acquisition guard and clock-regression side independently.                                         | PostgreSQL                                |
| Page atomicity            | Fail after partial registration but before progress, during commit and during cleanup; no false success or skipped cursor.                                                                                                                                 | PostgreSQL                                |
| Recovery                  | Crash after claim, after provider response and after committed page with lost reply; replay safely and preserve counters.                                                                                                                                  | Application and PostgreSQL                |
| Provider binding          | Wrong repository/install/API version/route/query, malformed workflow ID and selected-ID mismatch.                                                                                                                                                          | Adapter unit                              |
| Workflow choice catalogue | Current authorization before provider access, exact page/scope, empty versus unavailable, safe unique IDs, bounded metadata including provider-built-in paths, hostile Link, page cap and cancellation; preserve configured IDs missing from a later page. | Adapter/application/HTTP unit and browser |
| Pagination                | Empty page, repeated sources, changed totals, partial page, search cap, subsecond sources across adjacent windows, exact-second saturation and finite split/continuation.                                                                                  | Adapter and pure policy                   |
| Source lifetime           | Aged source, rerun of old run, duplicate discovery and new observation time cannot extend eligibility/retention.                                                                                                                                           | PostgreSQL                                |
| Capacity                  | Existing replay at full quota, page crossing capacity, concurrent manual/scan registrations and no count overflow.                                                                                                                                         | PostgreSQL                                |
| Admission precedence      | Aged-out existing source at full quota remains outside-source-window; mixed replay/conflict/age-out/capacity page retains only the required cursor and cannot block forever after final age-out.                                                           | Unit and PostgreSQL                       |
| Global configuration cap  | Two actual concurrent creations at 255 commit at most one new configuration; updates, pauses and operation replays do not change population.                                                                                                               | PostgreSQL                                |
| Query cost                | Representative 10000 retained sources and skew; bounded population lookup once per batch, correct index/pool/lock budgets.                                                                                                                                 | PostgreSQL performance                    |
| Child cleanup cost        | 100 expired attempts with 2000 jobs each plus snapshot/report/signal rows and tombstones; prove deletion/survival relations and measure query count, plans and deadline against current profile.                                                           | PostgreSQL performance                    |
| Fair scheduling           | Two repositories and both lanes; one failing or backlogged lane cannot starve the others under finite worker budget.                                                                                                                                       | Application and PostgreSQL                |
| Provider/DB failures      | Durable bounded reason, retry cursor retained, no planning effect and no unbounded exception details.                                                                                                                                                      | Application and metrics                   |
| Gaps                      | Old outage interval, saturated span, quota blockage, distinct revisions/selectors/cycles, replay after eviction, cap/overflow watermark and expiry; no coalescing or false complete state.                                                                 | Unit and PostgreSQL                       |
| Gap continuation          | Exact scope/current revision/retained anchor; independently reject malformed, foreign, stale, evicted and expired cursors; restart after reconfiguration still pages historical gaps.                                                                      | Unit, PostgreSQL, HTTP and frontend       |
| Privacy                   | No raw payload, credential or URL query in storage, logs, progress or errors.                                                                                                                                                                              | Static and native boundary                |
| API/UI parity             | Enable, pause, configure conflict and uncertain result; reload/second session shows committed server state.                                                                                                                                                | Browser                                   |
| Honest UI                 | Pending scan differs from captured attempt; previous retry reason remains historical; unknown totals show no fabricated percent.                                                                                                                           | Frontend and browser                      |
| Accessibility             | Labelled controls, keyboard operation, bounded responsive layout and reduced-motion progress.                                                                                                                                                              | Browser                                   |
| Storage admission         | Empty/populated migration, injected pre-commit failure, exact old-data preservation; old economics writers remain admissible, new observation requires its capability; tamper each new relation/constraint/index/grant.                                    | PostgreSQL compatibility                  |
| Unchanged CI authority    | Observation unavailable/paused/capacity-limited cannot dispatch, activate, omit or alter independent FullCI fallback.                                                                                                                                      | Application and composed runtime          |

Use parameterization for independent scalar operands, not a shared expected
value copied from the implementation. Assert actual DB lock blocking before
releasing race barriers; sleeps alone do not prove a race. Extend existing
mutation cohorts with sensitive representative claim, pause, atomicity,
scope/filter and UI-outcome mutants; do not invent a second test runner.
Keep canonical inventory, expected IDs, concrete mutants and owner-class
coverage synchronized; the existing preflight validates these projections before
any native mutation run. Use the documented `pageNumber` query in an authorized
HTTP witness. Explain populated queries before cleanup and retain their exact
statement identity, cost observations, deadline and pool-release assertions.
Control review must include the temporal lock helper and basic enabled/live-lease,
expired, paused and reduced-motion display cases, not only their callers.
Force a new contender connection to open only after the observer's first real
statistics query completes. Require a later poll to detect its actual advisory
lock wait before releasing the holder, then require contender completion.
This causal barrier falsifies a stale transaction-local session inventory;
the event hook controls timing only and never replaces a database result.

For the maximum cleanup fixture, use real one-job parent capture followed by
bounded canonical bulk expansion in `_observation_cleanup_support.py`. Retain
all 100x2000 expired children, the live child and report/signal relations. Check
generated-row parity against the existing production codec and one complete
expanded snapshot through the production reader before ageing it. Restore
normal triggers before child insertion, bound preparation to 180 seconds and
record its duration independently of the unchanged committed-cleanup budget.
The ordinary native PostgreSQL witness remains the acceptance gate; do not
claim a speedup until its successor run measures the changed preparation.

## 4. Proof And Library Discipline

Before each semantic writer phase, bind current owner bytes, intended delta,
protected observations, derived projections, the smallest whole-chain gate and
independent validation. Recompile changed mechanism obligations: design-time
generic invariants do not stand in for Pydantic, FastAPI, SQLAlchemy/PostgreSQL,
async lifecycle or TypeScript/browser contracts.

Use admitted Pydantic boundary types, existing transaction/strict JSON helpers
and provider clients. Reuse is justified only where semantics match. Pydantic
cannot prove commit ordering, database locks or provider freshness. No package
upgrade, new dependency, broad abstraction refactor or unrelated audit repair
is necessary for this feature without a new owner-bound justification.

The current registries reserve `REQ-CI-RUNTIME-045` for observation and
`REQ-CI-UI-019` for its browser journey. Keep proof-route JSON compact and native
commands owned by the repository. Static checks include lint/format, type contracts, import
boundaries, module ownership, docs graph, OpenAPI and Proofkit admission.
Behavioral suites, schema/migration and browser tests remain GitHub-only.

## 5. Rollout And Stop Conditions

No live write is admitted by a proposed document. The eventual rollout binds
the exact squash source, native job set, image attestations and expected owned
Swarm service specs. A schema transition requires its existing compatibility
fence and drain policy; do not reuse the previous image-only access rollout for
a migration. Preserve all unrelated applications and database data.

Start disabled. A separately authorized bounded pilot target enable operation records
exact configuration revision and observation window. Observe useful stored
provider evidence, duplicate-safe reread, pause/resume, failure recovery and
finite provider/database cost. Do not mutate or dispatch pilot target workflows.
Failure of capacity, auth, scope, pause fencing or compatibility blocks rollout.
Missing live performance evidence stays an explicit qualification gap, not a
passing static requirement.

The batch does not close paired savings, all-attempt source coverage, cohort
analytics, outbound review-bot advice, selective CI or production qualification.
Authentication return-to-view remains separately owned D7 work; UI chat is last.
