# Frontend Transport Ownership

Status: source admission design; native qualification required before merge

Owner: `REQ-CI-UI-001`, browser request bounds and response admission

## Problem And Boundary

`boundedFetch` owns bounded browser response acquisition. A new raw request in
another production module can bypass that contract even when its own happy-path
test passes. Existing Promise analysis and runtime transport tests do not reject
the addition of a second request initiator.

Enforce a finite source contract with the already pinned ESLint implementation.
The contract applies to handwritten `.ts`, `.tsx`, `.mts`, and `.cts` files under
`frontend/src`. Generated API types retain their exact-byte generation owner;
test mocks and development tooling retain their separate execution contracts.

## Admission Contract

1. Runtime global `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, and
   `sendBeacon` references are forbidden outside the transport owner. The same
   property names are reserved against member access and destructuring.
2. Only `frontend/src/api/shared/boundedFetch.ts` may reference raw `fetch`.
   That exception does not admit the other network initiators.
3. Runtime global `window` and `self` references are forbidden. Use a named
   `globalThis` member for legitimate browser capabilities. Local variables with
   those names remain ordinary lexical bindings.
4. Access to `globalThis` must use a static named member. Passing, destructuring,
   casting, or aliasing the global object and computed member access are rejected.
   Type-only `typeof globalThis` remains allowed. Nested global-object aliases
   through `window`, `self`, `globalThis`, `top`, `parent`, or `frames` are rejected.
5. Existing Promise analysis remains an independent requirement. A transport
   ownership failure cannot be satisfied by awaiting an otherwise forbidden call.

These are source restrictions, not a browser sandbox or a proof of all possible
network activity. They do not analyze arbitrary dependency implementations,
reflection over other browser objects, DOM resource loading, navigation, or
server effects. Authenticated navigation and clipboard access keep their existing
owners. A new network API or indirect mechanism requires an explicit owner
decision and its own negative examples; source admission does not prove the
semantic adequacy of `boundedFetch` itself.

## Ownership And Alternatives

ESLint owns JavaScript/TypeScript parsing, scopes and the admitted built-in rule
behavior. The repository owns the allowed path, reserved APIs, syntax policy,
exceptions and native regression oracles. Agentic Proofkit owns structural
requirement/binding admission and routes the existing `frontend.quality` command;
it does not own browser API policy or execute this linter.

The selected route extends the existing ESLint configuration and adds bounded
static counterexamples. A text search misses aliases and destructuring. Importing
a larger foreign analyzer would duplicate parser and policy mechanisms.
A new Proofkit command is unnecessary because no missing generic proof primitive
has been demonstrated. The one existing recovery-button reference changes from
`window.location.reload()` to the equivalent named `globalThis.location.reload()`;
the browser reload behavior and receiver are preserved.

The rule may reject an otherwise harmless new property named `fetch`. That is an
explicit reserved-name constraint rather than a claim that every such property
performs I/O. Reopen the constraint when a concrete legitimate consumer requires
that name; preserve the bypass counterexamples before admitting a narrower rule.

## Verification And Delivery

The existing frontend lint command executes the production scan, Promise
counterexamples and transport counterexamples. Negative cases cover direct and
aliased calls, global members, destructuring, computed access, global-object
escape, reflective access, casts, nested aliases and alternate initiators.
Positive cases preserve `boundedFetch`, static origin lookup, local lexical
bindings, type-only global references and test mocks. Configuration checks cover
all four source suffixes and the generated-file exclusion.

Implementation order:

1. Extend the pinned ESLint rules without adding a parser or dependency.
2. Bind the static oracle and this design to the existing requirement and command.
3. Refresh owned derived metadata and qualify the exact committed candidate using
   the existing GitHub frontend and required aggregate gates. Local static checks
   do not substitute for native behavioral execution.
4. Obtain independent source review according to `AGENTS.md`. Do not change GitHub
   approval counts, CODEOWNERS or provider review policy.

Acceptance requires the forbidden examples to fail for transport rules, the
admitted examples to remain valid, the actual source scan to pass, and the
existing runtime/browser witnesses to retain their native result population.
Retire this source policy only after an admitted replacement preserves those
predicates with equal or lower maintenance cost.
