# Persistence Module Specification

Status: module specification

Date: 2026-07-19

## 1. Owned Invariant

Durable uniqueness, compare-and-swap transitions, transactions, and migrations
are enforced by storage adapters without owning domain proof predicates.

```text
Domain -> public immutable values and ports -> persistence adapter
Domain -/-> persistence
```

An adapter may consume only the public immutable value algebra of the port it
implements. This includes the `reconciliation` and `shadow_mode` contracts;
neither domain may import persistence. The import-boundary witness enforces
both directions, so adding a PostgreSQL codec cannot create a domain-to-SQL
dependency cycle.

Cross-version schema admission is owned by
`cross-cutting/database-compatibility.md` and its machine profile. Persistence
implements that protocol but does not redefine its transition algebra, fence
identity, or rollout authority.

## 2. Public API

```text
UnitOfWork.begin() -> UnitOfWork
uow.commit()
uow.rollback()
repositories.config_epochs
repositories.audit_events
repositories.plans
repositories.observations
repositories.capacity
repositories.overrides
repositories.production_admissions
governance_baselines.load_active/resolve_operation/accept
control_plane_sessions.create/load/delete/logout
repository_attestations.create/consume
```

## 3. Storage Table Ownership

The pre-release migration graph contains one immutable bootstrap revision.
Product support begins with the first release, so a fresh database reaches the
entire current schema without historical compatibility hops.

```text
audit_events
audit_ledger_head
database_compatibility_declarations
database_compatibility_capabilities
config_epochs
active_config_epochs
config_epoch_activations
workflow_proposal_reviews
governance_baselines
governance_baseline_operations
control_plane_sessions
control_plane_logout_replays
repository_attestation_transactions
webhook_deliveries
production_admission_authorities
production_admission_scope_bindings
issued_plan_envelopes
shadow_evidence
reconciliation_subjects
reconciliation_observations
reconciliation_results
operator_overrides
```

The audit ledger does not replace query tables. Query tables do not replace the
ledger.

The audit adapter admits only values bounded by
`docs/specs/ci-coordinator-core/audit-persistence-byte-profile.v1.json`.
Persistence mirrors that audit-owned profile through exact `octet_length`
constraints but does not own or tune its constants.

### 3.1 Reconciliation convergence storage

`reconciliation_subjects` retains the immutable subject and contract together
with one bounded convergence schedule:

```text
created_at, deadline_at, next_attempt_at
attempt_count, max_attempts
backoff_seconds, max_backoff_seconds
claim_generation, lease_token, lease_acquired_at, lease_expires_at
revision
```

The first-release baseline migration creates this final shape directly; there
is no historical compatibility revision. Domain constructors, the canonical
row codec, the machine-readable shadow-reconciliation profile, DDL constraints,
schema attestation, and runtime-principal attestation describe the same fields
and bounds. Readiness rejects any drift in type, nullability, constraint,
index, ownership, or column privilege.

Every retained observation must name the subject digest, exact
`workflow_run_id + run_attempt`, and positive JSON-safe `provider_job_id`. The
write codec rejects a foreign run or attempt before SQL; the read codec
independently rejects canonically encoded and correctly hashed foreign identity
before constructing a snapshot. Therefore storage corruption cannot revive
cross-run or cross-attempt selection semantics.

The runtime principal may select and insert every reconciliation subject field,
but may update only the mutable convergence and revision columns. It cannot
rewrite subject identity, contract bytes, creation/deadline bounds, or attempt
and backoff maxima.

Claim acquisition is a two-part database proof in one transaction:

```text
Eligible(s, now)
  iff no result exists for CurrentRevision(s)
      and next_attempt_at <= now
      and (lease_token is null or lease_expires_at <= now)

select first Eligible by (next_attempt_at, subject_id)
  FOR UPDATE OF reconciliation_subjects SKIP LOCKED
then CAS claim_generation and write attempt/generation/token/expiry
```

The due index is exactly `(next_attempt_at, subject_id)`. The row lock excludes
simultaneous acquisition; the retained lease excludes later acquisition while
active; token and generation fence every post-poll mutation. Provider I/O is
outside the transaction. Observation persistence atomically inserts the
observation and advances revision. Terminal persistence atomically inserts the
result, clears the lease, appends the audit event, and records any derived
shadow evidence. A conflict in any member rolls back the complete effect.
The lease bound is measured from `lease_acquired_at`; measuring it from the
subject deadline would make terminal claims impossible after a sufficiently
long service outage.

`reconciliation_results` admits only terminal results. Incomplete provider
state is represented by the subject's next due time, never by a durable pending
result. This makes unresolved work queryable without conflating scheduler state
with terminal evidence.

### 3.2 Production authority and selected issuance

`production_admission_authorities` retains one immutable verified authority:
its content-derived identity, signing-key identity, public-key SPKI DER, exact
canonical envelope bytes, issuance time, and expiry. The child
`production_admission_scope_bindings` relation retains the unique canonical
repository coordinates, admission-subject digest, config epoch, and target
registry hash covered by that authority. Foreign keys make orphaned scope
bindings and selected plans unrepresentable.

Startup registration is insert-only and exact-replay idempotent. It uses the
database clock, rejects an expired authority, inserts the parent and every
scope binding, then reads the complete retained projection back before commit.
A same-identity byte or scope mismatch rolls back the transaction.

Selected issuance is a separate request transaction. Under the same
repository-scope advisory lock used by config activation and operator controls,
it proves all of the following before insertion:

```text
RegisteredAuthority(authorityId, expiry, scope, subject, epoch, registry)
and DatabaseClock < expiry
and ActiveEpoch(scope) = epoch
and not ActiveSubjectForceFullCI(scope, reconciliationSubject)
and LatestOmissionControl(scope) != disable_omission
```

The selected envelope then stores the non-null authority foreign key. FullCI
stores null. The signed payload, persistence codec, column shape constraint,
foreign key, and read codec independently enforce the same distinction.

This transaction owns only durable linearization. Cryptographic signature and
candidate-policy matching remain in `production_admission`; config transition
policy remains in `config_epochs`; override transition policy remains in
`operator_controls`.

### 3.3 Audit persistence

```text
PersistableAuditInputV1(x)
  -> prepare before connection checkout
  -> retain exact canonical payload bytes and hashes
  -> begin UnitOfWork
  -> append without application-layer payload traversal or recanonicalization
     under the ledger-head lock
```

Read admission checks raw column lengths before decode, parse, or hashing.
Readiness attests the exact enforced and validated constraints,
`STORAGE EXTENDED`, and a TOAST relation of the expected relation kind. These
facts contribute to representability under the admitted schema; they do not
prove capacity, actual out-of-line placement, or performance.

```text
RuntimePersistable(x) = PersistableAuditRecordV1(x)
DdlLengthBackstop(row) = every profiled BYTEA column is non-empty and within
  its profile-projected octet interval
SchemaRepresentable iff RuntimePersistable(x) implies DdlLengthBackstop(row(x))
```

The converse is intentionally false. Length CHECKs cannot validate UTF-8,
canonical JSON, hashes, or event semantics; the bounded read codec owns those
checks.

### 3.4 Proposal review registration

`workflow_proposal_reviews` is an immutable query relation for one affirmative,
non-activating proposal review. Its primary key binds repository scope and
operation id; separate unique constraints bind one manifest per scope and one
pair-owned audit event per review. Foreign keys bind both the reviewed baseline
epoch when present and the registered target epoch.

The proposal-review unit of work requires the compatibility, audit-ledger,
config-epoch, and proposal-review capabilities together. Under the same
repository-scope transaction lock used by activation, it rechecks the complete
active pointer, registers the exact target epoch, appends the pair-owned audit
event, and inserts the review row. The adapter commits all three effects or
none. It never writes `active_config_epochs`.

The runtime principal has only `SELECT` and `INSERT` on the review relation.
The migration attestor verifies static columns, constraints, foreign keys,
storage, trigger, routine, ownership, object ACLs, and PUBLIC absence before
publishing the capability. Each proposal-review transaction separately
attests the exact session principal and effective grant set before exposing the
repository. Either failed plane rejects independently.
The complete semantic protocol is owned by
[`proposal-review-registration.md`](proposal-review-registration.md).

### 3.5 Control-plane sessions and reviewer transactions

`control_plane_sessions` retains only a SHA-256 digest of the opaque handle,
immutable Keycloak issuer/subject/session identity, canonical actor id, bounded
roles and display metadata, authority-profile digest, and issue/expiry times.
It contains no Keycloak token, refresh token, browser secret, or GitHub token.

Create performs bounded expired-row cleanup and enforces at most eight retained
sessions per Keycloak identity. Lookup and the final guarded insert use database
statement time after compatibility-fence admission; transaction-start time
cannot preserve authority across a fence wait that crosses expiry. An addressed
expired or malformed row is deleted best-effort and never authenticates. Local
logout is idempotent. `control_plane_logout_replays` retains bounded
back-channel logout replay identities only until no admitted clock
interpretation can accept the token.

`repository_attestation_transactions` retains one pending, five-minute,
proposal-bound reviewer transaction. It binds the digest of an encrypted
one-use browser cookie to the initiating session, repository scope, operation,
proposal, active baseline, actor, authority profile, and durable time interval.
Registration serializes both the repository authority scope and the exact
session capacity domain, so concurrent starts in different repositories cannot
exceed the eight-pending-transaction limit for one session.
The OAuth verifier and token remain outside the database. Callback consumption
deletes the pending row in the same transaction that appends the immutable
proposal-review receipt and its pair-owned audit event.

The runtime principal receives only the exact relation and column grants
declared by the control-plane identity and repository-attestation capabilities.
Its sole control-plane session update grant is column-scoped to
`handle_digest`, exists only because PostgreSQL row locks require update
privilege, and is paired with a trigger that rejects every actual update.
Static schema attestation and per-operation principal/grant admission remain
independent. Cryptographic and lifecycle semantics are owned by
[`control-plane-identity-and-repository-attestation.md`](control-plane-identity-and-repository-attestation.md).

## 4. Failure Behavior

```text
DuplicateDelivery => idempotent read of existing record
ActiveEpochCASConflict => activation rejected
TransactionFailure => no partial activation
MigrationMismatch => readiness fails
StoreUnavailable => readiness fails; bootstrap uses FullCI fallback
```

### 4.1 Schema compatibility

Mutating and ordinary schema-dependent transactions use capability admission rather than assuming
that Alembic-head equality alone proves compatibility:

```text
SchemaTransactionAdmitted(required) iff
  READ COMMITTED is configured before begin and verified after begin
  shared compatibility fence is held
  and current Alembic head has one valid append-only declaration
  and required subset-of declaration.providedCapabilities
  and code-owned schema, seeded-data-domain, routine, role, default-privilege,
      and effective-privilege attestation passes
```

The shared fence is acquired before the current head is read and before any
schema-dependent repository is exposed. The revision read is a separate next
statement under enforced `READ COMMITTED`. Online migrations acquire the
matching exclusive transaction fence in `backend/alembic/env.py` before head
inspection, `run_migrations()`, or DDL, then commit DDL, one forward
declaration, and Alembic-head movement atomically. A pre-retention downgrade
selects an existing ancestor without appending a declaration.

The workbench repository configures profile-owned `READ COMMITTED` before begin,
sets the transaction `READ ONLY` and verifies both characteristics. Current
capability admission follows the shared compatibility fence; all five bounded
repository projections and metadata then use one statement snapshot. This
preserves post-fence freshness without a session-lock lifecycle or a snapshot
established by an earlier fence-acquisition SELECT. See
[coherent observation snapshots](../../features/database-observation-snapshots.md).

Readiness evaluates the same owner predicate for startup visibility, but it is
not a lease. Every schema-dependent transaction repeats current-revision
admission under the fence. A cached attestation is keyed by revision,
declaration hash, and required capabilities; it cannot replace the current-head
read.

Config-registration pair integrity has two distinct proof planes. Migration
admission verifies the complete two-way relation between registrations and
their pair-owned audit events while holding the exclusive compatibility fence;
any future explicit deep-integrity caller must hold an equivalent fence.
Ordinary runtime transactions do not rescan that unbounded history. They
preserve it inductively: a new pair is appended atomically under the
repository-scope lock, both histories are immutable to the runtime role, and
replay verifies the exact retained event's derived hashes and input identity by
point lookup. A migration-principal write invalidates this induction and
requires a fresh deep qualification before release.

Multi-statement readiness uses `READ COMMITTED` and the shared compatibility
fence. It verifies immutable bounded prefixes without blocking ordinary audit
writers. The final head and maximum sequence belong to one statement snapshot;
normal advancement is progress, not corruption. The fence excludes migrations,
not concurrent domain appends.

One process-local readiness probe proves the ledger by induction. Its first
successful check validates the complete chain and retains only the exact last
record as a checkpoint. Every later check repeats compatibility, schema, data,
routine, and principal admission under the same fence. An unchanged head then
requires no event-row read. An advanced head loads only records after the
checkpoint and proves contiguous sequence, previous-hash linkage, canonical
record validity, and equality with the observed head. Database uniqueness owns
idempotency across the already verified prefix. Corruption discards the
checkpoint; ordinary progress and transient unavailability retain the verified
prefix according to the probe's failure algebra. The stateless
`check_database_readiness` entry point always starts with complete verification.

This induction relies on the capability-attested immutability and restricted
runtime principal between checks. It does not detect a privileged external
actor that disables those controls, rewrites an old row, and restores the same
head identity between probes; that actor is outside the admitted runtime-writer
model and requires an independent deep-integrity or external attestation
control.

The runtime principal neither owns the application schema nor has schema
`CREATE` or compatibility-declaration mutation privileges. A declaration is
not proof of its own schema claim: capability-specific code attests exact
relations, columns, constraints, indexes, storage facts, ownership, and ACLs.
The attestation also owns retained row domains, codecs, backfill state,
trigger/routine behavior, role membership closure, and default privileges.
Pure capability policy and PostgreSQL catalog fact collection are separate
owners.

An additive capability must not turn an `expand` declaration into an
unannounced binary cutover. A migration may add DDL and a capability declaration
without immediately granting the runtime role. Provider
enforcement applies only named column grants after the relations exist. The
predecessor base attestor rejects a table grant on an unknown relation, whereas
PostgreSQL column grants do not satisfy its table-privilege probe. The new
capability attestor verifies the exact column projection separately. Therefore
the shared runtime role remains admissible for predecessor and base-runtime
transactions, while capability-dependent repositories fail closed until their grants
are present.

Mixed-version data inclusion is enforced by validated PostgreSQL bridge
constraints over retained rows and future writes. Seeded rows are adversarial
examples, not proof of a universal domain relation. A domain that cannot be
represented by the admitted constraint algebra cannot use the rolling
compatibility protocol.

The bootstrap declaration lists the generation-one capabilities and their
descriptor hashes. Later admitted migrations extend or replace that set;
the [compatibility profile](../../specs/ci-coordinator-core/database-compatibility-profile.v1.json)
binds the supported declarations, including source-aware economics in
generation seven. The bootstrap list is not the complete current set.
Future semantic schema changes must append a new migration and declaration;
they may not mutate the released bootstrap. Before the first release, the
current migration graph from an empty database remains the only supported
database origin. A development database that recorded an earlier byte version
under the same revision identifier is deliberately unsupported and must be
recreated; it cannot justify retaining a compatibility hop for a product state
that PA-6 excludes. The first product release freezes every migration included
in that release, after which all schema changes are forward revisions.

Every runtime mutation executes through a task-owned PostgreSQL UnitOfWork:

```text
admit immutable input before connection checkout
  -> open one AsyncConnection and transaction
  -> acquire compatibility fence and attest the current capability revision
  -> classify retained state under the owning lock or CAS predicate
  -> write the domain effect and its audit counterpart
  -> commit exactly once, or roll back the complete transaction
```

The UnitOfWork exposes domain-specific repositories only while active. A
repository cannot commit independently, escape its connection lifetime, or
return SQLAlchemy rows as domain state. Cancellation marks the transaction for
rollback and propagates unchanged. An expected database failure becomes a typed
unavailable result only at the adapter boundary that owns that failure algebra.

Commit-or-rollback finalization has one finite UnitOfWork-owned monotonic
deadline. Caller cancellation is retained while the finalizer is shielded, but
cannot extend that deadline. On expiry the finalizer is cancelled, its eventual
outcome is consumed, the UnitOfWork becomes failed, and the primary exception or
commit classification remains authoritative with a redacted cleanup failure.
This bounds caller progress without claiming that a cancellation-suppressing
driver has already released every external resource at the exact deadline.

Every composite effect defines its completeness predicate over retained rows.
`duplicate` means the entire effect pair already exists and is byte-equivalent;
a proper subset is repaired only when the missing counterpart is uniquely
derivable from retained canonical facts. Otherwise the operation is a conflict
and commits nothing. This prevents an audit row, state row, or delivery claim
from being mistaken for proof of a complete independent effect.

Read codecs admit bounded raw column values before decoding and construct only
detached immutable domain values. Caller mutation therefore cannot alter
retained state, and database rows cannot cross into the planner, verifier, or
application layers.

## 5. Proof Obligations

| Obligation                                                                       | Falsifier                                                                                                                                                        |
|----------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Transaction rollback prevents partial state.                                     | Epoch active but audit event missing.                                                                                                                            |
| Runtime admission is representable by the DDL length backstop.                   | A runtime-admitted event violates a profiled CHECK.                                                                                                              |
| DDL is not promoted to semantic admission.                                       | A length-valid invalid UTF-8 or noncanonical payload is accepted by the read codec.                                                                              |
| Stored violations fail before parsing.                                           | Oversized payload bytes reach UTF-8 decode or `json.loads`.                                                                                                      |
| TOAST feasibility is attested.                                                   | Readiness accepts `STORAGE PLAIN`, a missing/non-TOAST relation, or a non-enforced CHECK.                                                                        |
| Composite effects are atomic.                                                    | A state row or delivery claim commits without its required audit counterpart.                                                                                    |
| Duplicate means complete equivalence.                                            | A proper subset or byte-different pair is reported as duplicate.                                                                                                 |
| UnitOfWork lifecycle is closed.                                                  | A repository executes before entry, after completion, or from a foreign task.                                                                                    |
| UnitOfWork cleanup is bounded.                                                   | Repeated caller cancellation or a stuck driver keeps context exit waiting beyond the owner deadline.                                                             |
| Cancellation cannot commit.                                                      | A cancelled mutation leaves any attempted effect visible.                                                                                                        |
| Read DTOs are detached.                                                          | A SQLAlchemy row or mutable persistence object reaches a domain consumer.                                                                                        |
| Uniqueness is durable.                                                           | Same run identity gets two different selected envelopes.                                                                                                         |
| CAS protects active pointer.                                                     | Concurrent activations both succeed.                                                                                                                             |
| Domain objects are not ORM models.                                               | Planner accepts SQLAlchemy instance.                                                                                                                             |
| Startup readiness is not stale transaction authority.                            | A writer executes after an incompatible migration without re-admission.                                                                                          |
| Repeated readiness is ledger-cardinality independent when the head is unchanged. | A second successful probe reloads the verified event prefix.                                                                                                     |
| Advanced readiness preserves complete-chain meaning by induction.                | A suffix with a gap, wrong predecessor hash, invalid record, or mismatched locked head is admitted.                                                              |
| Compatibility transitions serialize with participants.                           | A migration changes a provided capability while a participant transaction overlaps it.                                                                           |
| Additive revisions preserve old code capabilities.                               | An expand retains an identifier but skips its resulting schema or seeded-data attestation.                                                                       |
| Rollback proof uses the real prior artifact.                                     | Current code, or an artifact without exact predecessor commit and digest identity, is accepted as equivalent evidence.                                           |
| Runtime DDL and declaration mutation are privilege-separated.                    | Runtime or PUBLIC can perform DDL or mutate revision declarations.                                                                                               |
| Supported runtime SQL is cooperative.                                            | A raw engine or session is exported outside the admitted unit-of-work boundary.                                                                                  |
| Reconciliation acquisition is exclusive.                                         | Two transactions acquire different active claims for one subject.                                                                                                |
| Reconciliation ownership is fenced.                                              | A superseded or expired token changes revision, due state, lease state, or result.                                                                               |
| Observation run identity is exact.                                               | A canonically encoded observation from another workflow run or `run_attempt` is admitted into a snapshot.                                                        |
| Reconciliation bounds survive restart.                                           | Restart resets creation, deadline, due time, backoff, attempt count, or maxima.                                                                                  |
| Pending is scheduler state, not evidence.                                        | `reconciliation_results` retains a pending result.                                                                                                               |
| Terminal reconciliation is one composite effect.                                 | Result, lease release, audit, or required shadow evidence commits alone.                                                                                         |
| Reconciliation schema descriptions agree.                                        | Migration, metadata, profile, codec, schema attestation, or principal attestation accepts a different shape.                                                     |
| Proposal review registration is one composite effect.                            | The target epoch, pair-owned audit event, or immutable review row commits without both counterparts.                                                             |
| Proposal review does not activate policy.                                        | A successful review changes `active_config_epochs`.                                                                                                              |
| Control-plane session authority is opaque and bounded.                           | A raw handle or token is stored, statement-time expiry after fence admission authenticates, cleanup is unbounded, or a ninth retained identity session survives. |
| Reviewer transactions are exact and one-use.                                     | A callback consumes a transaction for another session, scope, proposal, baseline, actor, profile, or operation, or two callbacks commit receipts.                |
| Identity and attestation grants are exact.                                       | Runtime or PUBLIC can access undeclared columns or use a relation before schema and principal attestation.                                                       |

## 6. Implementation Mapping

As-built owner groups:

```text
ci_coordinator/persistence/connection.py
ci_coordinator/persistence/unit_of_work.py
ci_coordinator/persistence/*_unit_of_work.py
ci_coordinator/persistence/schema.py
ci_coordinator/persistence/*_schema_attestation.py
ci_coordinator/persistence/*_codec.py
ci_coordinator/persistence/*_repository.py
backend/alembic/
```

Connection lifecycle, compatibility admission, schema attestation, codecs,
transaction state, and domain repositories remain separate owners because they
have independent reasons to change.

Compatibility owners are separately decomposed into contracts, profile loading,
fence acquisition, declaration persistence, capability attestation, and
migration orchestration.

## 7. Acceptance Tests

- Postgres transaction rollback tests.
- paired-effect failure, duplicate, conflict, and retry-repair tests.
- concurrent CAS and uniqueness tests that prove no committed state is lost.
- concurrent audit append and exact-byte replay tests.
- migration readiness tests.
- complete-first, unchanged-head, valid-suffix, and invalid-suffix readiness tests.
- shared/exclusive compatibility-fence interleaving tests.
- exact predecessor-commit and artifact-digest tests across successor revisions.
- proposal-review replay, ABA, rollback, immutability, capability, and
  pre-retention downgrade tests.
- control-plane session create/load/delete/logout, fence-crossing statement-time
  expiry, bounded cleanup, per-identity limit, replay, schema, and ACL tests.
- repository-attestation transaction binding, expiry, concurrent one-use
  consumption, schema, and ACL tests.
- seeded row-domain, codec, backfill, routine, schema-owner, runtime-role,
  migration-role, default-privilege, and PUBLIC privilege drift tests.
- repository adapters return domain records, not ORM models.
