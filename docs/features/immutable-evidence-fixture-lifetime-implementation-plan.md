# Immutable Evidence Fixture Lifetime Implementation Plan

Status: implementation; native validation pending

The [design](immutable-evidence-fixture-lifetime.md) owns scope, alternatives,
isolation and reconsideration conditions.

## Ordered Work

1. Freeze the complete native baseline, its source, runner/workflow and timing
   phases. Retain earlier partial timings only as discovery evidence.
2. Add the narrow `ProductionCutoverFixture.isolated_copy()` method, preserving
   only its stateless key by identity. Re-admit the signed grant and map it to
   a fresh grant in `deepcopy`'s memo; never copy opaque registration state.
   Keep the factory unchanged.
3. Add module-local baseline fixtures in the four named modules. Existing
   function fixtures copy their baseline; mocks, stores and worker execution
   remain per-case. Use ordinary `deepcopy` for the key-free relation fixture.
4. Add three independent copy-corruption witnesses and preserve all existing
   test bodies, parameters and foreign-key/successor creation calls.
5. Bind both new documents through existing Proofkit adoption routes and the
   roadmap. Re-run static lint, type, route, documentation and AST checks.
6. Freeze the whole delta for the independent reviewer selected by `AGENTS.md`.
   Native tests run only in GitHub; no local behavioral fallback is permitted.
7. Admit exact-head Full Check, compare setup/call/teardown separately, then
   squash and inspect post-merge results. Record uncertainty instead of a
   speedup claim when environments or timing completeness do not match.

## Acceptance

| Obligation                 | Required evidence                                                                                     |
|----------------------------|-------------------------------------------------------------------------------------------------------|
| Case semantics preserved   | Existing test-body/parameter AST equality; unchanged negative assertions and factory callers          |
| Copies independent         | Native staged/policy/envelope corruption cases, later fresh copy and unchanged seed                   |
| Key semantics preserved    | Explicit shared-key identity for copies; existing independent wrong-key and same-key successor cases  |
| Opaque authority preserved | Fresh verified grant and registration identities, unchanged public authority and envelope projections |
| Mutable harnesses isolated | Function fixture scope plus existing exact call-count/state/cancellation assertions                   |
| Cost actually reduced      | Complete source-bound native phase rows; no projection from elapsed time to CPU                       |
| No weaker proof            | Complete required jobs, case/skip inventory, unchanged canonical coverage floors and mutation gates   |

New documents are additions; historical designs and plans remain untouched.
The native result is required for merge, not inferred from static analysis.
