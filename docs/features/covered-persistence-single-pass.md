# Covered Persistence Single Pass

Owner: repository verification, `REQ-CI-PROOFKIT-006/008/009`.
Scope: the duplicate-execution portion of
[B3](evidence-led-operational-closure-implementation-plan.md#5-b3-measured-cost-reduction).
The [implementation plan](covered-persistence-single-pass-implementation-plan.md)
owns delivery. This design changes CI orchestration, not product behavior.

## Decision

Run the existing complete Python coverage command once in the PostgreSQL job.
Retain `persistence-test` in the aggregate quality plan and as a separately
callable targeted diagnostic and selective witness. Do not remove application or
persistence behavior witnesses, database fixtures, coverage sources, thresholds
or mutation cohorts. Update CI-topology assertions for the changed orchestration.

Currently the workflow runs `persistence-test` and then `coverage`. Both use the
same job's interpreter, dependency lock, provider image and configured environment,
without step-local overrides. The coverage command already
executes the whole backend test tree, including the same real PostgreSQL tests.
Removing the repeated invocation needs no new runner, cache or coverage merger.

## Equivalence Boundary

Let `P` be the native pytest selection for
`-m persistence tests/integration/persistence`. Let `C` be its selection for
`tests ../scripts/tests ../scripts/conformance/audit_persistence_byte_contract_test.py`
with branch coverage enabled. At the admitted configuration:

```text
tests/integration/persistence is a subtree of tests
C has no marker, keyword or ignore filter
P is a subset of C
Union(P, C) = C
```

The unchanged autouse persistence fixture creates a real PostgreSQL 18.6
container, migrates and cleans each test; collection is not conditional on an
earlier standalone execution. Coverage instrumentation adds observation, not a
different persistence assertion. The current
[pytest selection contract](https://docs.pytest.org/en/stable/how-to/usage.html)
and [pytest-cov options](https://pytest-cov.readthedocs.io/en/latest/config.html)
own those distinctions.

This proves selection containment under the stated configuration, not identical
timing or identical stochastic outcomes across two executions. The first run
was not an owner-required repetition, stress or non-instrumented benchmark.
The GitHub job already required the complete instrumented run to pass. A
failure still fails the job, and unchanged coverage evaluation still applies.

The separate persistence phase previously reported its failures before starting
coverage. That early phase result is intentionally removed: a persistence
failure now makes the complete covered run fail, potentially after its remaining
tests finish. Keep the full diagnostic suite and existing 1,200-second process
bound; do not silently add `--exitfirst`. This decision reduces repeated work
on successful runs, not every failure's feedback latency. Reopen it if a hard
early-failure budget or measured failure frequency justifies a different split.

The aggregate quality plan is a distinct envelope: its targeted persistence
command admits only `PATH`, while coverage also admits CI and range inputs.
Selection containment under one envelope does not prove environment-invariant
behavior across those two. Keep that existing aggregate observation unchanged;
unifying its environments or proving it obsolete is outside this decision.

The source oracle closes the finite project-authored execution envelope: exact
run-step inventory, no per-step/job environment or shell override, the three
admitted workflow environment keys, unfiltered pytest discovery configuration
and the 55-minute job limit. Adding a wrapped invocation, a selection filter or
a larger deadline must fail even when the coverage command string is unchanged.
This uses a closed inventory instead of implementing a general shell analyzer.
It does not authenticate arbitrary runner images, action internals or future
collection plugins; changed trust roots and collection hooks reopen admission.

| Protected observation                      | Preservation mechanism                                                      |
|--------------------------------------------|-----------------------------------------------------------------------------|
| PostgreSQL behavior and migration oracles  | Same test bodies, fixtures, provider image and complete coverage selection. |
| Unit and tooling checks                    | Same complete coverage arguments and environment projection.                |
| Coverage adequacy controls                 | Same branch instrumentation, denominator and aggregate/risk thresholds.     |
| Targeted diagnostics and selective routing | Existing command identities and implementations remain available.           |
| Mutation and required-check evidence       | Existing separate jobs and stable final gate remain required.               |

## Alternatives And Revision Conditions

Keeping both GitHub executions preserves duplication without a distinct admitted
oracle under the current shared job configuration. Keeping both aggregate
commands preserves the different existing environment observations. Splitting
and combining coverage would introduce artifact freshness,
path remapping and denominator obligations without being necessary to remove
this duplicate. Sharding, database-fixture optimization and caching remain
separate measured decisions.

Reopen selection containment if collection hooks, environment-dependent skips,
markers, pytest configuration, discovery roots or coverage invocation change.
Reopen the orchestration decision if an owner admits an independent
non-instrumented, repeated or differently configured PostgreSQL observation.
Test count alone is not equivalence proof. Native exact-head CI must still pass;
before/after job and step durations are observations, not CPU savings or a
statistical speedup guarantee.

## Writer Readiness

| Owner                 | Delta and derived surfaces                                                                                  | Falsifier and acceptance                                                                                                                                                                                                            |
|-----------------------|-------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Workflow verification | Remove the redundant PostgreSQL workflow step; retain aggregate and targeted command definitions.           | Existing workflow tests require the registered coverage argv exactly once, after setup, without conditional execution, step-local environment changes or ignored failure; the aggregate retains both environment-specific commands. |
| Native selection      | Preserve both Python witness implementations.                                                               | The registered coverage command flows through `PythonWitness.run()` to exact pytest arguments; command-mode or dispatcher substitutions, missing roots, filtering and missing branch coverage fail the composed witness.            |
| Routing and discovery | Bind this design/plan and update the exact workflow inventory digest in canonical and packaged projections. | Existing JSON, route, documentation and source-inventory gates; native GitHub Full Check.                                                                                                                                           |

Local validation is static-only. No runtime, deployment, provider authority,
production capacity or complete B3 claim follows from this batch.
