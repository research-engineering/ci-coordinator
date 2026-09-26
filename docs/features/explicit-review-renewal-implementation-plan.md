# Explicit Review Renewal Implementation Plan

Status: source candidate; native qualification pending
Date: 2026-09-26
Tasks: CI-054, CI-018
Design: [explicit review renewal](explicit-review-renewal.md)
Base: 7500536f5fd8acd3f28fe4f27e0bb60de7a3d6f7

## Writer Readiness

Only renewal domain/ports, app, persistence/migrations, HTTP/UI and focused
tests are admitted. Existing designs/plans and prepared frontend freshness work
are protected. Root owns generated contracts, routes, requirement metadata and
publication. This plan records obligations, not executed proof.

| Owner | Intended delta | Protected observations | Derived surfaces / causal witness |
|---|---|---|---|
| proposal_review ports and persistence | Multiple immutable operations for equal manifest; candidate and final authority lookup require exact review operation | Per-record codec/hash, actor/baseline, one-use consume, immutable rows, content dedup, scope lock | Schema attestors, PG two-receipt/selector/expiry/race cases |
| app.repository_attestation / HTTP | Closed current start/v2; unversioned legacy replay-only | Authentication, roles, CSRF, limits, exact replay before fresh work, callback binding | OpenAPI/types root-generated; malformed and unknown-legacy zero-effect witnesses |
| config_epochs / app / repository_activation | Explicit current selector; finite activation payload branches; reject unknown legacy before authority | Legacy payload bytes/hash, rollback, CAS, retained server time, authority digest, exact-operation result | Unit hash/replay operands and app provider/clock sentinels; PG pair replay |
| persistence compatibility / two migrations | Retire config/proposal v1, invalidate pending only, expand v2 without manifest uniqueness | Historical descriptors/migrations, audit/identity capabilities, fences, session/review/audit retention, ACLs | New-head native upgrade, old-UoW fence, catalog negatives, migration replay and rollback-on-failure |
| frontend attestation / activation consumers | Current wire and explicit receipt selection; deliberate Verify again | Confirmation snapshot, operation/baseline identity on retry, callback hints non-authoritative, abort/generation guards, retained sibling drafts | Existing freshness suites plus literal transport and renewal browser witnesses |
| normative module owners | Project admitted multiple-proof/cutover laws; link new design/plan | Existing design/plan payloads; unchanged unrelated authority | Static links and independent semantic review |

Direct frontend projections also include the existing callback navigation
hint allowlist, renewal-action grid and shared ConfigControlError consumers
(configLifecycle schema/transport and ConfigurationNotice). They carry the
same finite wire error and original baseline; they add no mutation authority,
retry policy or persisted storage. Root regenerates their API type source.

Independent validator: root-selected reviewer under current AGENTS policy, then
exact-head GitHub backend/PostgreSQL/frontend/browser owner routes. No local
behavioral execution, DB, runtime probes, dependency install or generator is
authorized. Static Ruff from backend cwd, mypy, AST and frontend no-emit checks
are permitted with existing tools only.

## Sequence

1. Publish this new design/plan and normative owner links before source edits.
2. Author independent literal closed-contract cases, then implement finite
   domain/HTTP replay branches and explicit selector propagation.
3. Remove manifest dedup only from current review persistence; add v2 catalog
   and capability admission while preserving old definitions and readers.
4. Add two forward migrations with exact predecessor admission, pending-only
   cutoff and proper contract/expand declarations. Preserve old migration files.
5. Integrate current UI wire/renewal action with the prepared freshness owners;
   never turn callback/reauthentication into submission or mint an ID on retry.
6. Author PG concurrency/migration and browser witnesses, run static checks,
   freeze the full source/test manifest and hand off independent review.

## Closed Cases

| Case | Independent operands / falsifiers |
|---|---|
| C1 Current review | Same manifest, two fresh operation IDs and independent one-use challenges -> two receipts, one content epoch; old receipt bytes unchanged. Same operation exact replay writes nothing. |
| C2 Identity and authority | Independently mutate actor, scope, manifest, baseline epoch/revision/absence, session, profile, provider permission, proposal/revision and expiry; no receipt impersonation or selected-receipt fallback. |
| C3 Wire cutoff | Literal old body exact retained replay succeeds; unknown old operation rejects before discovery, clock, pending or write. Null/unknown version and malformed current fields never parse as legacy. |
| C4 Activation identity | P1/W1 and P2/W2 replay only; changed selector/version/actor/target/revision/manifest conflicts before fresh work. Payload/v1 golden bytes stay exact; P2 adds required literal selector. Rollback unchanged. |
| C5 Pair and generic reader | Actual generic append rejects existing family for both payloads; owner pair commits atomically. Mixed chain/export remains valid; payload tamper fails integrity, unsupported rehashed semantic tuples fail command replay. Activity action unchanged. |
| C6 Cutoff concurrency | Old shared mint/consume finishes before exclusive cutoff or is fenced afterwards. Old cookie/provider-in-flight cannot consume post-cutoff authority. New nonce is not affected by old retirement. |
| C7 Migration | Exact predecessor/constraint admission; pending-only deletion, empty postcondition, strict two transitions; unchanged history/session/epoch/audit. Wrong schema/timeout rolls back. Applied migration replay never clears current pending rows. |
| C8 Requirement closure | Old config/proposal UoWs reject; each missing current capability rejects before domain SQL. Audit-only and Activity semantics stay valid; current Workbench/registration/production-cutover sets retain all dependencies. |
| C9 UI continuity | Verify again creates a fresh ID only by user action. Uncertain retry uses identical ID, selector and baseline. Callback hint needs authenticated exact replay; stale/aborted/session-changed completions do not authorize activation. |
| C10 Activation receipt | Require the captured command's exact target and successor revision for fresh and duplicate results. Independent literal positives and shape-valid revision-only substitutions distinguish relational admission from schema rejection; a deferred response must not reread mutable caller state. Wrong revision retains uncertainty without confirmation; identical retry can confirm the historical successor after a newer workspace read. |

Native oracles must use actual UoWs/migrations and the production browser
fixture, not only mocked state or parser roundtrips. Authored cases are not
passes. Missing native execution, actual old-deployment inventory and migration
cardinality remain explicit handoff gaps. A new semantic owner or failed
design premise stops this writer for root admission.

## Candidate Boundary

The finite source implementation and C1-C10 witnesses are authored. This is not
an executed test verdict. Static checking uses existing tools without running
application code, SQL, pytest, browser, build or provider operations.

Root integration must project current HTTP/OpenAPI/TypeScript contracts,
database compatibility inventory, proof routes and source hashes, then run the
native owner cohorts against the integrated candidate. The generated TypeScript
error unions still precede this source delta until that projection is rebuilt.
Old-artifact deployment inventory, migration budget qualification and independent
review remain required; they are not discharged by a source/typecheck result.
