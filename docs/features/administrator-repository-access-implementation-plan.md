# Administrator Repository Access Implementation Plan

Owner: delivery of the [scope contract](administrator-repository-access.md).

## Ordered Work

1. Add explicit restricted/App scope mode to settings admission, direct
   constructors and redaction. Reject App scope mode without App inventory.
   Preserve existing defaults, static emergency scopes and enforcing-scope
   subset requirements. No historical migration or design changes.
2. Extend the scope authorizer with a narrow provider-membership port and a
   typed unavailable exception. Authenticate the actor before provider I/O;
   never bypass current membership through the static list in App mode.
3. Implement exact installation identity and bounded membership paging using existing
   clients, transport guards and strict decoders. Preserve each independent
   identity and failure predicate; do not reuse economics policy as auth.
4. Wire the provider in composition and the explicit mode into inventory
   projection. Map unknown authorization evidence to the existing public
   unavailable response envelope. Keep every route's role/CSRF admission.
5. Update catalog action states and restricted-mode guidance. Document the
   one-time operator setting and API parity; no per-repository deployment edit
   in App scope mode, no automatic observation or selective execution.
6. Add focused parameterized native tests, refresh owned requirement/Proofkit
   routes and static metadata. Freeze source for one independent reviewer
   under current `AGENTS.md`; adjudicate reproducible findings, not preferences.
7. Execute required GitHub checks on the exact candidate; squash only after
   required gates pass. Verify post-merge CI and immutable release separately.
   Opt the owned Swarm deployment into the explicit mode and check real deployment-owned
   login and catalog-to-repository navigation without altering client workflows.

## Writer Readiness And Proof Map

| Owner                  | Intended delta                  | Protected observables                     | Derived surfaces and oracle                                                     |
|------------------------|---------------------------------|-------------------------------------------|---------------------------------------------------------------------------------|
| Runtime settings       | Explicit scope mode             | Defaults, secrets, enforcement scopes     | Constructor/env/redaction tests; runtime settings witnesses                     |
| Scope admission        | Fresh membership branch         | Actor, actions, emergency static mode     | Cross-product native unit tests; HTTP 503 witness                               |
| GitHub integration     | Exact membership observer       | Bounded requests, IDs, no redirects       | Existing adapter falsifiers plus membership paging, budget and rename witnesses |
| Inventory              | Available rows under App scope  | Installation/account binding, pagination  | Service tests, no per-row I/O, restricted controls                              |
| HTTP/composition       | Wire port and error translation | Roles, CSRF, request limits               | Native route/composition and dependency-boundary checks                         |
| Frontend               | Working Open and honest remedy  | Server truth, expiry, scope reset         | Native catalog/browser tests, type checks, live deployed login                  |
| Documentation/Proofkit | Current contract routing        | Historical design bytes, proof boundaries | Documentation graph, requirement bindings, native-route admission               |

Each provider conjunction must have a control where only that operand is
mutated. A shared happy-path fixture is not a replacement for those negative
oracles. Failure tests also assert no protected storage or later provider
operation occurred. Cancellation is not transformed into unavailable.

Distinguishing witnesses reuse one concrete reader before and after revocation,
pair selected and unselected public repository identities, exercise catalog rows
through the composed runtime, and advance one test-local event-loop clock across
credentials and later pages. Existing mutation cohorts own `HA17`-`HA19` and
`PP19`: renewed page deadlines, cached grants, blanket public denial and omitted
catalog mode wiring. Their finite outer budgets follow
`2 * case_count * per_case_timeout + 60000 ms`; per-case and CI job limits remain
unchanged. Behavioral and mutation outcomes require GitHub execution.

The native collection route must load the new test independently of sibling
test modules. Its closed mutation-inventory assertion must include all 19 HTTP
admission cases. The production-build witness pairs a valid documentation URL
with rejected aliased fixtures and copied synthetic assets, so fixing a false
positive cannot silently remove the development-data boundary.

## Completion Boundary

This closes the redundant deployment-scope onboarding prerequisite in D5/D7.
Then continue D4 continuous observation with enable/pause, bounded backfill,
gap reporting and real progress, reusing the current source-registration and
collection lifecycle. Do not mark those future functions delivered by this
access change. Broad code-quality claims, native test execution, rollout and
live browser success each require their own current evidence.
