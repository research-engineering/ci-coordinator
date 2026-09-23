# Administrator Activity Product Plan

Status: integrated backend source; independent native acceptance pending

The [product design](administrator-activity-product.md) owns semantic choices.
This plan owns execution order and acceptance only. Existing design/plan files
and published migrations remain untouched.

## Execution

1. Bind the clean worktree at 4404947b99db9d767fbee587e626b4dd5fd8c62e and read
   identity, audit, ownership, native-witness and migration contracts. Done.
2. Establish minimum model, event-class failure semantics, alternatives and
   writer readiness in the new product design. Done.
3. Add pure activity/cursor contracts, one forward 20260913_0012 revision,
   bounded persistence, session hooks and explicit route dependencies. Supplied.
4. Author parameterized unit, HTTP and PostgreSQL witnesses without executing
   or collecting them locally. Supplied; Ruff and whitespace checks only.
5. Integrate shared registries, schema/ACL admission, runtime, HTTP and bounded
   cleanup under the expanded backend writer authority. Supplied.
6. Commit owned changes once; return exact SHA, frontend contracts and non-claims.
7. Root rebinds the new epoch, integrates the Activity UI and generated contracts,
   then runs native CI and independent review under AGENTS.md.

## Acceptance Matrix

| Predicate                              | Native falsifier                                             |
|----------------------------------------|--------------------------------------------------------------|
| no false committed login/logout        | journal insertion failure rolls back session mutation        |
| replay does not duplicate effects      | repeated local/back-channel logout and replacement           |
| expiry is retained without credentials | expiry deletion, invalid record, profile revocation          |
| current authority on every page        | absent/expired principal, role removal, foreign scope        |
| sealed bounded query/export            | filter/cursor mutation, limits, invalid times, repeated page |
| retention and bounded memory           | boundary instants, more than one page, cleanup batch limit   |
| no sensitive storage/output            | secret markers in rejected login and session metadata        |
| cancellation is not success            | cancelled persistence and cancelled optional diagnostics     |
| business evidence remains owner-bound  | recognized pair event versus rejection/unknown event         |

Native receipts are pending. A green baseline suite is not proof of these new
predicates. No test oracle may redefine owner-required rejection or atomicity.
The source batch is not a final completeness receipt: consult Implemented
Coverage And Remaining Hooks in the design before root acceptance. Exact-epoch
typechecking, PostgreSQL graph/ACL admission, business-projection native
falsifiers and concurrency/timeout measurements remain root qualification work.

## Integration Acceptance

- Activity metadata, schema capability, independent catalog and identity-sequence
  attestation are registered. Runtime grants admit journal SELECT/INSERT/DELETE,
  bucket SELECT/INSERT/DELETE and UPDATE(count), and sequence USAGE only.
- Generation 12 result attestation is connected; the existing unpublished
  migration and all published predecessor bytes remain unchanged.
- Exact repository access, separate trusted issuer authority, omitted-issuer
  inference, separated cursor key, common HTTP admission, lifecycle diagnostics
  and periodic cleanup are connected. Consult the design's finite hook inventory.
- Root must route new docs/source/tests through documentation and Proofkit
  registries; generate OpenAPI/TypeScript from the integrated application.
- Root must qualify identity migration/ACL/transaction concurrency with PostgreSQL, then
  UI export, provider and external log retention separately.

## New Native Witnesses

Activity migration tests independently mutate column, constraint, index, identity
mode, sequence increment/cache and row-security facts; the migration failure
witness preserves the archive and previous declaration. Runtime tests independently
revoke required INSERT/sequence USAGE and add forbidden journal/sequence UPDATE.
Lifecycle tests cover journal rollback, concurrent logout/replacement, stale and
cross-scope cursors, exact retention equality and interrupted cleanup rollback.
The concurrency oracle admits the specified lock-timeout unavailability and
checks actual session/journal effects before any read-confirmed retry.
The business projection witness uses a real retention commit/replay pair and
checks unchanged audit rows, no security copy and foreign-repository exclusion.

HTTP witnesses cover all mounted routes and authentication challenges, omitted
and foreign issuer, break-glass, one shared four-request admission budget and a
route deadline that cannot enlarge the global deadline. Observer witnesses cover
ordinary exceptions, caller cancellation, anonymous pre-service callback failure
and login-start unavailability. Cursor witnesses preserve the prior history key
derivation and reject a history-purpose key in Activity. Composition witnesses
bind cleanup, query and observer to the same store and identity dependencies.

These tests are authored, not locally executed or collected. Local source gates
are Ruff, repository-mode mypy (explicit package bases), whitespace checks and
schema metadata generation. They do not replace native acceptance or final
root rebinding. Concurrent frontend and workspace-document changes remain with
root and are excluded from the backend writer's commit.

## Backend Batch Manifest

The following paths are the backend writer's complete change inventory. This
inventory excludes concurrent frontend, INDEX and workspace-document changes.

```text
backend/src/ci_coordinator/api/http/activity_contracts.py
backend/src/ci_coordinator/api/http/app.py
backend/src/ci_coordinator/api/http/dependencies.py
backend/src/ci_coordinator/api/http/operator_ui_access.py
backend/src/ci_coordinator/api/http/request_admission.py
backend/src/ci_coordinator/api/http/routers/activity.py
backend/src/ci_coordinator/api/http/routers/control_plane_identity.py
backend/src/ci_coordinator/api/http/routers/operator_controls.py
backend/src/ci_coordinator/control_plane_identity/activity.py
backend/src/ci_coordinator/control_plane_identity/activity_query.py
backend/src/ci_coordinator/observability/diagnostics.py
backend/src/ci_coordinator/observability/runtime_metrics.py
backend/src/ci_coordinator/persistence/activity_capability.py
backend/src/ci_coordinator/persistence/activity_repository.py
backend/src/ci_coordinator/persistence/activity_schema_attestation.py
backend/src/ci_coordinator/persistence/activity_schema_contract.py
backend/src/ci_coordinator/persistence/ci_economics_schema_attestation.py
backend/src/ci_coordinator/persistence/ci_economics_schema_contract.py
backend/src/ci_coordinator/persistence/compatibility_admission.py
backend/src/ci_coordinator/persistence/migration_result_attestation.py
backend/src/ci_coordinator/persistence/principal_attestation.py
backend/src/ci_coordinator/persistence/resources/database-compatibility-profile.v1.json
backend/src/ci_coordinator/persistence/runtime_principal_access.py
backend/src/ci_coordinator/persistence/schema.py
backend/src/ci_coordinator/persistence/schema_capabilities.py
backend/src/ci_coordinator/runtime/activity.py
backend/src/ci_coordinator/runtime/composition.py
backend/src/ci_coordinator/runtime/control_plane_composition.py
backend/src/ci_coordinator/runtime/cursor_key.py
backend/src/ci_coordinator/runtime/history_cursor_key.py
backend/src/ci_coordinator/runtime/maintenance_round.py
backend/tests/integration/persistence/conftest.py
backend/tests/integration/persistence/test_activity.py
backend/tests/integration/persistence/test_activity_admission.py
backend/tests/integration/persistence/test_activity_business.py
backend/tests/integration/persistence/test_activity_migration.py
backend/tests/integration/persistence/test_migrations.py
backend/tests/unit/api/http/test_activity_admission.py
backend/tests/unit/api/http/test_activity_route.py
backend/tests/unit/api/http/test_control_plane_identity_route.py
backend/tests/unit/control_plane_identity/test_activity.py
backend/tests/unit/persistence/test_initial_migration_retention.py
backend/tests/unit/persistence/test_schema_topology.py
backend/tests/unit/runtime/test_activity_integration.py
backend/tests/unit/runtime/test_runtime_dependency_composition.py
docs/features/administrator-activity-product-plan.md
docs/features/administrator-activity-product.md
docs/specs/ci-coordinator-core/database-compatibility-profile.v1.json
```

## Retained-Session Repair Batch

The root-adjudicated design is the Retained-Session Attribution Repair appendix
in the product design. Batch baseline is
`a4c6cea1ca1bc2ab653e5d6734b0d455038731c6`; these two documents remain additions
relative to intended base `7302ea47`. Execution order:

1. Bind baseline, current owner bytes, complete decoder, transaction/locks and
   pinned SQLAlchemy/psycopg cursor contracts. Declare model and readiness before
   code mutation. Bound; native execution remains pending.
2. Extract the existing complete retained-session codec without policy changes.
   Replace unadmitted deletion attribution with a single streamed handle
   selection and bounded actual-row deletion/admission/journaling.
3. Author PostgreSQL witnesses using bounded direct fixtures: valid and malformed
   current/expired load, previous-handle replacement, ordered expiry cleanup,
   multi-partition capacity eviction, local logout and exact-target back-channel
   deletion. Check exact survivors/deletion counts and every valid event; reject
   malformed attribution and duplicate effects. Inject a later journal failure
   and cancellation after an earlier partition wrote events, then assert full
   session/replay/journal rollback.
4. Author principal role_denied/export witnesses with prepared hourly buckets:
   incoming counts 126, 127, 128, 999999 and 1000000; exact resulting count,
   event cardinality, immutable identity and denied/attempted outcome. Keep
   production quota/time/failure behavior unchanged.
5. Run only permitted static Ruff/mypy and whitespace checks. Commit the complete
   owned batch and return SHA, support-source coordinates and unexecuted native
   predicates. Do not collect or execute local tests, use DB/Docker, or publish.
6. Root rebinds the post-edit epoch, integrates its separately owned HTTP/frontend
   oracles and generated routing, and independently qualifies the exact head in
   native CI under AGENTS.md. A source commit is not an acceptance receipt.

The cheapest sufficient whole-chain gate is the exact session/Activity PostgreSQL
cohort plus unchanged identity regressions and independent owner review. Bulk
fixtures avoid hundreds of service setup/attestation calls without bypassing the
runtime store at the exercised boundary. Reopen the plan if original selection,
full admission, fixed memory or later-failure rollback is not preserved; do not
weaken an oracle or increase a production budget to make a witness green.

### Supplied Repair And Remaining Acceptance

Steps 2 through 4 are supplied as source. The native matrix includes 14 session
cases and 10 principal-quota cases from explicit parameter sets, not pytest
collection. The back-channel interleaving witness removes a selected malformed
row through current load on another connection: 257 selected handles must yield
256 actual deletions and all 129 valid events. The later-failure witness observes
earlier journal writes and replay consumption before a native SQL error or caller
cancellation, then compares every retained session column after rollback.

Frozen dependency installation used `uv sync --frozen --no-install-project`.
Ruff lint/format checks and targeted mypy with `--explicit-package-bases` pass for
the five Python files below; whitespace checks pass. Initial formatting and
RowMapping/tuple-comparison type errors were corrected without weakening checks.
No project import, test collection, behavioral suite or database was executed by
the writer. Native acceptance and exact post-integration routing remain pending.

Complete repair manifest:

```text
backend/src/ci_coordinator/persistence/activity_write.py
backend/src/ci_coordinator/persistence/control_plane_session_codec.py
backend/src/ci_coordinator/persistence/control_plane_session_repository.py
backend/tests/integration/persistence/test_activity_diagnostics.py
backend/tests/integration/persistence/test_activity_session_deletion.py
docs/features/administrator-activity-product.md
docs/features/administrator-activity-product-plan.md
```

Read-only support outside the assigned changed-effect cohort included
`persistence/connection.py` for READ COMMITTED and the psycopg engine,
`scripts/python_witness.py` for static typecheck invocation, and `backend/uv.lock`
for exact dependency installation. Root must retain these support bindings when
rebinding; they are not newly claimed effect coverage or native proof.
## Principal Oracle Closure

Each selected excess-authority scenario first admits the legitimate runtime
principal, then rejects one added privilege and finally admits the restored
principal. This makes the existing DB24 mutation sensitive to both over-grant
acceptance and universal rejection after the Activity sequence allowance. Keep
the same mutation operator, expected killed count and negative scenarios; no
privilege predicate or acceptance threshold is weakened.
