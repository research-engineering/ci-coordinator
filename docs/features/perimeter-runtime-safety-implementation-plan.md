# Perimeter Runtime Safety Implementation Plan

Status: implemented

Date: 2026-07-29

## Scope

This batch closes six independently falsifiable perimeter predicates without
changing domain authority, provider capability, key lifecycle, ingress rate
policy, or deployment approval:

1. remove served API documentation and schema routes from the session-bearing
   runtime origin while retaining in-process schema generation;
2. replace path-pattern regex execution with a bounded reachable-offset
   automaton;
3. reject encoded, malformed-length, or oversized live provider responses
   before raw application-body iteration, and apply the aggregate byte bound to
   a transport-injected materialized response without making a wire-byte claim;
4. admit webhook, static bearer, and CSRF comparison operands before
   constant-time byte comparison;
5. require at least 32 UTF-8 bytes for webhook secrets and 32 to 4,096 ASCII
   bytes for operator bearer tokens at connected startup; and
6. avoid a mutating session delete on a physical digest miss while retaining
   deletion of an existing expired row.

## Owner Decisions

| Surface                 | Owner                             | Preserved observable                       | New admission                                                                                                            |
|-------------------------|-----------------------------------|--------------------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| Runtime API description | `api.http`                        | `app.openapi()` contract generation        | no served docs, ReDoc, OpenAPI, or OAuth redirect                                                                        |
| Freshness patterns      | `repo_context.freshness`          | admitted glob language                     | `P <= 512`, `S <= 4096`, `O(P*S)` time, `O(P+S)` state                                                                   |
| Provider response body  | `integrations.github.app_http`    | typed unavailable/timeout algebra          | identity request encoding, no response encoding, canonical bounded length, raw network stream, bounded injected response |
| Pre-auth comparison     | identity and HTTP boundary owners | constant-time equality after admission     | canonical ASCII or bounded raw-byte operands                                                                             |
| Authentication secrets  | `runtime_settings`                | redaction and connected-mode construction  | webhook UTF-8 byte bounds; operator ASCII byte bounds                                                                    |
| Browser session lookup  | `persistence.browser_sessions`    | statement-time expiry and targeted cleanup | physical miss is read-only                                                                                               |

Source independence is preserved because provider and browser inputs remain
untrusted until their own admission. Transport independence is preserved
because HTTP encoding and body allocation claims stop at the HTTP client
boundary. Semantic independence is preserved because no planning,
authorization, or omission rule changes. Operational independence is preserved
because this batch does not claim secret randomness, key rotation, ingress
rate limiting, network-chunk allocation, deployment readiness, or production
availability.

## Proof Matrix

| Predicate                                                              | Falsifier                                                                                                                                                            |
|------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Runtime documentation is not origin-served.                            | Any documentation or schema path returns other than 404.                                                                                                             |
| Glob matching is bounded.                                              | An admitted pattern reaches a regex engine or requires superlinear-in-`P*S` state transitions.                                                                       |
| Provider admission precedes application-owned network body allocation. | Encoded, invalid-length, or oversized-length network response reaches raw iteration; an injected materialized response bypasses the aggregate post-allocation bound. |
| Pre-auth text cannot raise comparison type errors.                     | Non-ASCII webhook, bearer, or CSRF input escapes as an exception.                                                                                                    |
| Startup rejects trivial authentication secrets.                        | A 31-byte webhook or operator secret constructs connected settings.                                                                                                  |
| Session miss is non-mutating.                                          | A missing digest issues `DELETE`, or an existing expired row is no longer cleaned up.                                                                                |

## Verification

Focused unit and persistence tests own the counterexamples. Ruff and mypy own
changed Python syntax and types. Import-boundary, documentation, requirements,
Proofkit verification, and selective planning own repository admission. The
portable full gate and any production-readiness decision remain with the root
owner.
