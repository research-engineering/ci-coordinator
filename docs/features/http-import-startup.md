# HTTP Import Startup

Status: implementation design

## Decision

Make `ci_coordinator.api.http` an inert namespace. Import the application
factory, dependency records and authenticator from their defining modules.
Keep the existing application composition root, runtime wiring and HTTP
contracts. This is an explicit internal Python import-path change, not a
business-policy or wire-protocol change.

The [API HTTP specification](../architecture/modules/api-http.md) owns the
namespace contract. [The plan](http-import-startup-implementation-plan.md)
owns implementation order and acceptance. This work advances ROADMAP B3;
it does not close the complete CI-performance program.

## Evidence And Scope

On source `a4aa8eba193ec819f4f345743d50bc062f735e9e`, the namespace imports
`app`, dependency records and the plan authenticator. Python initializes parent
packages before importing a leaf, so importing `api.http.body_limits` also
loads application/router composition that the middleware does not require.
The canonical middleware and actual route-policy witnesses remain unchanged.

HA01's 5000ms whole-process baseline budget failed in several native runs,
including post-merge run `34477166147` attempt 1, despite a passing test report.
The baseline correctly remained invalid because the process did not complete.
An unchanged-source retry passed. These observations establish unstable
whole-process qualification, not its exclusive cause or a latency defect in
the content-length parser. Imports, pytest preparation and teardown all consume
that budget. The proposed repair removes independently demonstrated import
work; native evidence must determine whether further preparation work is needed.

## Protected Contract

Let `D(s)` be the defining module of a previously re-exported symbol `s`.
For every migrated internal consumer, replace only its import edge:

```text
before: consumer -> api.http -> D(s).s
after:  consumer -> D(s).s

same defining module + same symbol
  => same constructor/function identity within one interpreter

Import(api.http) does not import app or routers
Import(body_limits) does not import app or routers
ExplicitImport(app) still imports the one application composition root
```

Preserve HTTP paths, schemas, response/error bodies, authorization, body limits,
resource admission, database/provider behavior and all existing test assertions.
Remove no test and change no mutation target, native identity, budget, timeout,
coverage threshold, bytecode policy or exit/report predicate. An internal
consumer that still relies on a removed symbol must fail type/import validation,
not receive a dynamic fallback with a different lifecycle.

This repository's service clients use HTTP contracts. Compatibility for an
unregistered external Python consumer of the removed facade is not claimed.
Discovery of such an admitted consumer invalidates this atomic migration and
requires its explicit migration or a separately justified compatibility design.

## Alternatives

| Candidate                                              | Decision and consequence                                                                                                     |
|--------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| Keep imports and retry failed jobs                     | Retains unnecessary work and recurring qualification cost; not a repair.                                                     |
| Increase the 5000ms budget now                         | Does not remove work or identify its cost; defer until measurements justify a separate budget change.                        |
| Dynamic lazy re-export facade                          | Preserves old spelling, but adds dynamic lookup, typing and caching behavior without a current external-consumer obligation. |
| Direct defining-module imports                         | Selected: no new runtime mechanism, fewer eager edges, explicit existing owners. Requires one atomic consumer migration.     |
| Extract route constants or rewrite all package facades | Broader than the demonstrated namespace edge; defer unless remaining measurements justify it.                                |

The preference is scoped to the current internal consumers and unchanged
service contracts. It is not a universal claim that package facades are wrong.
Rollback is a source revert and normal release; there is no data migration.

## Proof And Revision

An isolated Python process, from an empty working directory and with the exact
source root, imports each admitted target. It records loaded modules and
monotonic elapsed time. Namespace and middleware cases reject application or
router imports; an explicit application import is the positive control.
Thus a warm pytest process cannot conceal a previously initialized package.
The timing is diagnostic, not an asserted machine-independent deadline.

Static checks preserve import direction, type correctness, exact changed-path
routes and documentation reachability. Native GitHub checks retain complete
test collection, runtime/HTTP tests, coverage and all HTTP mutation witnesses.
The independent reviewer follows the current repository reviewer policy.
Local static evidence does not substitute for those native checks.

Falsifiers: a retained consumer fails to import; a symbol changes its defining
owner; a leaf loads application composition; HTTP behavior changes; an original
mutation no longer has a valid baseline and killed result. Continued watchdog
failures reopen preparation/budget diagnosis without weakening the oracle.
An additional supported Python consumer or a required initializer side effect
reopens compatibility. New measurements may justify another optimization;
they do not erase failed earlier attempts.

## Decimal Witness Follow-Up

Run34729862288 reopened the watchdog condition: HA01's passing test report did
not complete its process within 5000ms. The original namespace repair remains;
the witness module still imported plan/operator routers for unrelated route
cases, reintroducing application preparation into a middleware-only oracle.

Move that one decimal witness to an inert leaf test module with an explicit
bounded test policy. Keep its adversarial5000-digit input, real middleware,
no-body-read/no-downstream predicates and exact413 payload. Other tests retain
the actual production route policies. The HA01 target, patch, witness ID,
5000ms budget, kill semantics and suite population are unchanged; only its
test-file coordinate changes, with Proofkit and native collection updated.

This is less machinery than another runner, lazy facade or timeout increase.
The changed native identity is an explicit successor to the original no-move
namespace batch, not a claim that the earlier batch already made this change.
Accept only a fresh passing baseline and killed mutant; removed imports alone
do not prove a machine-independent completion time. If the process still times
out, diagnose the remaining preparation/teardown rather than weaken the gate.
