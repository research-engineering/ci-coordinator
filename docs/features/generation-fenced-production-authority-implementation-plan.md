# Generation-Fenced Production Authority Implementation Plan

The [design](generation-fenced-production-authority.md) owns semantics.
This plan closes Stage D of the [existing target-authority plan](target-authority-relation-implementation-plan.md).
It is one atomic implementation objective, not a sequence of independently
enabled partial production paths.

## 1. Entry Conditions

The worktree is based on pre-CI preparation squash `4c711ef`; revalidate that
base before source implementation and first publication.
Retain all pre-existing design and implementation-plan bytes unchanged. Project
the new owner's explicit delta into active machine requirements and routes;
do not retain the old Stage C runtime-import prohibition in that projection.
Read the actual
production receipt, issuance, persistence, identity and provider contracts;
compile fresh writer obligations for source, framework and migration changes.

Before transport or persistence code, resolve the exact transport and retention
bounds for staging and the queries proving the locally observable drain. The
pure relation-to-production binding can be implemented first using its existing
bounded owner values; it cannot activate anything independently. If bounds cannot
preserve the design's memory and admission budgets, revise this new design
before implementation, not after a failed rollout. Do not treat a signature or
test fixture as the missing external deployment observation.

## 2. Owner-To-File Work

| Order | Owner and concrete file group                                                                                                                                                                                                                                     | Change and independent acceptance                                                                                                                                                                                                                                                                                                                                                                 |
|-------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1     | `production_admission/model.py`, `codec.py`, `registration.py`, `subject_projection.py`, `resources/production-admission-envelope.schema.v2.json`; new focused `relation.py`, `current_evidence.py`, `cutover_state.py` and `cutover_drain.py`                    | Define the exact v2 signed relation/generation, current observation, state projection and independently signed remote drain. Reuse owner codecs; reject v1 for enforcement. Differential schema/native decoding and correctly re-signed one-operand mutations must reject                                                                                                                         |
| 2     | Existing `target_authority_evidence/{codec,replay,model}.py` consumers; `production_admission/relation_admission.py`                                                                                                                                              | Reuse dormant replay unchanged where possible. Replay the bundle inside admission rather than trusting a public value's type name; verify scope, policy, catalog, registry and owner epoch relations. Same IDs with different full rows must not pass. Keep the receipt value in `relation.py` to avoid a model/replay import cycle                                                               |
| 3     | New `persistence/_schema_production_cutover.py`, `production_cutover_repository.py`, `production_cutover_unit_of_work.py`; existing production-registration and runtime-state schema/attestation owners; two forward revisions required by the capability algebra | Retire the two incompatible v1 capabilities, then add their v2 successors and the cutover capability in the same outer migration transaction. Retain immutable bundle bytes, stage scope grants and implement generation/latch/revocation/audit transitions under the existing scope lock. Add exact DB constraints and least-privilege grants; preserve applied migration files and v1 attestors |
| 4     | New `app/production_cutover.py`; production-owned command/port contracts                                                                                                                                                                                          | Coordinate stage, inspect, latch and activate through one use case per capability boundary. Admission before effects, exact command replay, configured role and repository scope. Do not introduce a pass-through service or duplicate domain validators                                                                                                                                          |
| 5     | New `api/http/routers/production_cutover.py` and transport contract module; existing `api/http/dependencies.py` and `app.py`                                                                                                                                      | Expose the same complete versioned command/read capability through normal control-plane authorization, payload and deadline admission. Verify served responses, internal OpenAPI schema and unauthorized no-effect behavior; public docs remain disabled                                                                                                                                          |
| 6     | New `app/production_request_evidence.py`; existing `integrations/github/workflow_authority.py`, `workflow_inventory.py` and `governance_observation.py` consumers                                                                                                 | Acquire exact current workflow-source and composite `ProviderAuthoritySources` evidence through ports, under bounded observation and dependency budgets. Preserve caller/reusable-workflow distinctions and require coverage of every admitted source coordinate                                                                                                                                  |
| 7     | Existing `app/dynamic_plan_service.py`, `production_admission/authority.py`, `persistence/runtime_issuance_repository.py`, and relevant issuance row/codec owners                                                                                                 | Consume the current witness and generation in the actual public planning and transactional issuance path. Preserve all previous guards. Generation/latch/policy/time changes after application admission must still prevent selected persistence                                                                                                                                                  |
| 8     | Existing `runtime/composition.py`, runtime-settings admission/contracts, startup resources and finite metrics owners                                                                                                                                              | Wire services without auto-activation at startup. Keep non-enforcing mode functional and staged state inactive. Close resources in the current lifecycle; finite outcome labels must not leak subject or evidence data                                                                                                                                                                            |
| 9     | Runtime requirements, architecture traceability and module ownership, import policy, database access profile, Proofkit source routes and required tuples, API contract projections, README/INDEX/ROADMAP                                                          | Activate `REQ-CI-RUNTIME-030` only with this complete source cut and all native witnesses. Update mechanically derived artifacts through their owners and preserve unrelated rows. Document the v1 admission change visibly                                                                                                                                                                       |

These names describe responsibility boundaries, not a mandatory file count.
Reuse an existing cohesive owner when it preserves all required observations;
add a file only for a distinct owner or a demonstrably simpler bounded unit.
Update the concrete-service policy whenever a new service definition is added.

## 3. Required Native Proof

### Admission And Current Evidence

- Establish a real, nonempty baseline, independently produced relation, exact
  replayed evidence and correctly signed v2 receipt that permit selected
  issuance through `DynamicPlanService` and the real signed issuer.
- Mutate each signed coordinate independently, including generation, scope,
  predecessor, release, relation, evidence bytes, manifest, provider digest,
  policy, catalog, registry, owner epoch and time. A new valid signature must
  not hide a contradictory relationship.
- Exercise v1, malformed/unknown schemas, unsupported signing keys, missing
  retained artifacts, wrong bundle role, reordered/missing/extra rows and
  same-ID/different-value relations. Do not count unreachable negatives.
- Show that unchanged workflows at a new application commit preserve the
  stable manifest while source-binding identity changes. Separately reject a
  changed workflow outside the registry adapter and every admitted source's
  stale/foreign commit, owner, repository, branch, mode and object type.
- Reject unavailable, stale, late or mismatched provider governance. Mutate
  acquisition time and database-time order independently. Exercise deadline
  equality and completion after an await; repeated reads cannot renew evidence.
- Independently mutate provider workflow active state, complete workflow set
  and branch rules. A governance-only digest must not substitute for the
  composite provider epoch. Preserve stable identity when only source revision
  provenance changes.

### PostgreSQL Lifecycle And Races

- Run complete stage -> latch -> drain -> activate -> issue -> latch -> next
  generation scenarios with real PostgreSQL, retained bytes and audit replay.
- Prove exact duplicate staging is idempotent and a digest/bytes or command
  identity conflict is rejected without partial state.
- Race two activation commands at the same prior revision; at most one wins.
  Race issuance against disable, revocation, policy replacement and generation
  activation. Observe the actual persisted outcome, not only a mock call.
- Keep the latch for stale prior revision, live predecessor plan or lease,
  nonterminal operation, missing remote-execution drain, old routable replica,
  expired receipt, cancellation, rollback and unavailable provider.
- Hold an actual PostgreSQL audit-write lock past a fresh operation's time bound
  after its admission reads; staging, selected issuance and activation must reject and
  roll back both state and audit, not merely reject before reaching that await.
  Exercise the public transactional adapter: rejection must select rollback,
  never attempt commit on a rollback-required unit of work.
- Re-register the same receipt from two replicas, restart after staging and
  restart after activation. Startup must never clear the latch or revive a
  revoked generation.
- Preserve exact envelope/audit/relation references through retention. Reject
  removal of referenced evidence and prove bounded capacity exhaustion without
  eviction. This first profile does not expose physical deletion of accepted
  bundles; future archival admission is not claimed by receipt expiry.
- Test fresh schema and forward upgrade from the previously applied migration
  graph, actual DB constraints and runtime-principal privileges. No migration
  source rewrite can substitute for upgrade evidence.
  Predecessor migration fixtures seed valid retained data through the existing
  repositories inside a migration-owned transaction. They must not require the
  current runtime principal to pass its successor schema contract on an old
  schema, or weaken its grants to make fixture setup succeed.
- Backfill actual signed-plan expiry and reject a malformed legacy projection;
  exercise current-versus-old terminal revisions, registration crossing the
  cutover latch and an old replica with an unfinished request. A pre-drain
  empty query cannot be used as the final activation witness.
- Exercise begin-cutover on an existing disable, ordinary enable during drain,
  stale prepared registration after a complete generation change, and a
  registration without an issued plan. Retain explicit new provenance and
  conservative legacy membership; zero omissions do not prove FullCI.
  Also revoke active authority before any successor is staged, reject foreign
  authority/scope/revision operands without effects, and keep activation closed
  until a successor exists. Revoked identity remains inspectable history, but
  cannot be returned as usable active authority.
- Accept a current canonical `conflict` as non-success local terminal evidence;
  reject an old-revision terminal, inconsistent lease, malformed canonical row
  or skipped locked row. Independently require signed remote execution closure.
- Mutate the revocation floor, acquisition hint domain, expected scope revision
  and each remote-drain claim independently. Reject expiry equality and a drain
  observation predating the latch; restart must preserve the revocation floor.

### HTTP, Runtime And Target Behavior

- Test role x operation x repository scope, missing/revoked identity, duplicate
  and malformed requests, large payload, saturation, cancellation, error
  redaction, correlation, bounded staging and resource cleanup.
- Cross the real child-process boundary with valid evidence and independently
  malformed byte, JSON and envelope shapes. Validation exceptions must become
  a redacted invalid outcome, never an exception-deserialization failure;
  rejection must release capacity for a subsequent valid request.
- Prove default non-enforcing FullCI and the exact successor-enforcing
  composition with real adapters at each claimed boundary.
- Exercise independent consumer fallback when any added check is unavailable.
  Preserve caller/target registry identity and exact shard/test coverage.
- Add causal mutation witnesses for missing generation, skipped current
  evidence, ignored latch and an omitted signed relation operand. A surviving
  mutant prevents closure; unrelated fixture failures do not count as kills.
  The pure state model couples a present cutover latch to revocation of the
  current generation, so its revocation/latch mutant removes that combined
  admission gate rather than an equivalent single conjunct. Derive command and
  aggregate timeout projections from the full mutant count; retain the current
  GitHub job limit when it already covers the result.

Native failure diagnostics use short tracebacks and stop at the first failure
inside the bounded coverage-command deadline. Waiting for twenty failures
lost nine observed failures' tracebacks when the full run timed out. The first
failure already precludes acceptance; continuing cannot make that run pass.
A failed run makes no claim about unexecuted cases; a successful gate still
executes the complete selection and applies the same coverage threshold.
Temporal fixtures sample the database after predecessor effects and honor the
signed wire's millisecond precision; neither a backdated override nor a
pre-admission constructor failure proves the intended later rejection.
Downgrade tests assert the current forward-only boundary and retained rows,
without pretending an earlier, now unreachable guard was exercised.

For each cutover-at-registration or cutover-at-issuance scenario, preserve the
original observation and check its database-clock freshness before and after
the real persistence boundary. Run the same registration operands successfully
in a rollback-only transaction before changing authority. Require the real
generation-change rejection at issuance, and the expected boolean registration
outcome. Retain completed observations outside application exception handling,
so a swallowed assertion, expired observation or setup error cannot pass the
negative test. Do not refresh the original observation to manufacture freshness.

Project the [successor aggregate budgets](generation-fenced-production-authority.md#8-completion-boundary)
through Python witness, Proofkit command and Dev Container parent together.
Recompute the Dev Container stage envelope, every branch-aggregate child plus
its orchestration reserve, and the global command cap. Before publication,
admit the real graph with the existing bounded `load_quality_plan` loader; do
not run its command-executing `main` locally. Native negative cases must reject
a missing or one-millisecond-short stage envelope and an aggregate reserve
exceeding its parent. Structural Proofkit admission remains separate evidence.
Test exact complete argv, unchanged standalone tool bounds, wrapper reserves
and rejection of a portable command exceeding its parent. Keep one covered
persistence pass, all existing selections and the coverage threshold. Retain
the slowest-phase diagnostics from the next complete native run for the CI-cost
roadmap; a timeout or incomplete coverage report still blocks merge.
After the second coverage timeout, obtain the bounded fixture cost profile in
the independently completing repository quality job. Validate the measured
call path before optimizing fixture construction, schema admission or replay;
do not infer a culprit from the last printed test or bypass real admission.
Retain the unchanged complete coverage pass as the final acceptance oracle.

For the measured kernel ASCII scans, preserve the design's exact equivalence
conditions. Exercise empty/plain/control/quoted/DEL/all-ASCII strings, object
keys and nested values, plus two-, three- and four-byte Unicode. Independent
standard JSON string encoding owns expected bytes; exact and one-byte-short
limits must keep saturated failure coordinates. Raw UTF-8 boundary tests and
surrogates before or after a long ASCII prefix must retain rejection even after
deferred byte overflow. Preserve existing golden hashes, host-subclass, numeric,
node/depth and strict-ingress tests. A narrow fresh independent review covers
this new shared-kernel scope; earlier cutover review is not its proof. Compare
the native profile before removing its temporary duplicate, and require the
unchanged complete native gates without another aggregate timeout increase.

For raw-size equality, use nested values and keys so the error path or pointer
distinguishes early raw rejection from deferred escaped-output rejection.
For Unicode precedence, put an admissible overflowing element before the late
invalid value or nested key. Require baseline success and assertion-caused kills
for `B30` (raw equality), `B31` (late scalar admission) and `B32` (late key
admission) in the existing audit-byte mutation suite. Seventeen mutants require
1,080,000 ms including their declared reserve, within the unchanged 1,800,000 ms
outer envelope. These are distinct oracles, not proof of every possible mutant.
Keep the separate inventory-test expectation at 17 rather than deriving it
from the manifest under test. After removing the measured temporary profiling
step, rebind both workflow-disposition projections to all current member bytes.

Keep the two expiry-during-audit cases bounded by the scenario-specific
15/10/16-second fixture and observation windows from the design. Configure only
their real unit-of-work transaction's lock timeout to 27 seconds and assert the
complete envelope against actual PostgreSQL settings. The unchanged production
participant profile remains the runtime owner. Bound both database-time probes
with the expiry wait, and close an entered unit of work if test setup fails.
Preserve actual PostgreSQL lock and time barriers, the public transactional
adapter, exact rejection and complete rollback snapshots; do not substitute a
sleep or a mocked clock. The next exact native PostgreSQL run must complete
both cases; static review alone cannot close them.

The observing unit of work publishes its own backend PID after successful
setup. Admit that PID or reject an early completed operation under the same
observation supervisor; the SQL predicate must bind both waiter and holder.
Require a wrong-PID probe to reject an already observed block, preserve original
task errors, and materialize only the three settings rows before dictionary
construction. Predecessor commands and final reads retain the ordinary store.
Recheck these changed predicates independently on the frozen source, alongside
the exact native stage/activate cases; a prior unresolved attribution result
does not close the repaired epoch.

The [operator procedure](../how-to/stage-and-activate-production-authority.md)
projects the API-first lifecycle. This batch adds no frontend administration
screen; its internal HTTP schema and native contract tests own the new routes.

## 4. Verification And Delivery

Use only repository-admitted bounded static checks locally. Behavioral unit,
property, integration, migration, HTTP, container, coverage and mutation proof
must run through GitHub Actions. Freeze the source, tests, design, plan,
requirement/projection rows and transitive prerequisites for an independent
Astra/max review. Bound additional review to a material counterexample or an
uncovered independent scope; do not repeat the same broad review indefinitely.

Publish one owned nonmerge initial commit over the settled master. Repairs to
an open PR remain additive. Require the complete green exact-head Full Check
and provider-required checks before squash merge, then inspect post-merge
Full Check separately. A reviewed source snapshot does not validate later
patches, and a structurally admitted Proofkit route is not native proof.

The batch may close source implementation only. E1-E3 operational activation,
pilot target and the later two-repository pilots, measured savings, UI expansion and
the remaining roadmap are separate obligations, not implied by this merge.
