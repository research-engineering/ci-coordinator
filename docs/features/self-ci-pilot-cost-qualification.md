# Self-CI Pilot And Cost Qualification

Status: implementation candidate; no operational or omission authority.
Owner: ci-coordinator.proofkit-adoption.

## Objective

Qualify this repository as the first workflow-optimization consumer while
preserving its independent Full Check. Reuse the existing self-consumer,
configuration lifecycle, measurement contracts and runtime admission. The
companion [implementation plan](self-ci-pilot-cost-qualification-plan.md)
separates source delivery from release and live evidence.

## Evidence And Minimum Change

PR209 Full Check run `35509045239` measured 240.38 seconds of pytest phases in
`test_self_ci_generation.py`. Eight independent assertions each render the
same complete source artifact set, taking approximately 22-26 seconds per
rendering test. `test_self_ci_responsibility.py` adds another unchanged render.
These are elapsed phase measurements, not CPU or whole-workflow savings.

Preparing that unchanged dataset once per pytest process and giving each test
a deep copy removes repeated setup without removing assertions. Do not cache
production generation or use a stale artifact committed by the implementation
as its own oracle. Tests that modify generator inputs continue to invoke the
real affected operation. A fresh pytest process computes a fresh baseline.

```text
ReusableFixture := SameOwnedInputs AND SamePathModeInventory
                   AND NoSharedMutableTestState

SameRequiredAssertions AND ReusableFixture
  => SetupReuseDoesNotRemoveTheRequiredAssertions
```

This implication is limited to fixture preparation. Native CI must establish
the resulting outcomes and coverage; measured timing must establish benefit.
The fixture rechecks the generator's current-input predicate and its existing
Go source admission before each copy. Go content can change build constraints
without changing path/mode identity, so that content-sensitive predicate must
not be inferred from the inventory alone. Independent falsifiers mutate a
returned copy, substitute an owner read and add a same-path Go build constraint;
neither shared state nor stale admission may conceal those changes.

The alternative of adding shards would shorten one queue but retain duplicate
CPU work and add installations. Removing cases or replacing runtime decoders
with mocks would weaken the proof. A general cache/fixture framework adds no
needed capability. Keep one test-owned fixture in the existing conftest.

## Pilot Boundaries

Bind the actual repository, App installation, endpoint and public signing key.
Register a reviewed non-enforcing policy and retain the independent native gate.
Manual coordinated dispatch demonstrates fallback only; it does not prove a
service-selected plan. Production omission still requires exact external
admission, active generation, independent evidence and kill-switch qualification.

Use existing GitHub and test timing evidence to separate queue, preparation,
execution, combine and coordinator overhead. Compare matched event, source,
runner and cache scopes. Runner occupancy is not CPU usage. A source change
cannot authorize its own weakened validation. Unknown or unavailable planning
preserves FullCI, including a missing reusable-workflow dependency.

The two currently omittable short utility jobs cannot explain a material
reduction of the 18-minute Compose critical path. This repair targets aggregate
test work; broader wall-time reduction requires separate measured causes.
Do not weaken Compose rebuild, minimum/current compatibility, browser,
database or mutation witnesses merely to meet a five-minute target.

## Revision Conditions

Reconsider fixture scope when source-mutating cases join this cohort, pytest
worker isolation changes, a generator input is missing from its owner inventory,
or copy/setup cost erases the measured benefit. Each test still receives fresh
mutable containers; no fixture may supply a precomputed passing outcome.

Live deployment, real plan exchange, archive reconciliation, advisory execution
and notification delivery require their separate receipts. An unavailable VPN
blocks those observations, not source implementation or native qualification.
