# Response Freshness Implementation Plan

Status: delivery plan

Date: 2026-09-06

## Scope And Preconditions

Implement the [response-owned freshness design](refresh-result-freshness.md)
from `master` at `341a8972d6301fa9fd5cdad9331aae1494645892`. The
[audit ledger](../adoption/temporal-and-oracle-audit-2026-09-06.md) preserves
independent findings and their B2/B3/B5/D/E closure paths. This plan does not
close those paths by association.

## Ordered Work

| Step | Owner and files                                             | Intended change                                                                                                         | Acceptance                                                                                                                           |
|------|-------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| 1    | `identity_admission/jwks_provider.py`                       | Carry the existing private snapshot through the refresh result; retain acquisition time; recheck probe freshness.       | Both lookup/probe paths reject at/after original TTL after cancellation of every waiter; fresh results and subsequent recovery work. |
| 2    | `integrations/keycloak/discovery.py`                        | Return a timestamped private snapshot from the loader and publish it unchanged.                                         | `get`, `refresh` and synchronous `current` use original response age.                                                                |
| 3    | `integrations/keycloak/jwks.py`                             | Preserve response time and use the current-discovery predicate after refresh.                                           | `get_key`, `prepare` and `refresh` reject expired/source-obsolete results while retaining positive rotation and coalescing.          |
| 4    | Existing three cache test modules and Keycloak test support | Add native Event/task-completion barriers and parameterized boundary cases.                                             | No arbitrary sleep is accepted as completion proof; all old falsifiers remain.                                                       |
| 5    | Current indexes, roadmap and core/runtime Proofkit routes   | Link the successor and its plan; bind the same native owners.                                                           | Route digests, documentation reachability and predecessor design/plan preservation pass.                                             |
| 6    | Frozen review and GitHub Full Check                         | Independently challenge the code, model and oracles; publish additive commits and squash only after exact-head success. | No stale snapshot can pass through a sibling entrypoint; native evidence is reported separately from local static results.           |

The frozen review identified three additional oracle distinctions: recovery
after a failed result, expiry between surviving waiters, and time elapsed in the
real decoder. Add these owner-local traces before Full Check, preserving the
failure-backoff policy and isolating key age from discovery age. Source-reviewed
counterfactuals are not reported as executed mutation receipts.

## Whole-Chain Gate

The declared public entrypoint sets are closed for this change: GitHub
`get_key_set`/`probe`, discovery `get`/`refresh`/`current`, and Keycloak JWKS
`get_key`/`prepare`/`refresh`. `aclose` is the terminal transition in each owner.
Private field shape is not a public consumer contract.

Keep all predecessor positive and negative tests. Local Ruff, strict mypy,
imports, JSON, documentation and Proofkit gates precede publication. Behavioral
tests execute only in the repository's GitHub Full Check. Any failed required
gate or changed head leaves merge admission open.

## Explicit Remaining Work

No schema migration, endpoint, claim, session, retention, fallback, bot or
deployment policy changes in this batch. Failed-refresh cooldown and its
availability tradeoff remain B2 work. FIFO admission and causal test evidence
are separately implementable repairs, not reasons to expand this cache owner.
Production key rotation and provider failure qualification remain E1/E2.
