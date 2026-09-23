# Schema-Driven API Testing Plan

Owner: repository test portfolio
Base: `7911c7dcc6ddd1ed1c5c0bc18a8f7fa6817a32b2`
The [design](schema-driven-api-testing.md) owns behavior and non-claims.

## Sequence And Write Ownership

1. Review design premises against the exact app, native reporter and pinned
   library. Freeze the three-operation population and protected current gates.
2. Add Schemathesis 4.27.4 to backend dev dependencies, regenerate uv and exported
   development locks, and install the locked environment. Check production
   dependency exclusion; never install the upstream development extras.
3. Add a cohesive test-only API contract package: synthetic dependency fixtures,
   raw schema and explicit transport binding, generation/check profiles and
   independent qualification tests. Keep domain decisions in existing owners.
4. Add a bounded developer/CI campaign command using the existing process
   runner. The fast native cohort stays in normal collection; the deeper profile
   is explicitly requested and cannot silently expand effects or target a URL.
   One reusable/manual campaign workflow supports short isolated checks and
   the optional Full Check deep call without weakening full-portfolio admission.
5. Add diagnostics/artifact handling outside shard receipts and a documented
   local workflow. Bind new files to existing HTTP/quality requirements through
   Proofkit and route the new design, plan and how-to from current indexes.
6. Run local permitted lint/type/schema/route checks, one independent batch
   review under AGENTS.md, and exact-branch GitHub qualification. Apply grouped
   confirmed corrections, rerunning only invalidated evidence.
   Native qualification must also close the observed upstream lifespan resource
   leak without suppressing warnings and the malformed-encoding HTTP contract
   defect without changing valid requests or unrelated HTTP exception behavior.
7. Before opening the PR, require the current authorized pre-publication gates,
   one unpublished merge-unit commit over the admitted base, unchanged prior
   design/plan files, and admitted title/body metadata. Open without merging.

## Acceptance Matrix

| Scope                 | Positive witness                                                                                       | Falsifier / refusal                                                          |
|-----------------------|--------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| Dependency            | Locked Python3.13.15 install and normal suite                                                          | Incompatible resolution, production dependency leakage or warning regression |
| Population            | Exact three eager operations and native report admission                                               | Missing/extra operation, lazy subtest/foreign node or duplicate phase        |
| Transport             | Main and auth-probe ASGI requests stay in-process                                                      | Any real socket/provider request fails the test                              |
| Auth oracle           | Missing and valid-format invalid credentials have typed refusal and no effects                         | Wrong auth-only body/header or protected port call passes                    |
| Lifecycle             | Startup/shutdown counted; per-example mutable state reset                                              | Startup at collection, stale cookies/counters, leaked lifespan               |
| Positive reachability | Each operation reaches its intended port and meaningful outcome                                        | Only authentication refusals, empty unrelated data or skipped generator      |
| Input                 | Generated modes plus known boundary/invalid cases                                                      | Invalid request reaches effect port or loses required scope                  |
| Response              | Status/media/schema plus no-store and exact semantic assertions                                        | Wrong field/type/status, unintended sensitive output                         |
| Context               | Valid and invalid embedded configuration distinguished                                                 | Schema-valid envelope incorrectly forced to 2xx or all 422 accepted globally |
| Oracle                | Deliberately wrong response rejected for the intended reason                                           | Setup/collection failure masquerades as a detected mutation                  |
| Native CI             | Exact nodes/phases, coverage and final gate                                                            | Shard artifacts gain foreign files or failures become passing receipts       |
| Budgets               | Native timing comparison meets 10s collection/60s normal execution increment; deep bounded to 300s     | Timeout, excess increment, incomplete population or unbounded amplification  |
| CPU summary           | Zero, user-only, system-only and combined counter increments                                           | Negative cancellation residue or a discarded counter component               |
| Reproduction          | Exact synthetic case fails the same oracle and passes with restored response; supported fuzz shrinking | Reliance on changing live state, credentials or seed alone                   |

Each changed owner must have its inputs, outputs, derived consumers and
independent falsifiers inspected before mutation. Metadata-only schema
generation is not behavioral evidence. Tests execute through native GitHub
routes under the current organization rule; developer commands are delivered
without implying permission for agents to execute them locally.

## Closure And Limits

Publish measured native results rather than a promised latency. Retain original
test and coverage gates; do not fix fuzz findings by weakening production
requirements. Reject invalid findings with explicit owner evidence. Additional
review is limited to a material unresolved finding or an uncovered scope.

The capability-disposition matrix closes the selection decision, not every
library feature: meaningful stateful durable scenarios remain with their real
PostgreSQL proof owner and are not simulated to inflate coverage. Full API
expansion requires its own safe fixtures and non-vacuous evidence. No deployment,
database mutation, autonomous fuzzing, or pilot target workflow change belongs to this PR.
