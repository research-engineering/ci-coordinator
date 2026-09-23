# Runtime and HTTP Contract Hardening

Status: implemented

Date: 2026-08-21

Owner requirements: `REQ-CI-RUNTIME-008`, `REQ-CI-RUNTIME-017`,
`REQ-CI-RUNTIME-021`

## 1. Decision

Harden the connected runtime at four authority boundaries:

1. ASGI startup succeeds only after the initial reconciliation round succeeds
   within its configured bound.
2. Production-admission registration and the serving runtime use distinct
   PostgreSQL engines. The registration engine is created, used, and disposed
   inside one temporary event loop before any provider client is allocated.
3. Browser-session expiry is the minimum of the absolute provider-token expiry
   and the durable database-time session bound.
4. HTTP adapters depend on capability-owned input ports, retain only
   transport-specific policy, and map unexpected defects to one redacted
   `500` response. Typed unavailable outcomes remain `503`.

The correction also gives shutdown one owner and one total deadline. It samples
background health after asynchronous readiness probes, makes disabled-mode
route ownership exact, and makes database-readiness injection explicit. It
separates workbench ports from their implementation, aligns the ownership
profile with actual environment and GitHub transport owners, and strengthens
the HTTP import boundary against direct service implementation imports.

## 2. Finding Adjudication

| Finding                                                                            | Verdict                                          | Reason                                                                                                                                                                                                                                                                     |
|------------------------------------------------------------------------------------|--------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Initial reconciliation failure or timeout still permits ASGI startup               | confirmed P1                                     | `Runnable` requires successful bounded convergence; readiness after serving is not a substitute for startup admission.                                                                                                                                                     |
| One pooled `AsyncEngine` crosses `asyncio.run` and Uvicorn loops                   | confirmed P1                                     | SQLAlchemy pooled async connections are loop-affine unless the engine is disposed before reuse.                                                                                                                                                                            |
| Move all engine and provider construction into lifespan                            | rejected                                         | This would violate the stronger rule that enforcing authority is registered before any provider client allocation unless the entire composition model were deferred.                                                                                                       |
| Browser session can outlive provider token                                         | confirmed P1                                     | A relative duration sampled before the durable clock read can be added to a later time and exceed the absolute token expiry.                                                                                                                                               |
| Add a new atomic store API to cap expiry                                           | rejected as unnecessary                          | The existing guarded insert is atomic at database statement time; an absolute expiry computed before insertion closes the defect without widening the port.                                                                                                                |
| HTTP module owns application input ports                                           | confirmed P2                                     | Input-port signatures change with capability behavior, not FastAPI transport behavior.                                                                                                                                                                                     |
| Browser routes require exact `BrowserIdentityService`                              | confirmed P2                                     | Exact implementation identity does not prove behavioral substitutability and rejects valid structural adapters.                                                                                                                                                            |
| `app.py` is wholly invalid because it registers all routers                        | rejected in part                                 | Central route registration is a valid composition-root responsibility. Capability-specific error and body-limit policy in that file is not.                                                                                                                                |
| Unexpected defects are reported as dependency unavailability                       | confirmed P2                                     | `503` asserts a known temporary dependency condition; an unclassified defect proves no such condition and must remain a redacted `500`.                                                                                                                                    |
| An unknown dynamic-plan outcome is reported as dependency unavailability           | confirmed P2 during independent review           | The closed result algebra admits only `Issued`, `IssuanceConflict`, and `IssuanceRejected`; an unclassified value proves a broken internal contract, not temporary unavailability.                                                                                         |
| Operator UI OpenAPI duplicates router composition                                  | confirmed P2 during implementation falsification | A second composition path omitted the generic `500` algebra and allowed the generated client contract to diverge from the serving app.                                                                                                                                     |
| Browser callback catches only service-call defects                                 | confirmed P2 during implementation falsification | Admission or unsupported-outcome defects outside the narrow catch violate the callback-specific `500` schema and leave the one-time transaction cookie uncleared.                                                                                                          |
| Plan and repository authentication contain redundant broad catches                 | confirmed P2                                     | Provider adapters already own conversion of declared transport failures; a second broad catch hides contract defects. The OAuth callback remains the explicit exception because every outcome must clear its one-time cookie.                                              |
| `RuntimeReadiness` accepts unused engine and path arguments                        | confirmed P3                                     | The constructor has two competing composition modes while connected composition already owns the exact probe.                                                                                                                                                              |
| Workbench ports live in the service implementation module                          | confirmed P3                                     | Adapters import an implementation module to implement its boundary, reversing the intended dependency vocabulary.                                                                                                                                                          |
| HTTP import enforcement is weaker than the documented boundary                     | confirmed P2, bounded                            | A mechanical rule can forbid direct service implementation imports. It cannot prove semantic orchestration ownership by itself.                                                                                                                                            |
| A mixed facade module object can expose a forbidden HTTP-to-service implementation | confirmed P2 during independent review           | HTTP needs exact ports and data symbols, not a module object that co-exposes implementations. Rejecting that module-object capability closes the bypass without interpreting runtime control flow.                                                                         |
| Ownership profile omits the actual environment and JWKS owners                     | confirmed P3                                     | The declared path sets do not cover `runtime/environment.py` or `integrations/oidc_jwks.py`.                                                                                                                                                                               |
| OAuth transaction lifetime is duplicated in service and router                     | confirmed P2                                     | Equal literals are not a shared authority and can drift independently.                                                                                                                                                                                                     |
| Shutdown has no total owner deadline                                               | confirmed P1                                     | Per-component or per-caller waits do not bound the complete drain-and-close transition and can make process termination indefinite.                                                                                                                                        |
| Concurrent shutdown callers can race over finalizer cancellation                   | confirmed P1 during implementation falsification | A caller-owned timeout can cancel shared work and make another caller misclassify finalizer cancellation as its own cancellation. The deadline must belong to the shared finalizer.                                                                                        |
| Stop during the first round can publish a successful initial outcome               | confirmed P1 during adversarial review           | An abort-aware round may return normally after stop; publication must linearize against the stop signal before success is admitted.                                                                                                                                        |
| Close during `STARTING` can be overwritten by `ACTIVE`                             | confirmed P2 during adversarial review           | An awaited background start can resume after concurrent cleanup; `STARTING -> ACTIVE` must be conditional on the state still being `STARTING`.                                                                                                                             |
| Reconciliation drain and total cleanup use the same timeout                        | confirmed P2 during adversarial review           | The outer deadline starts first, so the inner retryable timeout cannot reliably win. A strict sub-budget restores the retry partition.                                                                                                                                     |
| Owner deadline can precede cleanup-child settlement                                | confirmed P2 during adversarial review           | Cancellation is cooperative; an explicit cleanup-pending state and post-deadline close fence are required for an honest lifecycle projection.                                                                                                                              |
| Readiness samples background state before blocking probes                          | confirmed P2                                     | A round can fail while a database probe is pending, allowing a stale healthy sample to produce a green result after the failure.                                                                                                                                           |
| Disabled mode publishes the operator UI                                            | confirmed P2                                     | The runtime contract gives disabled mode only operability routes; a static shell is still a reachable route and therefore exceeds that surface.                                                                                                                            |
| Cleanup retains provider failures in the exception graph                           | confirmed P2                                     | Redacted wrapper text is insufficient when the traceback cause or context retains provider diagnostics.                                                                                                                                                                    |
| Import-time webhook policy loads connected evidence in disabled mode               | confirmed P2 during implementation falsification | Capability-owned policy may be loaded only when that capability is selected; module import must not broaden disabled-mode dependencies.                                                                                                                                    |
| New Python contexts can exist without a contextual import rule                     | confirmed P2                                     | A denylist can pass a newly added context unless rule coverage itself is closed-world and enforced.                                                                                                                                                                        |
| Locked dependency graphs contain known security advisories                         | confirmed P1 during CI closeout                  | The Python graph admitted an advisory-affected `cryptography 49.0.0`, while repository overrides retained advisory-affected `js-yaml 4.3.0` and `brace-expansion 5.0.8`. Upstream fixed releases exist, so accepting the affected graph has no compensating necessity.     |
| A body lane is released while downstream still retains the replayed body           | confirmed P1 during frozen closeout review       | If `b_i` bodies leave the lane but remain reachable downstream, another request can buffer a `(b_i + 1)`th body. The claimed `b_i * m_i` retained-memory bound is therefore false unless the lane remains held through downstream completion.                              |
| A downstream `TimeoutError` is reported as the middleware deadline                 | confirmed P2 during frozen closeout review       | Exception type does not identify the expiring timeout scope. Only the middleware's own `deadline.expired()` proves its typed timeout response; every other timeout is an unexpected defect.                                                                                |
| Unknown config outcomes fall through to typed unavailability                       | confirmed P2 during frozen closeout review       | Runtime values can escape static `Literal` checking. Closed outcome models must reject unknown states, and HTTP must still fail closed if a nonconforming adapter bypasses construction.                                                                                   |
| A path-sensitive alias gate can soundly approximate the supported Python runtime   | rejected during frozen closeout review           | Counterexamples across imports, exception handlers, `finally`, annotations, defaults, mappings, loops, context managers, and short-circuit expressions disprove the claim. A flow-insensitive syntactic policy with exact imports is both sufficient and strictly smaller. |
| Webhook idempotency contract violation emits a route-specific `500` body           | confirmed P2 during frozen closeout review       | OpenAPI declares the generic `ErrorBody`; an internal adapter-contract defect must therefore reach the generic redacted `500` owner rather than emit a second undocumented schema.                                                                                         |
| An unknown OIDC rejection reason becomes typed authentication unavailability       | confirmed P2 during control review               | An open string emitted outside the admitted rejection sets proves an internal contract defect, not dependency outage or retry safety.                                                                                                                                      |

## 3. Formal Model

Let:

- `I` mean the initial reconciliation round completed successfully;
- `B` mean it completed within the configured startup bound;
- `H` mean shutdown was requested before the initial outcome was published;
- `A` mean the ASGI lifespan yielded and routes may be served;
- `E_b` be the bootstrap database engine;
- `E_r` be the runtime database engine;
- `L(x)` be the event loop that owns async resource `x`;
- `D(x)` mean engine `x` has been disposed;
- `P` mean any GitHub provider client has been allocated;
- `R` mean production authority has been durably registered;
- `T_exp` be the absolute provider-token expiry;
- `K_db` be durable database time read before session construction;
- `M` be the configured maximum session duration; and
- `S_exp` be the session expiry.

### 3.1 Startup admission

```text
A -> I and B
not I or not B -> StartupRejected
StartupRejected -> not A
```

A `503` readiness response after `A` cannot satisfy this implication because
the protected routes are already reachable. Therefore initial failure and
timeout must raise from lifespan startup.

The first-round publication is one event-loop atomic transition:

```text
H before PublishInitial -> StartupRejected
PublishInitial(success) before H -> InitialReconciliationAdmitted
H before PublishInitial and PublishInitial(success) are mutually exclusive
```

The lifespan independently admits `STARTING -> ACTIVE` only while the lifecycle
still equals `STARTING`. Concurrent close changes that state first and therefore
prevents a stopped or already closed resource graph from being reactivated.

### 3.2 Event-loop and provider ordering

```text
E_b != E_r
Create(E_b) and Use(E_b) and D(E_b) occur in L(E_b)
Create(E_r) occurs only after D(E_b)
R occurs before P
```

It follows that no pooled connection owned by `L(E_b)` is available to
`L(E_r)`, while the production-admission ordering remains unchanged:

```text
D(E_b) before Create(E_r) before P
R before D(E_b)
therefore R before P
```

### 3.3 Browser temporal authority

```text
S_exp := min(T_exp, K_db + M)
AdmitSession only if T_exp > ApplicationNow and S_exp > K_db
StoreInsert only if StatementTime < S_exp
```

By the definition of `min`:

```text
S_exp <= T_exp
S_exp <= K_db + M
```

The guarded insert prevents a fence or transaction delay from reviving an
expired record. No new store operation is required.

### 3.4 HTTP error algebra

```text
DeclaredUnavailable -> 503 with capability-owned public error
UnexpectedException -> 500 with {"code":"internal_error"}
UnknownClosedAlgebraOutcome -> UnexpectedException
UnknownOidcRejectionReason -> UnexpectedException
MiddlewareDeadlineExpired -> capability-owned timeout response
DownstreamTimeoutError and not MiddlewareDeadlineExpired -> UnexpectedException
UnexpectedException -/> DeclaredUnavailable
```

The last implications are essential: exception class membership alone does not
prove a dependency outage, retry safety, temporary failure, or which timeout
scope expired.

Let route `i` have body maximum `m_i`, retained-body lane width `b_i`, and
`R_i(t)` active request-lifetime replay bodies at time `t`:

```text
BodyLaneOwned(request) until DownstreamComplete(request)
forall i, t: R_i(t) <= b_i
therefore RetainedReplayPayloadBytes(t) <= sum(b_i * m_i)
```

Path-isolated lanes preserve independence between routes; holding one route's
lane cannot consume another route's `b_j` permits.

### 3.5 Shutdown ownership

Let `O` be the single cleanup owner-task, `C_i` a cleanup caller, `S` the
configured shutdown bound, `D := S / 2` the reconciliation drain sub-budget,
and `t_0` the instant at which `O` starts.

```text
forall i: C_i waits on shield(O)
Cancel(C_i) -/> Cancel(O)
O owns exactly one deadline t_0 + S
0 < D < S
drain timeout before O deadline -> retryable close failure
O deadline and cleanup unsettled -> CleanupPending and no retry authority
CleanupPending and cleanup settles -> DeadlineExceeded
O deadline and cleanup settled -> DeadlineExceeded
DeadlineExceeded -> RuntimeFailed and no retry authority
O retryable failure -> a later call may create one new O
O success -> RuntimeClosed
```

Therefore concurrent callers observe one cleanup result and cannot extend or
shorten the owner's bound. The strict drain sub-budget makes a retryable drain
outcome reachable before the total owner deadline while reserving time for the
external-resource close sequence. At the deadline, the owner cancels its cleanup
child and sets a fence that prevents the child from initiating another close
operation. An already active third-party close may settle after the deadline;
that interval is represented by `CleanupPending`, remains not ready, and cannot
be retried or reactivated. Provider exceptions are consumed inside `O`; only a
fixed redacted failure class crosses the lifespan boundary.

### 3.6 Readiness linearization and disabled surface

Let `K` be the background-health sample taken after all asynchronous probes
settle, `D` mean disabled mode, and `Routes(D)` be its mounted route set.

```text
ReadinessResult uses BackgroundHealth(K)
BackgroundFailure before K -> not Ready
Routes(D) = {health, readiness, metrics}
ConnectedPolicyLoad(D) = false
```

This is not a transactional snapshot across PostgreSQL, provider, and
background state. It proves only that a background transition observed before
the final sample cannot be hidden by a stale pre-probe value.

## 4. Boundary Design

```mermaid
flowchart LR
    HTTP[FastAPI adapters] --> PORTS[Capability-owned input ports]
    PORTS --> APP[Application services]
    APP --> DOMAIN[Domain contracts]
    APP --> OUT[Outbound ports]
    OUT --> ADAPTERS[PostgreSQL and GitHub adapters]

    BOOT[Bootstrap loop] --> EB[Registration engine]
    EB --> REG[Durable production admission]
    REG --> CLOSE[Dispose registration engine]
    CLOSE --> ER[Fresh runtime engine]
    ER --> LIFE[ASGI lifespan]
    LIFE --> INITIAL[Initial reconciliation]
    INITIAL -->|success within bound| SERVE[Serve routes]
    INITIAL -->|failure or timeout| REJECT[Reject startup and drain]
```

### 4.1 Input ports

Input protocols live with the capability whose behavior they describe:

- config management ports with `app.config_management`;
- operator override ports with `operator_controls.use_cases`;
- workbench, provider inventory, workflow discovery, governance observation,
  and browser application ports with their existing capability boundaries;
- browser session and full identity ports with `browser_identity`.

Transport authenticators that consume `fastapi.Request` remain in HTTP because
they are not source-independent application ports.

### 4.2 HTTP composition

`api/http/app.py` continues to own only:

- app creation;
- router inclusion;
- middleware ordering;
- collection of route-owned body-limit policies; and
- registration of generic transport handlers.

The operator UI OpenAPI projection invokes this same composition root with an
exact UI-only dependency set. It does not manually assemble a second router
graph. Every projected operation therefore declares the generic redacted
`500`; a route-specific `500` schema may replace that generic schema only when
the route owns a different public response shape.

Each body-bearing router owns its path, byte limit, and public timeout/invalid
response. Any policy that reads capability evidence is constructed lazily only
when that route group is selected. The generic middleware owns bounded reading,
retained-body concurrency, timeout, and replay mechanics. A permit is released
only when the downstream call returns, so the in-process memory proof covers
both buffering and replay retention.

### 4.3 Enforcement

The import policy forbids HTTP code from importing or statically referencing
known concrete application service modules or facade exports, asserts that HTTP
declares only its three request-shaped protocols, and rejects any Python source
not covered by a contextual rule. The owner declares one finite typed set of
concrete service records: implementation module, `*Service` export, package
facades, and whether the implementation module itself is private. An
independent AST oracle compares that set exactly with every top-level concrete
`*Service` definition and every transitively reachable package-facade re-export
route in the admitted source inventory. The public name of each facade export
is part of that comparison; the current owner contract admits only same-name
facade exports, so an alias is rejected rather than becoming an unmodelled
route. Direct `Protocol` definitions are excluded.

Let `D` be the finite set of concrete definition authorities and `E` the finite
set of package-facade import edges. The oracle computes the least relation `R`
such that `(d, d) in R` for every `d in D` and `(d, s) in R and (s, f) in E`
implies `(d, f) in R`. It compares every public route `(d, f)` where `f != d`
with the owner declaration even when `f` is also a member of `D`; only the
identity pair for the same root is excluded. Thus import order does not affect
discovery, reachable cycles terminate at a finite fixed point, and a route
cannot disappear behind an intermediate facade or a colliding definition name.

Let `M` be the deterministic projection of every implementation and facade
module from those records. Acquiring a module object `m in M` yields the
synthetic authority `reserved:module-object:m`, which the HTTP rule rejects.
Exact private implementation modules and every declared service export are
also forbidden. Exact `from m import PortOrData` imports remain admissible
unless that exact symbol is independently forbidden. Thus a newly defined or
re-exported concrete service makes the inventory oracle fail until all of its
authority routes have one owner declaration.

Imported-name provenance is a monotone, flow-insensitive may-set. A finite
pre-pass assigns every static import to its CPython binding owner: the module
for module or declared-global bindings, the nearest enclosing function for
nonlocal bindings, the defining function for ordinary function locals, or the
current class namespace for ordinary class locals. Ordinary reassignment cannot
erase an imported authority. Function `global` declarations select the module
binding for their nested scopes; class `global` declarations do not change a
nested scope that CPython resolves through an enclosing function closure.

The pinned CPython `symtable` supplies executable function and class local
binders, including exception aliases, match captures, and names bound in an
enclosing scope by a comprehension assignment expression. AST declarations
supply generic parameters and comprehension targets. Generic function defaults
are evaluated against the defining environment; generic annotations and bodies
mask their type parameters except where an explicit `global` declaration gives
the executable body a module-owned binding. Generic class type-parameter masks,
but not a class-body `global` declaration or ordinary class locals, propagate
into nested class, callable, and comprehension environments.
Every executable AST function or class scope must consume exactly one matching
symbol table; a missing or unconsumed scope rejects the scan. Class bodies
otherwise conservatively retain enclosing provenance.

An exact `from m import n` projects `m.n`, not a synthetic authority for `m`.
Prefix rules still observe the dependency on `m`, while an exact allowed child
cannot be rejected by its forbidden parent. Under postponed annotations, direct
expressions and lambda defaults remain conservatively projected, but a lambda
body is not an executable lexical scope merely because the annotation may later
be resolved.

Direct attributes and literal `getattr` chains are resolved against those
sets. A bare sensitive import root used as a value projects every configured
descendant authority, so secondary aliasing cannot weaken the gate. The closed
dynamic-import token set is projected from imported names, literal member
subscripts, and literal `getattr` members. Static text is closed over string
constants, interpolation-free f-strings, and `+` concatenation of those forms.

The gate intentionally does not interpret control flow, exception dispatch,
evaluation order, descriptor behavior, or arbitrary Python object flow. This
restriction is a strength: exact imports make those semantics unnecessary for
the repository boundary, while avoiding a custom Python abstract interpreter.
The public boundary engine owns rule evaluation and source inventory; the
syntactic projection remains a separate one-way dependency with one public scan
operation.

This proves bounded syntactic properties:

```text
DirectKnownServiceImport -> gate failure
AliasReferenceToKnownService -> gate failure
LiteralGetattrOfKnownService -> gate failure
ConcreteServiceInventory != OwnerDeclaration -> gate failure
AliasedConcreteServiceFacade -> inventory mismatch -> gate failure
AcquireRestrictedServiceModuleObject -> gate failure
BareSensitiveRootEscape -> configured descendants -> gate failure
StaticDynamicImportToken -> reserved:dynamic-import -> gate failure
ExactAllowedFromImport -/> synthetic parent authority
ExactPortOrDataImport -/> restricted-module failure
HTTPDeclaredNonTransportProtocol -> gate failure
UncoveredPythonSource -> gate failure
```

It does not prove substitutability, cohesion, or correct orchestration. Those
remain semantic review obligations.

## 5. Alternatives

| Alternative                                            | Rejection reason                                                                                                                          |
|--------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| Keep startup alive but expose `readyz=503`             | Violates `A -> I and B`; stateful routes remain reachable.                                                                                |
| Use `NullPool` for the shared engine                   | Avoids pooled reuse but retains mixed lifecycle ownership and discards normal runtime pooling.                                            |
| Dispose and reuse the same engine                      | Permitted by SQLAlchemy, but creates a less explicit two-phase identity and complicates rollback proof. Two engines are simpler to audit. |
| Defer all resources to lifespan                        | Correct only after a wider composition rewrite and risks allocating provider clients before registration.                                 |
| Convert every exception to a typed unavailable outcome | Makes programming errors observationally indistinguishable from retryable dependency failures.                                            |
| One repository-wide `input_ports.py`                   | Centralizes unrelated capability change reasons and recreates the ownership problem at a different layer.                                 |
| Remove protocols and type against concrete services    | Reduces substitutability and forces tests and decorators to inherit implementation identity.                                              |

## 6. Falsifiers

The design is invalid if any witness demonstrates one of these cases:

1. ASGI lifespan yields after initial reconciliation failure or timeout.
2. A bootstrap engine or pooled connection is reachable from the serving loop.
3. A provider client is allocated before durable production registration.
4. A stored browser session expires after its provider token or after
   `K_db + M`.
5. An unexpected service defect returns `503` or exposes exception detail.
6. A typed unavailable result ceases to return its declared `503` contract.
7. HTTP imports a forbidden concrete service implementation without a gate
   failure.
8. Router body limits, middleware ordering, cancellation, or cleanup semantics
   change unintentionally.
9. Concurrent cleanup callers observe different owner outcomes, extend the
   deadline, hide unsettled cancellation, or initiate another resource close
   after the deadline fence.
10. Readiness returns green from a background sample captured before a blocking
    probe while reconciliation becomes unhealthy.
11. Disabled mode mounts UI or capability routes, or loads selected-capability
    evidence while composing its operability-only surface.
12. A provider diagnostic remains reachable from the rendered cleanup
    traceback.
13. A concrete `*Service` definition or direct or transitive package-facade
    export is absent from the owner inventory, a restricted service module
    object reaches HTTP without an import-gate failure, an aliased facade export
    is treated as a canonical same-name route, a facade route is discarded
    because its public authority also names another definition, or a valid exact
    port or data import is rejected.
14. A value outside the closed dynamic-plan result algebra is reported as a
    retryable dependency outage.
15. More than `b_i` bounded bodies for route `i` remain reachable while its
    downstream work is blocked.
16. A downstream `TimeoutError` for which the middleware deadline is not expired
    returns the route's typed timeout response.
17. An unknown config outcome returns `503`, or an idempotency contract defect
    emits a webhook-specific `500` body.
18. HTTP acquires a restricted service module object, or reaches it through a
    statically resolved attribute, without an import-gate failure.
19. An exact port, model, or DTO symbol import is rejected only because its
    declaring module also exports a concrete service, or an exact allowed
    `from` import is rejected through a synthetic parent authority.
20. A bare sensitive import root escapes as a secondary alias without projecting
    every configured descendant authority.
21. An OIDC verifier emits a reason outside the admitted finite sets and the
    request returns typed `503` unavailability.
22. An exception alias, match capture, generic type parameter, type-alias
    parameter, or enclosing comprehension assignment is mistaken for an
    imported authority; a generic class mask is lost in a nested callable or
    comprehension; or any admitted static dynamic-import token form is omitted.
23. A generic function default is evaluated as though its type parameter already
    masked the defining environment.
24. A lambda body in a postponed annotation is treated as an executable CPython
    scope, or authority acquired by its default expression is omitted.
25. A declared-global or nonlocal import is resolved through the wrong CPython
    binding owner, or an ordinary class-local import is propagated into a nested
    scope that resolves the same spelling through a module or function binding.
26. A generic parameter masks an explicit same-name `global` in its function or
    class body, or a class-body `global` incorrectly erases that generic mask in
    a nested lexical scope.

## 7. Non-Claims

- This change does not prove provider availability or production deployment.
- A passing import gate does not prove semantic architecture correctness.
- The body-lane bound covers complete replay payload bytes retained during the
  active request lifetime. It does not bound allocator overhead, ASGI-server
  buffering, transient conversion copies, or a downstream component that copies
  payload data into longer-lived state.
- The import gate does not interpret arbitrary Python object flow or provide a
  runtime security sandbox; dynamic-loading authorities remain forbidden and
  non-static reflection remains a review obligation.
- The import gate does not claim runtime path reachability, exception matching,
  or evaluation order. Its flow-insensitive provenance may reject reuse of a
  sensitive imported name; exact port and data imports are the admitted remedy.
- CPython `symtable` is the lexical-binding authority only for the admitted
  CPython 3.13.14 runtime. It is not evidence for another patch or Python
  implementation.
- The postponed-annotation projection does not claim that a latent lambda body
  will never be invoked after an external annotation resolver creates it.
- The concrete-service inventory is complete for top-level classes whose names
  end in `Service` and their package-facade re-exports. Concrete implementations
  outside that owner naming contract remain a semantic-review obligation.
- Structural protocols do not by themselves prove behavioral substitutability.
- Successful startup does not guarantee every later reconciliation round will
  succeed; later failures continue to degrade readiness and may recover.
- The temporary registration engine does not create a distributed startup
  singleton; idempotent registration remains the persistence contract.
- The asynchronous shutdown deadline cannot preempt CPU-bound code that blocks
  the event loop or third-party code that suppresses cancellation indefinitely;
  deployment termination remains the final outer bound.
- An in-flight third-party close may finish after the owner deadline. The runtime
  exposes that interval as cleanup pending and makes no claim that external code
  is preempted at the deadline instant.
- Readiness is a conservative projection, not a distributed atomic snapshot.
