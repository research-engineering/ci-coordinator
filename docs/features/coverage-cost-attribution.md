# Coverage Cost Attribution

Status: implementation design

Owner: B3 repository verification, `REQ-CI-PROOFKIT-006`.

## Decision

Expose every native pytest setup, call and teardown duration in the covered
GitHub run before selecting a cost reduction. Use pytest's existing
`--durations=0 --durations-min=0`, not another profiler or test runner.
The [implementation plan](coverage-cost-attribution-implementation-plan.md)
owns execution and acceptance; the [roadmap](../../ROADMAP.md) owns priority.

## Evidence And Scope

Full Check `34158899084`, source `d0acd4117970d8488fa56ddcf8e58b5e72688ccb`,
reported 5,822 passed, two skipped and 84.80% coverage. The monotonic phase
markers place 2,344.46 of 2,380.25 seconds inside the test loop. Collection took
19.95 seconds and finalization 15.83 seconds. This refutes final coverage
processing as the dominant cost in this run, but does not identify the expensive
operation inside a fixture or test. The 25 slowest entries are not a complete
cost distribution. Source `9919e724bb9ed724b8fa54690eb2abf863cee6f9` has the
same source tree after squash.

```text
T_session = T_collection + T_test_loop + T_finalization
T_test_loop includes setup, call, teardown and runner overhead
Top25Durations != CompleteAttribution
CompleteDurations != CPUTime != CausalSpeedupProof
```

The diagnostic step changes reporting only. The reduction below preserves
product business logic, domain boundaries and database authority. The measured
fixture preparation change below retains isolated per-case inputs; equivalence
cases are added without removing existing tests.

## Protected Observations

The exact covered paths, branch/subprocess instrumentation, coverage policy,
84% total floor, risk-owned floors, native exit status, `--maxfail=1`,
environment projection, 2,400-second child deadline, 2,460-second parent
deadline and existing bounded output capture remain unchanged. Standalone
test and persistence commands retain their own reporting and environments.

The reporting options select already-collected pytest reports, not tests:

```text
SameInvocationExceptDurationRendering
  => SameTestSelection and SameCoveragePolicy
```

This is a source-level selection argument, not proof of identical wall time.
The [pytest documentation](https://docs.pytest.org/en/stable/how-to/usage.html#profiling-test-execution-duration)
and pinned runner implementation own option semantics. The exact command
oracle must reject a missing duration option, altered path, weakened coverage
argument or changed timeout. Native CI remains the execution witness.

## Cost, Alternatives And Revision Conditions

Pytest already retains and sorts report durations. Rendering the full finite
list adds output proportional to that run's report count; it adds no new
collection or per-test timer. Only test IDs, phases and durations are exposed,
as in the former top-25 report. No environment, credential or request-body
logging is introduced. The existing 64 MiB process capture remains fail-closed;
verify the actual output size instead of assuming a bound for future suites.

Keeping 25 entries is cheaper but cannot locate distributed fixture cost.
Full cProfile adds instrumentation to a run already near its deadline.
Premature sharding can hide CPU waste, while broader fixture scopes can leak
mutated schema/ACL state. Neither is justified by the current evidence.

Aggregate the native durations by file and phase, retain runner/source identity,
and inspect the dominant owners. A concrete optimization requires an additional
owner-bound argument and falsifiers in this same batch before implementation.
Do not lower coverage, remove tests, enlarge timeouts or claim savings from this
diagnostic change. Revisit reporting if volume becomes material or the complete
distribution is no longer needed; preserve a recoverable attribution path.

## Physical-Line Counting

The same native run attributes 17.72 seconds to the repository admission test
and 34.89 seconds to the compact/full report comparison. Together they scan the
worktree three times. Source inspection finds `_physical_lines` visiting every
payload byte in Python. Its exclusive share remains unmeasured; it is a
concrete interpreter-cost candidate, not proof of the entire timeout's cause.
Post-merge run `34161648186` reached the unchanged 2,400-second bound before
completion. That failure cannot admit a release or supply successful timings.

Let `c(p, s)` count non-overlapping occurrences of byte string `s` in `p`.
Every CR and LF contributes one terminator except adjacent CRLF, which denotes
one terminator rather than two. CRLF cannot overlap itself. Therefore:

```text
terminators(p) = c(p, CR) + c(p, LF) - c(p, CRLF)
lines(empty) = 0
lines(nonempty p) = terminators(p) + indicator(last(p) not in {CR, LF})
```

Replace only the interpreted loop with three built-in `bytes.count` calls.
This keeps linear time and constant auxiliary storage; it introduces neither
content caching nor a second traversal authority. Splitting the entire payload
would allocate line objects and is unnecessary. All other byte values remain
ordinary content, including Unicode-looking encodings and binary controls.

The independent oracle normalizes CRLF, then standalone CR, and splits LF on
bounded fixtures. Enumerate lengths zero through six over `{CR, LF, ordinary}`
and retain explicit binary, mixed-terminator and unterminated cases. Empty,
single CR, single LF, CRLF and ordinary-tail cases independently falsify each
operand. Existing real-worktree and candidate threshold tests remain intact.

Writer readiness: the source owner changes implementation, not the metric;
the test owner adds exact-result witnesses through public `metric_values`;
the native repository/coverage gates validate the dependent candidate report.
No new dependency or interface is needed. Revert or revise on any counterexample
or changed metric grammar. Measure native elapsed cost before claiming a
speedup; equivalence alone does not establish its magnitude or eliminate runner
variance, distributed fixture cost or other remaining B3 work.

## Measured Fixture And Output Cost

Run `34162974428`, reporting-only source `8729001`, completed 5,822 tests with
two skips and 84.81% coverage. Its 17,472 native phase rows attribute 985.40
seconds to setup, 1,286.13 to calls and 40.24 to teardown. Values are rounded
to hundredths; their sum is not an exact partition of the monotonic test loop.
The session reported 2,361.68 seconds. Provider-step and log timestamps use a
different clock and must not be subtracted from those monotonic markers.

The `test_current_production_evidence.py` cohort spent 463.20 seconds preparing
the same fixed-time valid evidence and 1.90 seconds executing test bodies.
Prepare that cohort's `ObservationInput` seed once per module, then deep-copy
it for every function-scoped `observed` fixture. Keep the shared factory and
all real admission calls, substitutions and assertions unchanged. The selected
seed contains value dataclasses, tuples, bytes and scalar values; it retains no
signing key, live authority, database, mock or cleanup-dependent resource.

```text
seed = real_factory(fixed inputs), once per module
input_i = deepcopy(seed), once per test case
value(input_i) = value(seed)
mutation(input_i) does not mutate seed or input_j
admission_under_test executes independently for every case
```

A shallow shared fixture is cheaper but creates an unnecessary aliasing risk.
Caching the shared factory would also affect unrelated callers and constructor
tests. Neither is selected. Native adversarial fixture tests alter the copied
candidate, workflow binding and provider value independently, verify the seed
is unchanged, and admit a fresh copy. Revisit on noncopyable or resource-bearing
fields, live-time inputs or a test that expects construction under monkeypatch.
This is not a database fixture change or an oracle bypass.

One overflow-test parameter generated an 8,388,722-byte node ID. Rendering it
in all three phases dominates the 29,224,961-byte native log. Assign explicit
`empty` and `over-limit` IDs; preserve the same zero-byte and limit-plus-one-byte
payloads, ordering, cardinality and rejection oracle. No current binding selects
the old parameter IDs. Keep complete duration rows, bounded capture and the
underlying overflow check rather than filtering evidence or raising limits.
