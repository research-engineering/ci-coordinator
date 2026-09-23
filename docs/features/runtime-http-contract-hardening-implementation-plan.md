# Runtime and HTTP Contract Hardening Implementation Plan

Status: implemented

Date: 2026-08-21

Design authority:
[Runtime and HTTP Contract Hardening](runtime-http-contract-hardening.md)

Owner requirements: `REQ-CI-CORE-002`, `REQ-CI-RUNTIME-008`,
`REQ-CI-RUNTIME-017`, `REQ-CI-RUNTIME-021`

## 1. Objective

Restore exact agreement between runtime behavior, machine requirements, module
specifications, and enforced dependency direction without introducing a new
framework or changing domain business behavior.

## 2. Implementation Order

1. Make initial reconciliation publish an exact first-round outcome. Raise one
   redacted startup error on failure, scheduler failure, stop-before-success,
   or timeout; retain later degrade-and-recover behavior.
2. Prove lifespan rejection and resource drain with failure and timeout
   witnesses that never enter the serving body. Give the shared cleanup
   finalizer one total owner deadline, a strict half-budget for reconciliation
   drain, an explicit cleanup-pending terminal path, and prove concurrent callers
   cannot cancel, reactivate, or extend it.
3. Replace the cross-loop production registration engine with a temporary
   registration engine disposed in its creation loop; allocate the fresh
   runtime engine and provider clients only afterward.
4. Compute browser expiry from absolute token expiry and durable time. Project
   the owner-defined OAuth transaction lifetime through `LoginStart` instead of
   repeating it in the router.
5. Replace exact browser service identity with narrow structural input ports.
   Move every non-transport use-case protocol out of HTTP to its capability
   owner.
6. Move workbench adapter ports into `workbench_read_models.ports` and make the
   readiness probe an explicit required dependency.
7. Move capability-specific body-limit policies next to their routers. Keep
   generic bounded-body mechanics in `body_limits.py` and route collection in
   the app composition root.
8. Replace path-specific unexpected-error conversion with one redacted `500`
   handler. Remove the redundant broad catch from plan authentication. Retain
   one local browser-callback catch solely because every callback outcome must
   clear the transaction cookie; map its unexpected outcome to a route-shaped,
   redacted `500` and preserve cancellation propagation. Generate the operator
   UI OpenAPI through the same HTTP composition root so every operation exposes
   the runtime error algebra without a duplicate router graph.
9. Split the ownership profile into exact runtime-settings, process-environment,
   GitHub API, and Actions JWKS responsibilities. Add import-policy negative
   probes for direct concrete imports, restricted service-module acquisition,
   exact port imports, literal `getattr`, and bare sensitive-root escapes from
   HTTP, an exact inventory of HTTP-owned request protocols, and closed-world
   contextual-rule coverage.
10. Sample background health after asynchronous readiness probes. Restrict
    disabled mode to operability routes and prove that connected route policy is
    not loaded during disabled composition.
11. Align current module specifications and `REQ-CI-RUNTIME-008`,
    `REQ-CI-RUNTIME-017`, and `REQ-CI-RUNTIME-021` with the exact lifecycle,
    temporal, and error algebra. Bind every new proof-like owner and witness in
    Proofkit.
12. Run local static, requirement, documentation, ownership, import-boundary,
    lint, and type witnesses. Route behavioral, persistence, and complete
    suites to GitHub CI under repository policy.
13. Submit the frozen diff to one independent adversarial review and one
    closeout review if the first review changes material behavior.
14. Replace advisory-affected Python and pnpm resolutions with the smallest
    upstream-fixed releases, remove obsolete vulnerable overrides, regenerate
    every lock projection, and require both ecosystem audits to report no known
    vulnerabilities.
15. Retain each route's body-lane permit until downstream releases the replayed
    body, and prove that request `b_i + 1` cannot be buffered while `b_i` bodies
    remain retained.
16. Classify timeout as route-owned only when the middleware's exact deadline is
    expired; rethrow every downstream `TimeoutError` into the generic error
    algebra.
17. Close config outcome construction over its finite state sets and make both
    HTTP projections reject any adapter value outside those sets.
18. Replace last-write alias resolution with a flow-insensitive lexical import
    projection. Imported authority provenance is monotone within a scope;
    reassignment does not erase it.
19. Route webhook idempotency contract violations through the generic `500`
    owner so runtime bytes and OpenAPI use one schema.
20. Reject acquisition of any module object that defines or re-exports a
    concrete `*Service`. Derive definition, facade-export, private-module, and
    module-object authorities from one typed service declaration. Compare that
    declaration to an independent AST inventory of every top-level concrete
    service definition and the least fixed-point closure of every transitively
    reachable package-facade re-export, including each public alias name. Reject
    aliases because the current owner declaration admits only same-name facade
    routes. Compare every non-identity route even when its target authority is
    also a concrete definition. Keep exact port, model, and DTO symbol imports
    admissible.
21. Resolve direct attribute and literal `getattr` references from lexical
    import provenance. Project the closed dynamic-import token set from imported
    names, literal member subscripts, and literal `getattr` members. Treat a bare
    sensitive import root used as a value as a conservative escape of every
    configured descendant authority.
22. Keep the gate syntactic. Use the pinned CPython `symtable` for executable
    function and class local binders, and AST declarations for generic
    parameters and comprehension targets. Fail closed on an unmapped executable
    scope. Pre-index each import under its module, function-local, nonlocal, or
    class-local CPython binding owner so declared-global and nonlocal provenance
    reaches exactly the scopes that resolve that owner. Evaluate generic function
    defaults in the defining environment; let an explicit `global` override a
    same-name generic mask in the current executable body; propagate generic
    class masks into nested classes, callables, and comprehensions; and preserve
    ordinary class-local exclusion from those closures. Project exact
    `from` authorities without synthesizing their parent module. In postponed
    annotations, project direct expressions and lambda defaults while treating
    lambda bodies as latent. Make no control-flow, exception-dispatch, or
    arbitrary runtime object-flow claim.
23. Treat every OIDC rejection reason outside the admitted finite sets as an
    unexpected internal defect, never typed dependency unavailability.

## 3. Change Map

| Concern                          | Primary owner                                                                  | Witness class                                                                                                                                                                                                    |
|----------------------------------|--------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Initial convergence admission    | `runtime/reconciliation_service.py`                                            | scheduler failure, timeout, lifespan non-entry, cleanup order                                                                                                                                                    |
| Loop-safe authority registration | `runtime/composition.py`                                                       | distinct-engine identity, disposal order, provider allocation order                                                                                                                                              |
| Browser temporal bounds          | `browser_identity/service.py`                                                  | delayed durable clock, expired token, maximum duration                                                                                                                                                           |
| Input-port ownership             | capability `ports.py` or application module                                    | type checking, import-boundary negative probes, structural fake                                                                                                                                                  |
| HTTP error algebra               | `api/http/errors.py` and routers                                               | unexpected `500`, typed unavailable `503`, redaction                                                                                                                                                             |
| Body limits                      | body-bearing routers plus generic middleware                                   | exact limit, invalid length, timeout, middleware ordering                                                                                                                                                        |
| Readiness composition            | `runtime/readiness.py`                                                         | explicit probe, timeout, exception redaction                                                                                                                                                                     |
| Shutdown ownership               | `runtime/resources.py`                                                         | one shared deadline, caller cancellation, concurrent timeout, retry partition                                                                                                                                    |
| Disabled route surface           | `runtime/application.py`                                                       | exact route inventory, no selected-capability policy load                                                                                                                                                        |
| Ownership governance             | machine profile and import policy                                              | profile admission, negative probes, module ownership gate                                                                                                                                                        |
| Dependency integrity             | Python and pnpm manifests plus lock projections                                | frozen install and zero-advisory audits                                                                                                                                                                          |
| Retained request memory          | `api/http/body_limits.py`                                                      | `b_i + 1` blocked-request concurrency witness                                                                                                                                                                    |
| Timeout ownership                | `api/http/body_limits.py`                                                      | owned deadline versus downstream timeout witness                                                                                                                                                                 |
| Closed config algebra            | app outcome models and config router                                           | invalid construction and forged-adapter witnesses                                                                                                                                                                |
| Static import authority          | `scripts/python_import_authority_scanner.py` behind the boundary-engine facade | exact symbol imports, complete concrete-service inventory, restricted module acquisition, direct attributes, literal reflection, bare-root escape, CPython lexical binders, and mutation-sensitive shadow probes |
| Webhook `500` schema             | GitHub webhook router                                                          | contract-violation runtime/OpenAPI equality witness                                                                                                                                                              |
| OIDC rejection classification    | plan authenticator                                                             | admitted reason classes and unknown-reason internal-error witness                                                                                                                                                |

## 4. Acceptance Predicate

```text
Accept iff
  initial success within bound is necessary for lifespan yield
  and initial failure and timeout reject startup and initiate drain
  and later reconciliation failure still degrades and can recover
  and bootstrapEngine != runtimeEngine
  and bootstrapEngine is disposed before runtimeEngine allocation
  and production registration precedes every provider allocation
  and sessionExpiry <= providerTokenExpiry
  and sessionExpiry <= durableNow + configuredMaximum
  and the guarded database insertion remains authoritative
  and browser transports accept structural ports rather than exact services
  and HTTP owns no application input protocol
  and workbench ports are implementation-independent
  and readiness has exactly one explicit probe construction mode
  and every unexpected defect is a redacted 500
  and an unknown closed-algebra outcome is an unexpected defect, not unavailable
  and every generated OpenAPI operation declares its exact 500 schema
  and the OpenAPI projection uses the serving HTTP composition root
  and every declared unavailable outcome retains its typed status
  and body-limit behavior is byte-exact
  and retained request bodies for route i never exceed b_i
  and only the middleware's expired deadline selects its timeout response
  and config outcome construction and projection are closed over one finite algebra
  and webhook internal defects use the generic ErrorBody
  and ownership paths cover their actual authorities
  and direct HTTP-to-service implementation imports fail mechanically
  and every concrete service definition and every transitive facade route is inventoried
  and a facade target that is also a concrete definition does not suppress another root's route
  and every facade public name equals its owner-admitted service name
  and restricted HTTP service module objects fail mechanically
  and exact HTTP port and data imports remain admissible
  and exact allowed from-imports do not synthesize a forbidden parent
  and imported authority provenance is monotone within each lexical scope
  and CPython local binders and AST-declared binders mask their owned scopes
  and declared-global and nonlocal imports retain their exact CPython binding owner
  and generic defaults retain defining-scope authority provenance
  and explicit globals dominate same-name generic masks only in their declaring body
  and generic class parameter masks propagate into nested lexical scopes
  and postponed annotation lambdas distinguish defaults from latent bodies
  and every admitted static dynamic-import token form is projected
  and bare sensitive import roots conservatively expose configured descendants
  and unknown OIDC rejection reasons remain unexpected defects
  and every Python source has a contextual import rule
  and HTTP declares only request-shaped transport protocols
  and all cleanup callers share one owner task and one total deadline
  and reconciliation drain receives a strict sub-budget smaller than that deadline
  and stop-before-initial-publication cannot admit startup
  and concurrent close cannot transition a stopped startup to active
  and unsettled deadline cancellation is represented as cleanup pending
  and no new resource close starts after the deadline fence
  and cleanup diagnostics retain no provider exception
  and readiness samples background state after asynchronous probes
  and disabled mode mounts only health, readiness, and metrics
  and disabled composition loads no connected-capability policy
  and Python and pnpm lock projections contain no known vulnerabilities
  and requirements, bindings, documentation, lint, types, and tests pass
```

## 5. Review Questions

1. Can any route become reachable before successful initial convergence?
2. Can any engine, connection, task, lock, or event cross an unproved event-loop
   boundary?
3. Is provider allocation still strictly later than production registration?
4. Is every temporal comparison made in one explicit time domain, or converted
   through an absolute instant before domains interact?
5. Does each `503` have a typed proof of temporary dependency unavailability?
6. Does any transport file define a port whose lifecycle is controlled by a
   capability owner?
7. Did moving a policy change middleware order, response bytes, cancellation,
   or OpenAPI truth?
8. Does any new abstraction remove a demonstrated dependency or duplication,
   or is it unearned?
9. Can one shutdown caller cancel, restart, extend, or misclassify the shared
   finalizer owned by another caller?
10. Can disabled composition load or publish a capability that is absent from
    its exact route inventory?
11. Can stop or close win before startup publication while a later continuation
    still yields the ASGI lifespan?
12. Can the background drain consume the entire outer shutdown budget, or can a
    cancellation-suppressing close hide an unsettled cleanup child?
13. Can request `b_i + 1` buffer a body while `b_i` downstream calls retain their
    replayed bodies?
14. Does a timeout response prove that the middleware's own deadline expired?
15. Can any value outside a declared finite result algebra reach a typed
    availability response?
16. Can HTTP obtain a restricted service module object and use it to reach a
    concrete implementation without importing that implementation explicitly?
17. Can a sensitive import root escape through a bare value while the gate
    reports only an admitted child authority?
18. Does every typed authentication `503` have an admitted finite reason that
    proves dependency unavailability?
19. Does any proof statement infer runtime path reachability, evaluation order,
    or exception semantics from the deliberately flow-insensitive import gate?
20. Can the restricted-module rule reject an exact port, model, or DTO import
    that does not expose a module object?
21. Does each lexical-binder and static reflective-token mechanism have an
    isolated oracle that fails when only that mechanism is removed?
22. Can a facade alias create a public concrete-service route absent from the
    owner declaration without making the inventory oracle fail?
23. Can an exact allowed `from` import be rejected solely because the scanner
    synthesized its forbidden parent module?
24. Do generic defaults, generic class closures, and postponed annotation
    lambdas each follow their distinct CPython evaluation scope?
25. Can a service traverse two or more package facades without every public
    route appearing in the independent inventory?
26. Can an ordinary class-local import leak through a nested class into a
    callable even though CPython resolves that name from the non-class parent?
27. Can a facade route disappear solely because its target authority is also a
    concrete definition?
28. Can a declared-global or nonlocal import be lost, or can a class-declared
    global incorrectly override an enclosing function binding in a nested scope?
29. Can a same-name generic parameter hide an explicit global in its executable
    body, or can that declaration erase the generic class mask for descendants?

## 6. Rollback

The change is one contract-hardening unit. If rollback is required, revert the
whole unit; retaining generic `500` behavior while restoring cross-loop engine
reuse or permissive startup would create an internally inconsistent runtime.
No schema or data migration is introduced.
