# Control-Plane Audit Closure

Status: scoped implementation design and acceptance plan.

## Scope

Close `ARCH-01`, `API-SEC-01` and `ROUTE-01` from the
[snapshot adjudication](../adoption/snapshot-review-validation-2026-09-13.md).
These are corrections to existing contracts, not a new planning capability.
The touched history-service witness also closes the `TEST-05` empty-turn gap:
each of the four current lanes attempts one claim and performs no later effect.

| Predicate owner                                                                                            | Current contradiction                                                                  | Required result                                                                      |
|------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `REQ-CI-CORE-002`, public domain APIs in [application use cases](../architecture/modules/app-use-cases.md) | Two scanning services import a private domain module.                                  | One public capability-owned worker-identity admission; a static private-import gate. |
| [Control-plane identity](../architecture/modules/control-plane-identity-and-repository-attestation.md)     | Ordinary override success/error responses omit the personalized-response cache policy. | Preserve status, body and authentication challenge; always emit `no-store`.          |
| `REQ-CI-RUNTIME-015`, [observability](../architecture/modules/observability.md)                            | A handwritten placeholder list turns existing matched templates into `unmatched`.      | Labels belong to the actual registered template set plus `unmatched`.                |

CI decisions, repository authorization, operation identity, lease authority,
database state and schema, callback semantics, public assets and deployment
configuration are protected observations. None changes in this slice.

## Boundary Decisions

### Worker Identity

Expose worker-identity admission from the existing observation/lease owner.
It reuses the existing lowercase SHA-256 predicate, preserving exact type,
length, character and exception behavior. Services validate before any effect;
leases retain their independent token and temporal checks.

The import engine gains an optional first-party private-import allowlist.
Enable it for application consumers, admitting private imports only within
`ci_coordinator.app`. Other rules retain their existing defaults. Apply it to
the scanner's projected static import identities, not arbitrary Python runtime
reflection. A future caller needs an explicit public API, not another private
module exception silently added to the application allowlist.

Removing constructor validation loses fail-fast behavior; copying the predicate
creates a second owner; renaming every private utility broadens the change.
One public function and the existing gate close the actual boundary at lower
change cost. No new DTO, service wrapper or validation framework is needed.

### Personalized Responses

The two existing override response helpers own `Cache-Control: no-store`.
Preserve `WWW-Authenticate` on 401 and every existing status/body projection.
Keep shared outer rejection handling and public-asset caching unchanged.
A new response framework adds no necessary capability for these two helpers.

### HTTP Template Registry

The existing observation middleware already receives the admitted Starlette
routes. Project their canonical string templates once into an immutable set and
bind it to the runtime metrics instance. Runtime metrics remains independent of
Starlette. It owns only membership and instrumentation.

Binding is serialized, idempotent for the same set, and rejects a different set.
An unbound metrics instance records HTTP routes as `unmatched`. Each runtime
owns one application/metrics pairing; sharing one instance between different
route catalogs is not admitted. Configuration hot reload does not add ASGI
routes and therefore does not mutate this set.

```text
R = immutable templates captured from the admitted application routes

RecordedRoute(p) = p if p in R else unmatched
Image(RecordedRoute) subset R union {unmatched}
|Image(RecordedRoute)| <= |R| + 1

Bind(R); Bind(S) succeeds iff R = S
```

The membership boundary admits all captured parameter names and never raw
concrete paths merely because they start with `/`. Extending the placeholder
list repeats the original maintenance failure; accepting arbitrary strings
would not independently enforce the label bound. One set and an initialization
lock are sufficient; no dynamic route service or per-request registration is
introduced. Request logging and cancellation retain their existing owners.

## Implementation Order

1. Add public worker admission, switch both services and lease worker checks,
   and enable the application private-import rule. Preserve same-owner private
   imports and all existing infrastructure prohibitions.
2. Add the cache header to the existing override helpers without changing
   response models, authentication or mutation behavior.
3. Bind actual templates during observation middleware construction and replace
   the heuristic sink filter with immutable membership.
4. Extend existing witnesses and route the new design through the current
   Proofkit and architecture owners. Do not replace historical design payloads.
5. Run local static checks, one independent batch review under `AGENTS.md`,
   and the required native GitHub checks on the final exact revision. Squash
   publication does not prove deployment or operational qualification.

## Sensitive Witnesses

| Relation                   | Positive controls                                                       | Isolated falsifiers                                                                                  |
|----------------------------|-------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| Worker admission           | Valid lowercase 64-character identities across both services and leases | Wrong type, length, uppercase or non-hex input; no store/provider effect                             |
| Empty history turn         | Exactly one claim and one no-work outcome for each current lane         | A repeated claim loop, a missing lane or any provider/completion effect                              |
| Private-import enforcement | Public capability symbols and same-app private helpers                  | Explicit private module, from-import and alias projections, including a different capability         |
| Cache policy               | Accepted and duplicate overrides; unchanged wire body                   | Missing no-store on domain rejection, unavailability or unauthorized response; lost bearer challenge |
| Template identity          | Matched templates with current and new parameter names                  | Collapsing legal templates to unmatched or substituting concrete request paths                       |
| Registry lifecycle         | Same-set replay and read-only request projection                        | Rebinding another catalog, including competing binds; unknown route labels                           |

Unknown paths and varying concrete identifiers must not increase the template
label population. Existing liveness, readiness, metrics authentication, logging
failure and cancellation witnesses remain required. Test existence alone does
not discharge these predicates.

The mounted-catalog witness enables every HTTP capability in the actual app
factory and independently enumerates its public OpenAPI paths. Opaque dependency
fixtures deliberately cannot perform domain work: this witness owns route
correspondence, not handler success. It invokes every declared public method
with distinct path identifiers and compares the exported template population.
A negative control removes one economics template only from observation while
leaving the HTTP operation and independent OpenAPI inventory intact. The same
correspondence assertion must reject that mismatch. Synthetic examples remain
useful for future parameter names, but cannot replace this composition witness.

## Revision Conditions

Revisit this design if one process deliberately serves multiple independently
owned ASGI catalogs, routes become dynamically mutable, private API exports are
explicitly admitted, or personalized caching acquires a new owner policy.
Registry binding is startup authority, not proof of provider availability,
request authorization, worker liveness or production capacity. No global
optimality or exhaustive architecture certification is claimed.
