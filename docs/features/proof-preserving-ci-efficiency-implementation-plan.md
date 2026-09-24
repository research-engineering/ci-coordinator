# Proof-Preserving CI Efficiency Implementation Plan

Status: implementation admitted after design review and plan control

## Contract And Execution Order

The [design](proof-preserving-ci-efficiency.md) owns preservation predicates.
Use the following ordered, bounded changes. This is not a new CI framework.
The independent [inventory plan](automatic-app-inventory-implementation-plan.md)
owns the product change delivered in this user-requested batch.

## 1. Freeze Native Collection And Cost Evidence

Add `scripts/ci_test_plan.py` for native universe admission and a file-cohesive
assignment; `scripts/ci_test_report.py` for pytest collection/outcome/phase
receipts; and `scripts/ci_test_execution.py` for plan, run and combine commands.
Keep each module limited to its named responsibility. Extend existing coverage
diagnostics rather than inventing another test framework.

One GitHub planning process collects the existing full universe, including
tooling and explicit conformance paths. Record canonical repository-relative
file plus parameterized node ID, marker and fixture-name metadata. No tests run
during collection. Reject duplicate identities and unknown/out-of-root files.
Preserve skips explicitly. Never deserialize executable objects from artifacts.
Keep pytest's existing backend working directory and config-relative import
paths for collection, shards and serial qualification. The repository root is
the report identity root, not a replacement execution directory. Exercise
namespace-package integration helpers and unit helper imports through the real
runner in an isolated project. Preserve long native parameter IDs without
truncation or an arbitrary per-ID ceiling; the complete artifact retains its
32 MiB bound. A long-ID plugin witness must preserve every phase and identity.

The first public same-head serial attempt (run `35932427988`) completed its
test loop but could not write the native report: an incidental parameter ID
for a 4 MiB + 1 log-overflow payload occupied 4,194,405 bytes and was repeated
across collection and phase records. Keep the payload and oracle unchanged;
give that test's three parameter cases explicit short IDs at collection. This
is not truncation by the report writer. Recollect and qualify the changed source
on a fresh exact run before claiming serial/shard parity; do not reuse the
failed run's receipts.

Freeze source SHA, run/attempt, dependency/toolchain identities, all nodes and
the single assignment in a bounded JSON artifact. Each shard verifies the same
plan before execution. Coverage/data artifacts must use non-executable formats,
regular files, confined paths and exact inventory; extra or missing receipts
cannot silently be ignored.

## 2. Use A Concrete Minimal Scheduling Rule

Select deterministic file-cohesive longest-processing-time assignment using
stdlib `heapq`: sort files by descending elapsed hint then path; assign each to
the least-loaded shard with index tie-break. Hints never determine inclusion.
Unknown files use the measured cohort mean, with a positive fallback when no
measurements exist. Store compact per-file costs, not thousands of tracked
parameter rows, in `proofkit/ci-test-costs.v1.json` with source/run provenance.

Start with four backend shards, four isolated PostgreSQL shards and one
tooling/conformance shard. Empty assignments are rejected. Counts are explicit
policy inputs, not inferred from CPU count. This keeps estimated group work
near three minutes plus setup before hosted qualification; the five-minute
target is not yet a measured guarantee.

Revision for the expanded public-repository population: admit three tooling
shards using fresh per-file phase hints from the successful exact-source run
`35918291178`. Validate the union and disjointness of all nine source reports
before replacing the hint file, preserving source/run provenance. Change both
native workflow matrices and the planner's expected assignment set together.
Install the admitted scanner on every tooling shard; do not pin scanner tests
to one shard merely to preserve the old matrix. Keep all emitted shard receipts
mandatory at the aggregate gate. Measure the resulting critical path and total
runner occupancy against the one-tooling-shard baseline, including setup cost.

Compared with pytest-split, preserving files avoids repeated expensive module
fixtures. Compared with xdist on one standard runner, separate jobs do not
make four database containers compete with CPU-heavy codecs on that runner.
Revisit counts after native timing and runner-occupancy evidence; do not add
adaptive capacity, a queue or a new service to this implementation.

## 3. Preserve Aggregate Proof

Modify `.github/workflows/python-persistence.yml` to run planning, the explicit
matrix and a coverage aggregator. Retain the existing required gate as an
`always()` join and the serial `scripts.python_witness coverage` route.

Each shard runs real pytest with original branch and subprocess instrumentation.
Disable only per-shard aggregate floor evaluation; run every original overall,
owner and changed-critical floor after `coverage combine`: coverage.py owns
the aggregate `fail_under` in pyproject; `python_coverage_policy` owns the
additional risk floors and does not enforce aggregate. Fail closed if any
coverage dataset cannot be read and materialized through `CoverageData`, or
lacks branch data. Include a corrupt input with a recomputed matching receipt
hash while the other inputs independently meet every floor. Fail closed if any
expected shard/node is absent, duplicated, failed, cancelled, foreign, changed
between collection/execution or missing a terminal outcome. Include all shard
results in the gate; no `continue-on-error` or path-based exclusions.

Reject unexpected skip/xfail and failed setup/teardown even with matching IDs.
Admit only the two exact real-esbuild parameter identities and current reason
when tooling lacks esbuild, retaining their installed execution in the required
repository-quality job. Compare complete identities/outcomes with same-head
serial qualification; do not turn the historical 6,831 count into a constant.

For the manual serial comparison only, align its process deadline with the
observed full-universe cost: 3600 seconds inside a 75-minute job. The previous
2400-second limit stopped run `35940367926` at 83% despite successful normal
shards; their shard-session time totalled 2819.67 seconds and recorded test
phases totalled 2733.90 seconds. Keep the same complete
collection, outcome/coverage comparisons and terminal timeout failure. Update
the existing negative timeout witness and exact workflow budget assertion;
regenerate dependent self-CI and runtime entrypoint fingerprints. A fresh
same-head pass, not this bound change, qualifies serial/shard parity.

Native script witnesses use small synthetic pytest projects for missing/extra
node, duplicate assignment, failed/skipped/empty shard, stale SHA/run/attempt,
malformed artifact, traversal and bad coverage cases. They are GitHub tests,
not locally executed static checks. Keep bounded human progress separate from
JSON reports and retain process-group timeout/cleanup behavior.

## 4. Remove Repeated Fixture Construction

For `backend/tests/unit/target_authority_evidence/{test_codec,test_file}.py`,
add a module-owned seed plus function-owned deep copy in the local fixture
owner. Leave the general-purpose fresh evidence builder unchanged. Every codec,
file and replay operation under test still executes. Add nested-mutation and
seed-isolation tests; do not bypass any guarded production copy contract.

For `consumer_contract_lab/test_consumer_contract_lab_end_to_end.py`, construct
the same Git/source epoch once per module and copy both repositories per test.
Use ordinary copies, not hardlinks/shared index/object state. Preserve actual
CLI subprocesses, committed source binding, and all adversarial worktree and
bytecode cases. Add sibling/seed mutation-isolation and exact-commit witnesses.

Do not coalesce PostgreSQL migration/ACL cleanup in this batch: current suites
change those owners and no cheaper complete isolation proof is established.
Keep real DB clocks, lease horizons, commits and PID/lock barriers unchanged.
Record that retention as a reviewed safety choice, not an omitted optimization.

## 5. Partition Independent Mutation Work

Use a matrix of the seven existing complete persistence mutation cohorts.
Partition the 34-member database-compatibility cohort into two deterministic
subsets only if exact manifest union/disjointness and terminal receipt admission
are implemented; otherwise retain that cohort whole and report its remaining
latency. Every mutant keeps its current baseline, restoration and classified
failure evidence. Baseline caching is not admitted merely from shared test IDs.

Update `scripts/mutation/mutation_suite_specs.py`, its execution-envelope
admission, existing tests and quality metadata where matrix structure changes
the current serial-envelope assumption. Keep harness self-tests/preflight
once in their proper gate, not repeated unnecessarily in every worker.

## 6. Account For Every Job And Retained Cost

Add an artifact/report projection covering every collected node and every job
in all tracked workflows. Cost, fixture ownership, oracle-preserving disposition
and unknowns remain explicit; no top-N cap masquerades as completeness.
Measured fixture-phase elapsed is inclusive, not processor time.

Expose monotonic stage timings in the existing quality-plan/Dev Container
witness owners. Preserve provision, user/mount/network, installed toolchain,
portable proof and cleanup stages. Do not remove the portable proof as a
duplicate of a host run. Report release/requester or other unmeasured jobs as
unmeasured, not zero-cost or optimized.

## 7. Integrate And Qualify

Update Proofkit requirements/routes, documentation index and ROADMAP D4.
Preserve all floors, mutant IDs and existing requirement coverage. Locally run
only admitted static checks. Freeze the implementation for one independent
review under `AGENTS.md`; fix material findings and repeat only their affected
scope. Publish one initial commit and open the requested PR.

Run exact-head native Full Check. Compare with the recorded baseline and, for
causal speedup claims, the same-head serial route. Report critical path, every
job, case/fixture phases, queue/occupancy, failures and retained bottlenecks.
Do not claim p95, total CPU reduction or maximum performance from a single run.
The original scheduling batch did not authorize a local test fallback,
threshold reduction or timeout increase. The separately evidenced process
budget repair below does not change any test or coverage result.

Native qualification also checks every dependent workflow oracle: trigger
domain, mandatory job dependencies, complete mutation matrix, preflight order,
tool provisioning, conditional serial outcome and the package-bound workflow
disposition digest in both the canonical runtime specification and packaged
resource. The installed-wheel comparison remains the independent validator.
Update those projections together with scheduling; retain
their negative cases instead of deleting a stale assertion wholesale. Derive
reported requirement cardinality from the canonical trace inventory, with a
separate assertion for the new requirement, rather than freezing a historic
count. The integrated requirement is `REQ-CI-DEV-018`; retain the published
developer-experience requirements `REQ-CI-DEV-013` through `017` unchanged.
Bind the epoch to tracked `mise.toml` and `mise.lock`, not an assumed
`.python-version`; mutate each toolchain operand independently in its witness.

Coverage dataset admission owns the complete temporary-resource lifecycle.
Use `CoverageData.close(force=True)` for the owned in-memory validation data;
ordinary close retains its SQLite connection. The real coverage-join witness
forces collection of released objects before returning so strict resource
warnings fail that witness, including failed-admission paths. This is less
complex than a new SQLite reader or process per artifact and preserves every
existing coverage floor. Revisit only if the pinned library changes this API.

## 8. Prior Portable Process Budget Repair

This records the previous cap adjustment. Section 9 owns the current aggregate
lifecycle model; the four-term subtotal below is not a complete call inventory.

1. Bind the exact PR #10 failing job and inspect the process timeout versus
   test failure. Preserve the full native suite and its original outcome
   requirements; do not treat 98% as completion.
2. Set the portable `python.test` process to 1,080 seconds. Project its
   wrapper, parent, Dev Container and branch envelopes as
   `1140 s -> 1380 s -> 2160 s -> 32306000 ms`.
   Keep ordinary native shards and the 40-minute outer job unchanged.
3. Update the source-owned quality plan, direct budget assertions and
   governing deadline document together. Split the portable test deadline
   from the native shard's 900-second process ceiling, then verify the native
   failure witness still stays below its 960-second workflow watchdog. Run
   exact plan and matrix-risk admission; renew the content-addressed execution
   and witness digests for every changed owner. Run scoped test witnesses and
   the hosted Dev Container on the new exact SHA.
   Regenerate self-CI from its source owners, then refresh the canonical and
   packaged `generated.documents` fingerprint and run the entrypoint
   disposition witness; the generator check alone does not close that
   downstream classification.
4. Measure actual portable elapsed time and revisit the bound if the failure
   repeats or its headroom remains too narrow. A passed rerun of the old SHA
   is diagnostic, not a substitute for the repaired contract.

## 9. Cooperative Lifecycle And Aggregate Budget Repair

1. Preserve the complete command, test and native shard populations. Treat the
   original four-term Dev Container sum as historical, not proof that all
   sequential operations fit.
2. Share one work deadline across source admission and all witness operations;
   bound cleanup discovery/removal as one phase, followed by bounded source
   finalization. Preserve the 2,160-second catalog envelope and the 40-minute
   direct job. Test cumulative consumption, the exact boundary, late success,
   and multiple network operations independently of the implementation formula.
3. Preserve the primary exception through cleanup for RuntimeError, OSError
   and KeyboardInterrupt, including a failing/interrupted cleanup. Verify that
   an unrelated or replaced container is never removed. Keep timeout stdout
   and stderr bounded and distinguish both from the primary failure cause.
4. Strengthen the PostgreSQL COPY witness under its existing owner: identify
   the injected exception and independently observe the late COPY checkpoint;
   distinguish a test deadline from the synthetic cancellation being tested.
   Retain canonical rows, transaction scope, trigger state, rollback and retry.
5. Run scoped static checks, update transitive risk/generation fingerprints,
   obtain an independent review and execute Full Check on the published head.
   Hosted PostgreSQL and Dev Container witnesses, not static checks, qualify
   their respective runtime claims. Keep CI-006 open for remaining measured
   cost reduction; do not refresh a single cost hint without cohort evidence.

| Changed owner | Preserved behavior | Independent acceptance |
| --- | --- | --- |
| Dev Container witness | Exact source, mounts, ownership, portable command population, finite output and native shard limits | Failure-kind matrix, fake-clock cumulative boundaries, exact generated-source checks and hosted least-privilege container |
| COPY fixture/test | Same canonical population, SQL transaction, normal insert triggers and cleanup oracle | Reached injection identity, non-injection failure rejection, committed readback, rollback and identical retry |
| Existing design/plan | One task register and unchanged runtime authority | Historical/current scope alignment, documentation graph and exact witness bindings |

## Rollback And Completion

The serial coverage entrypoint and original mutation manifests remain usable.
Revert workflow scheduling independently of fixture improvements if native
partition/aggregation fails. No production runtime, schema or client workflow
changes are needed. Completion means demonstrated proof-preserving improvement
and the reviewable PR, not a universal five-minute or global-optimality theorem.
