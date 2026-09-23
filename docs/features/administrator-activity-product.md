# Administrator Activity Product

Status: integrated source; independent native qualification pending

## Authority And Model

The Administrator Activity And Authentication Audit section in ROADMAP.md owns
the product delta. The identity module owns principals and session transitions;
audit_replay owns retained business evidence; persistence owns transaction time,
atomicity and storage. This document owns the new activity projection, not a
replacement for those authorities. Execution is in
[the product plan](administrator-activity-product-plan.md).

Baseline: 4404947b99db9d767fbee587e626b4dd5fd8c62e. As-is, selected business
mutations have pair-owned audit events; browser sessions disappear on deletion.
The minimum sufficient model is an event-order model plus an information-flow
boundary. Materiality is established by persisted identity, privacy and failure
semantics. Direct HTTP-success assertions cannot distinguish rollback from
commit, so they are not a sufficient model.

Intended delta:

1. Session creation and valid retained-session deletion write a security event
   in the SAME transaction. A rolled-back write produces neither durable effect.
2. Invalid login telemetry is anonymous, aggregated into fixed hourly buckets.
   Role denial is a bounded, best-effort diagnostic, never a committed mutation.
3. The activity read side projects selected pair-owned business events in place.
   It never copies them into a second journal or changes canonical replay.
4. Each page authenticates the current principal, requires audit role and an
   explicit scope authorizer. An issuer-wide grant is distinct from repository
   access; neither may imply the other.

Protected behavior: token-free sessions, credential-plane exclusivity, exact
Origin/CSRF checks, role checks, no mutation replay after browser recovery,
cancellation propagation, original identity failure codes, and provider logout
being independent from committed local session removal.

## Event Classes And Failure Semantics

| Class                                      | Outcome authority                             | Durability and retention                          |
|--------------------------------------------|-----------------------------------------------|---------------------------------------------------|
| login                                      | session insert in the same transaction        | required; 30 days                                 |
| logout / expiry / revocation / replacement | exact deleted session rows                    | required; 30 days                                 |
| role denial                                | verified principal rejected by role owner     | best effort, bounded global hourly quota; 30 days |
| login rejection / login unavailable        | no admitted actor                             | best effort fixed hourly counters; 48 hours       |
| business mutation                          | existing pair-owned audit record              | existing ledger retention unchanged               |
| export                                     | authorized bounded page prepared for response | best effort attempted event; delivery is unknown  |

Journal storage failure rolls back session changes. This is a required security
record, not an optional export dependency. Read/export failure never invalidates
an otherwise active session. A cancelled or disconnected commit has an uncertain
caller outcome: no synthetic success/failure row is inferred from the request.
Expiry is recorded when deletion is observed, not by inventing an exact timer
execution. Absent and malformed session rows cannot supply an accountable actor.
Repeated logout and consumed back-channel replay create no duplicate deletion
events. Session limits evict with a replacement event. No session handle,
handle digest, sid, token id, display name or credential is copied to activity.

Security records have database timestamps, verified issuer/subject/actor,
closed action/outcome values and generated operation/correlation identity.
Anonymous counters contain only time, closed action and saturated count. No raw
request/query/exception text, IP or device data is retained. Global counter and
denial quotas are deliberate loss of diagnostic detail under abuse, not full
failed-login history. Root must separately qualify process and IdP log retention.

## Query And Export Contract

The wire entrypoints are GET /api/v1/activity/security and
GET /api/v1/activity/repositories/{installation_id}/{repository_id}; each has
an /export sibling returning the same bounded JSON envelope as an attachment.
Security queries resolve an omitted issuer from the verified principal; an
explicit issuer must equal that principal's issuer and the configured trusted
issuer. Repository queries never return security events and reject an issuer
parameter. Required since/until UTC instants, at millisecond precision,
bound the interval to 31 days.
Optional actor/action filters are closed and bounded. Page size is 1..100.
Results are descending source sequence, with a signed continuation binding the
entire query, principal actor, authority profile and an expiring upper watermark.
Changing filters, scope, actor, profile or cursor bytes rejects the continuation.
Every continuation repeats current authentication, role and scope checks.

The envelope exposes context, items, nextCursor, retentionSeconds, observedAt and
integrity. It is a filtered activity projection, NOT a verified replay or a
claim of all operations. Business entries expose original audit identity/hash
and idempotency digest as an exact operation reference, not raw payloads.
Issuer and subject unavailable in legacy business evidence stay null.
No arbitrary payload, reason or unrecognized event text is exported.
Export records mean attempted preparation, never proven browser receipt.

Queries have finite statement timeout and concurrency admission. Security
retention is enforced at read time even before physical cleanup; cleanup deletes
at most 128 journal rows and 128 diagnostic buckets per transaction. The existing
maintenance round schedules continued cleanup after its primary startup round.
Physical erasure latency and throughput require native qualification. Business retention
and tamper verification remain with the existing audit ledger/replay owner.

## Decision Costs And Falsifiers

Reusing the business chain for every login would couple deletable identity data
to immutable replay and introduce new event algebra. A dedicated journal adds
one migration and small storage/query adapters but avoids that lifecycle and
proof cost. A blanket middleware/access log is cheaper locally but cannot prove
committed mutations, and adds unbounded secrets/cardinality risk.

Separate source pages avoid a synthetic global ordering across two owners.
Signed keyset pages avoid OFFSET cost and whole-ledger materialization. A shared
short journal write lock establishes sequence/commit order; its contention cost
must be qualified natively. Revisit if measured contention fails the identity
latency budget, retention policy changes, or owners admit a cheaper equivalent
atomic event mechanism. No global optimum or exhaustive architecture verdict
is claimed.

## Writer Readiness

| Changed owner      | Delta and protected operands                                                                                                | Derived surfaces and root gate                                               |
|--------------------|-----------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| identity           | lifecycle labels and optional diagnostics; unchanged principal, expiry, roles, CSRF and cancellation                        | identity unit/HTTP/PG tests; root independent review                         |
| persistence        | atomic session/journal pair and bounded projection; same connection, actual deleted rows, trusted time, scope and retention | migration, schema/ACL/capability attestation, rollback/concurrency witnesses |
| activity contracts | authorized query, bounded page, sealed cursor; principal plus current roles plus exact scope plus every filter              | OpenAPI/TypeScript and independent HTTP boundary witnesses                   |

Readiness is for this bounded source batch only. The integration batch below
owns shared composition and schema/profile/ACL registries; root retains Proofkit
routing and native CI. Independent operand falsifiers:
remove scope check; weaken audit role; mutate cursor scope; fail journal insert;
force rollback; replay logout; cross the retention boundary; inject credentials
into rejected requests; cancel session persistence; insert a foreign audit event.

## Qualification Boundaries

No native test is executed by this lane. External UI/CLI hook completeness,
Keycloak events, Swarm logging, privacy erasure of the existing immutable audit,
native runtime admission and production qualification remain root-owned follow-up.

## Integration Batch Readiness

The integration batch starts at 12555e5599fe7521d6f9076bb508d444d2cc9d11,
preserving archive product 1e08c91415ede2f9eb94301841fa73bb68dab9c0.
It reuses the event-order and information-flow model above. Unlike the original
source-only batch, this writer owns the shared runtime, API, schema, capability,
ACL and fixture integration, plus the minimal cleanup telemetry registration.
Root still owns frontend generation, proof routing and independent native review.

| Owner         | Intended delta and protected observations                                                                                                                         | Independent falsifiers and post-batch gate                                                                                                 |
|---------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| persistence   | generation 12, exact Activity catalog and identity sequence, required capability, direct least-privilege grants; preserve earlier declarations and archive schema | independently alter column, constraint, index, identity sequence or grant; migration and runtime admission must reject before Activity SQL |
| identity      | wire the existing observer, bound optional failures and callbacks; preserve credentials, role decisions, session/journal atomicity and cancellation               | rollback, concurrent logout/replacement, duplicate callback diagnostics, observer exception or timeout                                     |
| HTTP          | mount the four routes through the common registry and admission; preserve authentication challenges and typed no-store failures                                   | absent/expired identity, changed role/scope, wrong method, overload, deadline, malformed query                                             |
| runtime       | domain-separated cursor subkey and one bounded cleanup operation in the existing maintenance round                                                                | restart/key separation, aborted or interrupted cleanup, loss of database authority                                                         |
| query context | omitted security issuer resolves from the verified principal only; explicit issuer still requires its separate authorization                                      | foreign issuer, repository membership without issuer grant, break-glass without issuer, changed continuation scope                         |

For issuer ergonomics, the response adds the admitted source/issuer/repository
context used for that page. This is a display binding, never SQL tenant authority.
The service must still authorize the exact resolved scope before storage on every
page. Deployment trust in the configured issuer is separate from repository
membership and cannot be inferred from it.

Reusing catalog descriptors and existing request/maintenance admission has lower
end-to-end maintenance and proof cost than parallel reflection or scheduling.
The catalog adapter needs only an explicit no-routine variant for independent
Activity relations; existing routine-bearing contracts retain their checks.
This choice is reopened if a native mutation witness survives, the old archive
contract changes, or measured contention violates the existing identity budget.
Ruff and mypy are the local source gate; root's exact-head PostgreSQL/HTTP tests
and independent review are the whole-chain validator. Source readiness does not
assert that those native gates have passed.

## Implemented Coverage And Remaining Hooks

The source batch implements the session store's insert, replacement, local
logout, expiry deletion, profile invalidation and back-channel deletion hooks.
The browser service emits anonymous diagnostics for terminal rejected/unavailable
complete_login outcomes. Shared authenticate_and_admit_roles emits role_denied
only when the concrete ControlPlaneRoleAuthorizer has an observer configured.
Exports through the new activity service emit best-effort attempted diagnostics.
No other read is journaled. Global anonymous diagnostic buckets are not exposed
through issuer-scoped queries: assigning those counters to an arbitrary issuer
would assert authority that was never collected.

The integration closes pre-service callback query/cookie/issuer failures,
login-start rejection/unavailability, and both direct HTTP role checks. Invalid
or replayed back-channel token diagnostics, machine token verification diagnostics,
malformed retained-session deletion and CLI activity beyond existing business
audit remain outside the journal's accountable event classes.
Security operation_ref is a journal-generated effect identity,
not a captured HTTP correlation ID. Linking a callback's request correlation to
its session transition is still a root-owned integration decision.

The business projection currently admits exactly seven pair-owned event types:
config registration, activation, rollback, history configuration, operator
override applied, governance baseline approved and history retention applied.
The retention projection reads the existing committed/replayed pair identity;
it neither copies payloads nor creates another business journal. Other audit types remain
retained by their owners but are not displayed here. Their payload revisions,
legacy issuer/subject and raw operation strings are not reconstructed; exact
audit/idempotency/hash references support owner replay without exporting raw
payloads. This bounded projection does not replace chain verification.

The capability registry now defines ADMINISTRATOR_ACTIVITY, with a compatibility
re-export from activity_capability.py. The existing unpublished generation-12
migration uses the same descriptor and predecessor declarations. Shared result
and runtime admission invoke the independent Activity catalog and identity-sequence
attestor. Runtime provisioning grants journal SELECT/INSERT/DELETE, bucket
SELECT/INSERT/DELETE plus column-only UPDATE(count), and sequence USAGE. Journal
UPDATE and sequence SELECT/UPDATE remain forbidden. Apply the forward migration
and the existing drained runtime-principal provisioning before rollout; no service
uses the migration principal and this batch performs no deployment.

schema.py imports both tables; the HTTP dependency registry mounts all four
routes. Runtime composes a stable domain-separated subkey, exact repository
adapter, domain-owned trusted-issuer authorization, browser/role observer and
PostgresActivityStore.cleanup. Shared fixtures reset both tables. All four HTTP
routes share four concurrent requests and a five-second ceiling inside the global
deadline. The store caps reads at four/three seconds and diagnostics at two/half
a second. Optional observer calls also have a half-second cooperative deadline,
contain ordinary exceptions and propagate caller cancellation. Lock contention
is limited to 100ms. SQL and JSON bounds are not performance qualification.

## Finite Hook Inventory

| Boundary                                             | Event and exact outcome owner                                               |
|------------------------------------------------------|-----------------------------------------------------------------------------|
| session replace insert                               | login; same transaction as the inserted session                             |
| previous-session and capacity eviction               | replaced; only rows actually deleted                                        |
| local logout                                         | logout; actual deletion, no event on repeated absent handle                 |
| load/replace expiry cleanup                          | expired; observed deletion, not an invented expiry timer                    |
| browser profile/issuer/restoration invalidation      | revoked; valid retained identity deletion                                   |
| admitted back-channel logout                         | revoked; replay consumption and actual deletion in one transaction          |
| complete_login terminal rejected/unavailable         | anonymous hourly login_rejected/login_unavailable counter                   |
| callback rejected before service                     | anonymous login_rejected once; no service invocation or duplicate hook      |
| login start rejected/unavailable                     | anonymous login_rejected/login_unavailable counter                          |
| shared role admission, operator page, override route | role_denied for the verified principal only on a role denial                |
| Activity export                                      | attempted page preparation; never delivery acknowledgement                  |
| expired journal/buckets                              | bounded physical deletion in existing maintenance; no recursive audit event |

Rate-limit rejection before routing, scope denial rather than role denial,
invalid/replayed back-channel diagnostics, failed machine verification, malformed
session identity and external CLI/IdP actions do not acquire invented actors or
events. A cancelled/uncertain request is not itself a committed action.

## Frontend Contract

GET routes and operation IDs:

| Route                                                                  | Operation ID               |
|------------------------------------------------------------------------|----------------------------|
| /api/v1/activity/security                                              | query_security_activity    |
| /api/v1/activity/security/export                                       | export_security_activity   |
| /api/v1/activity/repositories/{installation_id}/{repository_id}        | query_repository_activity  |
| /api/v1/activity/repositories/{installation_id}/{repository_id}/export | export_repository_activity |

Query: required since/until UTC RFC3339 strings at millisecond precision, positive
window <=31 days; optional issuer, actor, action, limit (decimal 1..100, default
50), cursor (<=1024 characters). Repository IDs are positive safe integers.
Unknown or duplicate parameters fail. Omit issuer for the normal security view.

Response: context {source, issuer, installationId, repositoryId}, items,
nextCursor (nullable), observedAt, retentionSeconds (2592000 for security, null
for business), integrity (journal_transaction or audit_reference_only). Context
source is security or business; unused scope fields are null. Each item contains
sequence, source, action, outcome, occurredAt, actor, issuer, subject, operationRef,
auditEventId and eventHash; unavailable identity/reference fields are null.
Exports return the same bounded JSON with attachment filename activity.json.

Each page requires the existing exclusive bearer-or-session authentication,
current audit role, and explicit exact scope authorization; break-glass is denied.
Security issuer authorization is independent of repository access. Error bodies
are {ok:false,error}: 400 invalid_request, 401 unauthenticated with WWW-Authenticate,
403 forbidden, 503 unavailable. Successful and admitted error responses are
no-store. A continuation repeats all authorization and binds resolved query,
actor and authority profile; it expires in at most five minutes. Root regenerates
OpenAPI/TypeScript from these integrated sources before frontend qualification.

## Retained-Session Attribution Repair

The root-adjudicated repair batch starts at
`a4c6cea1ca1bc2ab653e5d6734b0d455038731c6`. This design and its companion plan
are additions relative to the intended base `7302ea47`, not historical payloads.
The batch reuses the event-order and information-flow model above. It is material
because retained identity admission, deletion counts, atomic audit and Python
memory are protected operands; this is not a new identity policy or full audit.

As-is, current load decodes the complete retained row, but expired load and the
common deletion CTE copy identity columns without admission. Schema-valid foreign
actor coordinates can therefore produce false committed attribution. Intended
delta: let S be the original handle selection, D the rows actually returned by
deleting selected handles, V the members of D admitted by the existing complete
retained-session decoder, and J the new deletion events. On commit:

```text
deleted_count = |D|; J = projection(V, action, database_time, operation_ref)
malformed(D) contributes no event; every member of V contributes exactly one
required journal failure or cancellation => no session, replay or journal commit
```

These operands are independent: selection is not deletion, schema validity is not
identity admission, and attribution count is not deletion count. Authentication
of a current valid record is unchanged; invalid current/expired load returns no
principal. Cleanup retains its original ordered 128-row selection. Replacement
retains the original newest-seven survivors plus the inserted session. Logout
replay, issuer/subject/sid conjunction, roles, lifetime, time guards, caller
cancellation and required journal failure algebra remain unchanged.

Move the existing complete row decoder and its role-bit codec into the narrowly
owned `control_plane_session_codec.py`, shared by load and deletion. It still
constructs `ControlPlaneSessionRecord`; no independent identity/role checker is
introduced. Stream one SELECT of the original DELETE's handle predicate and
process at most 128 handles per partition. For each partition, DELETE by those
handles with full-row RETURNING, count actual rows, admit each row, then insert
only valid projections in the same transaction. Use one operation reference per
deletion call. Do not reevaluate the limited/offset selection after each delete.

The pinned SQLAlchemy 2.0.52 `AsyncConnection.stream` supports a closing async
context manager and per-execution `yield_per`; its psycopg server cursor delegates
bounded fetches rather than buffering the entire result. Psycopg 3.3.5 uses
DECLARE/FETCH for a SELECT cursor. DELETE RETURNING remains client-buffered, but
its primary-key input is limited to 128 handles. Cursor options are passed per
execution, not installed on the shared connection. Supporting contracts:
[SQLAlchemy streaming](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncConnection.stream),
[Psycopg cursors](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#server-side-cursors),
and [PostgreSQL DECLARE](https://www.postgresql.org/docs/18/sql-declare.html).
The cursor keeps the original selection snapshot, while each bounded DELETE
provides actual deletion evidence. Existing transaction locks remain in place.
Application memory is O(128 * bounded_row_size); no driver-streaming claim is
made for DML/CTEs and no database execution-cost bound is inferred.

The cheaper whole-result CTE cannot invoke the retained-record decoder; fetching
all returned rows violates the memory constraint. Repeated limited DELETEs alter
selection and cleanup semantics. The chosen approach adds one codec dependency
and bounded SQL round trips, avoiding duplicated identity rules and per-session
service/attestation calls. Reopen if native multi-partition selection, cancellation
or rollback falsifiers survive, driver behavior differs, or measured latency
violates the existing owner budget. No global optimum is claimed.

Writer readiness for this cohesive batch:

| Owner                       | Delta and protected observations                                                                                          | Derived surfaces, causal falsifiers and independent gate                                                                                                                          |
|-----------------------------|---------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| retained-session codec/load | relocate the existing full decoder and role representation; preserve all fields, checks and current malformed-row cleanup | native valid/current/expired rows and schema-valid foreign actor or noncanonical issuer; root independently compares old/new admission                                            |
| activity deletion           | original S, actual D, admitted V and exact J; fixed partitions, shared transaction/locks and operation reference          | mixed rows beyond one partition, exact counts/survivors, skipped valid event, reevaluated 128 limit/offset, later journal failure and cancellation; root native PostgreSQL replay |
| diagnostic witnesses        | no production delta; fixed global action/hour bucket, saturating count, principal and action/outcome                      | prepared counts 126/127/128/999999/1000000; transitions to 127/128/129/1000000, exact attributed count and denied/attempted outcome; root native PostgreSQL replay                |

Only source/test and these two document appendices belong to this writer. Root
owns generated routing/traceability and independent exact-integrated-head native
qualification under AGENTS.md. Static Ruff/mypy cannot close native acceptance,
performance, corruption provenance, deployment or production predicates.
## Exceptional Stream Cleanup

Native PostgreSQL qualification exposed a server-cursor resource warning after
both late SQL failure and cancellation. In the pinned SQLAlchemy 2.0.52 source,
the stream context generator closes its result in the normal-completion branch,
not after an arbitrary exception thrown through the context. Use the public
awaitable stream API with an explicit `finally: await result.close()` for this
owned result. Keep the single selection, bounded partitions and outer atomic
transaction unchanged. No driver internals, suppression of resource warnings or
new streaming abstraction is needed. The existing late-failure/cancellation
PostgreSQL scenarios must preserve all rows and replay markers without warnings.
This local repair does not claim arbitrary driver or repeated-cancellation
correctness. Revisit it when upstream cleanup semantics or transaction ownership
changes.
