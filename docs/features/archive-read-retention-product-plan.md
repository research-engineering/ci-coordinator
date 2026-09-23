# Archive Read And Retention Execution Plan

Status: owned source batch authored; shared integration and native qualification pending

Design authority: [product design](archive-read-retention-product.md).
Independent review follows the current repository `AGENTS.md` policy.

1. Bind the clean assigned worktree and base, read existing owners, record the
   minimum sufficient model and owner readiness before code changes.
2. Add bounded domain read contracts, retaining one canonical header authority.
3. Add single-statement PostgreSQL header/job/gap/detail-metadata pages and
   exact public application authorization with authenticated continuation.
4. Add finite retention preview/apply and detail erasure using existing policy
   laws, locks, audit pair, quota and savepoint patterns.
5. Add isolated HTTP routes and transaction adapters without changing common
   dependencies, runtime composition, shared exports, registries or schemas.
6. Author focused boundary, HTTP and PostgreSQL tests. Run local static lint
   and type checks only; root runs native GitHub witnesses at the final SHA.
7. Commit the complete owned batch once; return the exact SHA and integration
   needs. Rebind the changed epoch before any final conformance verdict.

## Native Acceptance

Cases must distinguish scopes with unequal installation/repository IDs, stale
generation/configuration/data/default revisions, forged/cross-query/cross-actor
cursors, equal timestamp ties, empty and over-limit pages, exact attempt keys,
conflicting or partial populations, unknown provider availability, expiration
at the exact boundary, non-resurrection and unchanged permanent job operands.

Retention witnesses require preview without writes, explicit reviewed digest,
cutoff exclusion, current policy, same-command replay, actor/operation conflict,
changed population, actual released bytes, missing-child rollback, failed audit,
outer rollback and concurrent import/cleanup/configuration scope fencing.
HTTP witnesses require roles, actor injection, CSRF/mutation admission, no-store
on failures, duplicate query/body keys, bounded bodies and private errors.

## Root Integration

Wire the new transaction adapter from `PostgresHistoryUnitOfWork`, the service
from the existing repository authorizer plus admitted cursor key, and the route
builder using the existing history authentication/role/mutation dependencies.
Register its request/body admission policies in production middleware, with
the existing economics read bulkhead: use `history_read_request_limit` with
`CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT`, plus `HISTORY_RETENTION_BODY_LIMITS`
and `HISTORY_RETENTION_REQUEST_LIMITS`. Request admission must wrap body admission
so early failures also receive no-store headers. Register new model/OpenAPI provenance and
requirement/witness bindings, then regenerate projections through their owners.
No new router is mounted by this isolated batch.

Add `HISTORY_RETENTION_EVENT` from `ci_economics/history_retention_commands.py`
to `_is_pair_owned_audit_event_type` in `persistence/audit_repository.py`, beside
the existing history configuration event. This shared registry is outside this
writer's mutation scope. Both lookup and append currently reject an unregistered
retention event; apply therefore remains unavailable until this root-owned
admission is added. Do not bypass the pair-owned audit API or reuse the
configuration event with a different payload. Native retention cases below
require that registration and are not claimed green on the isolated tree.

Root must separately decide rich-detail payload admission, global defaults and
full statistical erasure. Do not activate these as implied by this plan.

## Authored Batch

The batch contains bounded read, cursor, retention-command and response
contracts, PostgreSQL read/retention functions, a transactional extension of
the existing history store, the authorization service and an unmounted router.
`archive_statistics.py` is changed only to make its header a reusable parent;
full-statistics admission retains the same population oracle. This avoids a
duplicate header schema or reconstructing every job for a summary read.

Six new test files cover domain/cursor and read-codec boundaries, application
admission, isolated HTTP routes, PostgreSQL read pages and retention operations.
They are authored evidence candidates, not execution receipts. Complete
production middleware/composition, root audit-event admission, final schema and
requirement inventories, and real PostgreSQL concurrency qualification remain
root-owned. The original external-audit rows are unchanged by this batch.
