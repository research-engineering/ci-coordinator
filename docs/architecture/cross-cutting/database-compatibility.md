# Database Compatibility Protocol Specification

Status: cross-cutting specification

Date: 2026-07-12

## 1. Decision

Every schema-dependent database transaction participates in one revision and
capability protocol. Application transactions take a shared transaction-level
advisory fence before observing the database revision or executing
schema-dependent SQL. Migration transactions take the corresponding exclusive
fence before compatibility-relevant DDL or declaration changes.

The canonical machine contract is
`docs/specs/ci-coordinator-core/database-compatibility-profile.v1.json`.
This document explains its proof; it does not duplicate profile constants.

## 2. Problem

Startup readiness is necessary but not sufficient. Consider this history:

```text
t0: old writer attests revision r and becomes ready
t1: migration commits incompatible revision r+1
t2: old writer begins a transaction using the contract of r
```

No startup check can reject the operation at `t2` because its evidence predates
the migration. Therefore:

```text
StartupAttestation(writer, r)
does not imply
SafeTransaction(writer, currentRevision)
```

The missing relation is serialization between schema-dependent transactions
and migrations. PostgreSQL transaction-level advisory locks supply exactly the
needed lifetime: shared locks coexist with application transactions, an
exclusive lock excludes them during a migration, and every lock is released by
transaction completion.

## 3. Authority Boundaries

| Decision                     | Owner                                      | Rejected substitute                                   |
|------------------------------|--------------------------------------------|-------------------------------------------------------|
| current migration head       | database `alembic_version` under the fence | deployment label or cached startup value              |
| revision transition law      | compatibility profile                      | migration prose                                       |
| provided capabilities        | append-only revision declaration           | application version comparison                        |
| operation requirements       | schema-dependent operation contract        | use-case narrowing or database guessing from SQL text |
| schema sufficiency           | code-owned capability attestation          | declaration self-assertion                            |
| DDL and declaration mutation | migration principal                        | runtime service principal                             |
| production rollout approval  | platform/deployment authority              | a valid declaration shape                             |

The database declares what the current revision provides. Each operation
contract owns one immutable non-empty required set. A use case may union the
sets of the operations it invokes but cannot narrow any set. Admission is set
inclusion, not an ordering comparison over release versions:

```text
Admitted(operation, revision) iff
  RequiredCapabilityIds(operation) subset-of ProvidedCapabilityIds(revision)
  and each required descriptor hash exactly equals its declaration row
  and CodeOwnedSchemaDataPrivilegeAttestation(operation, revision) = passed
```

Version ranges are rejected because a higher revision is not necessarily a
superset of behavior. Capability identifiers name semantic contracts directly.
One identifier has immutable schema, data-domain, codec, backfill, routine, and
privilege meaning across artifacts; changing any coordinate requires a new
identifier. Otherwise two binaries could admit the same identifier while
implementing incompatible contracts.

Each declaration capability row binds its identifier to a canonical descriptor
hash over schema, data-domain, codec, backfill, routine, and privilege
requirements. The revision hash includes the sorted `(capabilityId,
descriptorHash)` pairs. Therefore silently changing an identifier's meaning is
both an admission mismatch and a declaration-content mismatch.

## 4. Transaction Protocol

For every mutating or ordinary schema-dependent operation:

```text
configure READ COMMITTED before transaction begin
begin transaction
verify transaction_isolation in the first statement
set transaction-local lock and statement timeouts
acquire shared compatibility fence
in a separate next statement, read exactly one Alembic head
load and validate its immutable declaration
prove required capabilities are provided
attest code-owned schema and privileges on cache miss
expose repositories or execute schema-dependent SQL
commit or roll back
```

No repository is exposed before admission. Ordinary read-only schema-dependent
transactions follow the same law: a contract migration can invalidate a query
shape as well as a write shape.

`READ COMMITTED` is configured on the SQLAlchemy connection before transaction
begin and verified before timeout or fence SQL. The fence statement and
revision-read statement must then be separate. If a client establishes a
`REPEATABLE READ` snapshot before
waiting for the fence, it can retain a pre-migration snapshot after the
migration commits. The protocol therefore neither combines lock acquisition
and declaration reading into one statement nor permits an earlier
schema-dependent statement to establish the transaction snapshot.

A bounded workbench projection uses the same profile-owned `READ COMMITTED`
mode, with the following additional read-only and coherence constraints:

```text
configure READ COMMITTED before transaction begin
begin transaction and set READ ONLY
verify isolation and read-only state using transaction-characteristic statements
acquire the shared compatibility fence
admit every capability needed by the complete projection
execute all jointly interpreted projections and metadata in one bounded statement
```

Timeout configuration and advisory acquisition may establish earlier statement
snapshots. READ COMMITTED does not retain them for the subsequent admission or
projection. The exclusive migration fence cannot overlap the admitted interval;
all five sections use one later statement snapshot, while ordinary domain
writers remain unblocked. A separate statement per section would violate this
coherence rule. See the [snapshot decision](../../features/database-observation-snapshots.md).

The profile owns positive participant and migration timeout values satisfying
`0 < lock_timeout < statement_timeout < transaction_timeout`. PostgreSQL value
`0` disables these guards and is rejected before lock acquisition.

Every online forward migration runs through `backend/alembic/env.py`:

```text
begin outer migration transaction
set transaction-local lock and statement timeouts
acquire exclusive compatibility fence
validate the current head and declaration
apply one admitted bootstrap, expand, or contract transition
attest every resulting capability against resulting schema, data, and privileges
if revision is new, append exactly one successor declaration
if revision is known, byte-exactly validate it and append nothing
atomically commit DDL, declaration, and Alembic head
```

The exclusive fence is acquired at the online environment boundary before
head inspection, `context.run_migrations()`, revision DDL, or declaration
writes. Alembic offline execution, autocommit blocks, concurrent index DDL, and
revision-local transaction boundaries are inadmissible because they cannot
atomically couple DDL, declaration, and head movement.

A pre-retention downgrade is a different operation:

```text
acquire the exclusive fence at the online environment boundary
select an already declared ancestor
byte-exactly validate its declaration and attest its resulting state
move DDL and Alembic head atomically without appending history
```

All migrations use the fence. Restricting it only to migrations currently
believed to be breaking is unsafe because that classification is precisely a
fact the protocol must validate.

The protocol bootstrap is the sole exception to the successor wording: it
creates generation 1 under the same exclusive fence. Compatibility migrations
must be fully transactional. A PostgreSQL operation that cannot run in the
same transaction as declaration and Alembic-head movement requires a separate
protocol and is inadmissible here.

## 5. Revision Chain

Each revision declaration is append-only and contains the profile-owned lineage,
a contiguous generation, exact Alembic revision and parent, transition kind,
protocol version, content-derived declaration hash, and normalized
provided-capability set.

Runtime admission reads only the current declaration and its exact parent.
Bootstrap is the induction base; each first application proves one successor,
while database uniqueness, parent foreign-key, generation, and immutability
constraints preserve continuity. The retained history is not given a finite
semantic maximum and is never loaded into one runtime transaction.

```text
Bootstrap(next) iff
  next.generation = 1
  and next.parent = null
  and next.capabilities is non-empty

Successor(previous, next) iff
  next.generation = previous.generation + 1
  and next.lineage = previous.lineage = admitted lineage
  and next.parent = previous.revision

Expand(previous, next) iff
  Successor(previous, next)
  and previous.capabilities proper-subset next.capabilities

Contract(previous, next) iff
  Successor(previous, next)
  and next.capabilities proper-subset previous.capabilities
```

A no-op successor and a transition that both adds and removes capabilities are
invalid. When an intermediate state can satisfy all retained schema and data
contracts, the change decomposes into an expand followed by a contract and the
bridge makes rollout and rollback obligations explicit. If no such bridge is
realizable, the transition is not relabeled as expand/contract: it requires an
independently admitted maintenance or destructive-data protocol.

Declarations are retained across ordinary downgrades. Re-upgrading an already
declared revision is idempotent only when the proposed declaration is exactly
equivalent. A conflicting replay is corruption, not an update.

The declaration hash gives stable content identity and cache binding; it is not
a signature and does not create trust against the migration owner. Relational
generation and parent constraints own ancestry continuity.

## 6. Concurrency Proof

Let `A` be an application transaction taking the shared fence and `M` a
migration taking the exclusive fence.

Exactly one of these lock orders exists:

```text
A < M:
  A admits revision r and completes before M can alter r.

M < A:
  M commits or rolls back before A acquires the fence.
  A then observes the committed head and either admits it or fails closed.
```

Thus no admitted application transaction overlaps compatibility-changing DDL.
The proof depends on every participating client acquiring the same profile-owned
key before any schema-dependent statement. Advisory locks are cooperative;
the supported runtime architecture exposes repositories only through the
compatibility-admitted unit of work. Database privileges prevent runtime DDL
and declaration mutation, but cannot force an arbitrary SQL client to acquire
an advisory lock. Non-cooperating clients, superusers, and migration-owner
bypass remain explicit non-claims.

Timeout, missing head, multiple heads, missing declaration, malformed chain,
unknown lineage or protocol version, capability absence, schema mismatch, and
privilege mismatch all reject before repository exposure.

## 7. Capability Attestation

A database declaration cannot prove its own truth. Each capability therefore
has a code-owned attestation over the PostgreSQL facts required by that
capability:

```text
relations and persistence kind
columns, types, nullability, and defaults
constraints and validation state
correctness-critical indexes
storage properties required for representability
retained row read and write domains
codec and backfill state
trigger, policy, and routine behavior
capability-owned runtime privilege requirement identifiers
per-session exact runtime grants before repository exposure
migration ownership and object ACL privileges
role attributes and membership closure
default privileges
absence of PUBLIC privileges on schemas, relations, sequences, routines,
types, and domains
```

These facts belong to two non-interchangeable proof planes. Let
`A_static(C, r)` mean that revision `r` proves capability `C` against schema,
data, codec, routine, ownership, object-ACL, migration-authority, default-ACL,
and PUBLIC facts. Let `A_runtime(C, s)` mean that runtime session `s` is the
exact restricted direct-login principal and has exactly the grants denoted by
`C.privilegeRequirements`.

```text
Publish(C, r) iff transition-valid(C, r) and A_static(C, r)
ExposeRepository(C, r, s) iff Declared(C, r) and A_static(C, r)
                                  and A_runtime(C, s)
```

The migration cannot prove a role that is intentionally provisioned after old
participants drain, while the runtime declaration cannot prove its own live
grants. Conflating the planes therefore permits either a false declaration or
an unavailable rolling transition. Keeping both conjuncts makes a published
capability necessary but insufficient for runtime exposure: missing, stale,
or excessive runtime grants fail closed after the shared fence and before a
repository is constructed.

PostgreSQL grants PUBLIC access to new routines and types by default, while a
per-schema default cannot revoke a global grant and type defaults do not govern
the implicit composite row type created with a table. CI Coordinator therefore
does not mutate role-global defaults: that would affect unrelated schemas and a
downgrade could not reconstruct an unknown prior role state. Each migration
revokes PUBLIC access from every resulting routine and type in the same
transaction and passes whole-schema object and schema-local default-ACL
attestation before publishing its declaration. PUBLIC has no schema access
during that transaction, so an unadmitted intermediate object is not exposed.

Automatically generated array types are not independent ACL owners:
PostgreSQL rejects direct privilege changes on them and derives their effective
`USAGE` from the element type. Therefore `typelem = 0` is the complete set for
raw type-ACL revocation and attestation, while runtime-principal attestation
checks effective `has_type_privilege` over both element and array types.
Interpreting a null array `typacl` through `acldefault('T', owner)` would report
a privilege that PostgreSQL does not independently grant and is not a valid
security oracle.

A positive result may be cached only by
`(revisionId, declarationHash, requiredCapabilities)`. The transaction must
still acquire the shared fence and read the current declaration before using
that cache key. A startup cache without a transaction check recreates the race
from Section 2.

An additive DDL label is not compatibility proof. New disjoint objects outside
the closure of every retained capability can be compatible. A new column,
constraint, trigger, policy, index, storage fact, or privilege on an object
already owned by an old capability must be evaluated by a new exact capability
contract. For example, a `NOT NULL` column without a compatible insert
projection can break an old writer despite being syntactically an addition.
Likewise, a schema-compatible column rewrite can be data-incompatible if old
rows fall outside a retained reader domain or mixed-version writes fall outside
a retained writer or reader domain. Empty-database tests cannot prove this
property. Before mixed deployment, a validated PostgreSQL bridge constraint
must enforce that every retained row and every future write lies in every
retained reader domain. A domain that cannot be expressed in the admitted
database constraint algebra is outside the cooperative rolling protocol and requires a separately
admitted drained or maintenance protocol. Seeded boundary rows and explicit
backfill states remain falsifiers; finite examples are never universal proof.

`schema_capabilities.py` owns pure capability requirements and performs no SQL.
`schema_attestation.py` owns bounded catalog and effective-privilege fact reads
and contains no capability policy. `data_attestation.py` attests validated
bridge constraints and executes bounded capability-owned seeded-row and
backfill falsifiers without defining their policy.
This split gives each fact and predicate one owner and makes policy tests
independent of PostgreSQL transport.

## 8. Rollout And Rollback

An expand transition adds capabilities and retains every previous identifier.
It is admissible only when every capability in the resulting declaration
attests against the resulting schema and seeded data state. Set inclusion
without resulting attestation is insufficient: a migration can preserve
identifier `A` while
destroying the column, row domain, codec, or privilege that gives `A` meaning.

A contract transition removes capabilities and adds none. It may run only after
deployment authority has independently established that removing those
capabilities is allowed. The exclusive fence drains in-flight participating
transactions; any later old operation acquires the shared fence, observes the
new declaration, and fails before schema-dependent SQL.

Code rollback is admissible only when the current declaration provides every
capability required by the rollback target and that target's attestation passes.
The fact that a binary previously ran against the database is not proof that it
can run after a later contract transition.

Rollback proof executes the actual pinned predecessor artifact, identified by
its source commit and artifact digest, against the migrated database. Running
current code with an `oldVersion` parameter proves only a branch in current
code; building current source before a test-owned migration proves only that
specific transition. Neither proves behavior of a predecessor release.

Before a predecessor release exists, tests may prove the rollback mechanism
with an immutable test artifact across test-owned transitions. This is not
predecessor-release evidence. The first successor schema change must pin the
released predecessor source commit and artifact digest before claiming
backward-code rollback.

Every supported schema-dependent client must participate in the compatibility
fence. A non-participating client cannot share the runtime principal during a
rolling transition.

## 9. Failure Algebra

The implementation exposes stable typed compatibility failures, not PostgreSQL
or Alembic exception text:

```text
database_compatibility_fence_timeout
database_query_cancelled
database_compatibility_transaction_timeout
database_compatibility_outcome_unknown
database_isolation_mismatch
database_migration_head_invalid
database_declaration_missing
database_declaration_invalid
database_declaration_bounds_exceeded
database_capability_unavailable
database_schema_capability_mismatch
database_data_capability_mismatch
database_privilege_mismatch
database_compatibility_store_unavailable
```

Commit cancellation or transport loss after a migration or application commit
retains the existing unknown-outcome semantics. Compatibility admission does
not convert an unknown commit into a known rollback. PostgreSQL SQLSTATE `57014`
means `query_canceled`, not specifically `statement_timeout`; it maps to the
generic cancellation failure unless the caller owns independent provenance.
Caller task cancellation remains observable and is not rewritten as a timeout.

## 10. Proof Obligations

| Obligation                                       | Minimal falsifier                                                                                 |
|--------------------------------------------------|---------------------------------------------------------------------------------------------------|
| readiness cannot become stale authority          | migration commits after readiness and old transaction executes without revalidation               |
| migration and participant operations are ordered | both hold conflicting fence modes concurrently                                                    |
| post-fence admission observes current state      | a snapshot established before waiting on the fence is reused                                      |
| expand preserves rollback-capable code           | previous capability disappears in an expand declaration                                           |
| retained capability still means the same thing   | retained schema, seeded row domain, codec, backfill, routine, or privilege attestation is skipped |
| contract is review-atomic                        | one transition both adds and removes capabilities                                                 |
| downgrade does not fork forward history          | downgrade appends a second declaration for an existing ancestor                                   |
| timeouts preserve bounded waiting                | zero or unordered timeout values are admitted                                                     |
| revision history is continuous                   | generation gap or parent fork is admitted                                                         |
| declarations are immutable                       | update, delete, or conflicting replay succeeds                                                    |
| declaration is not schema proof                  | declared capability is admitted after its required column or privilege is removed                 |
| cache does not bypass current state              | attestation for revision `r` is reused under revision `r+1`                                       |
| runtime cannot bypass governance                 | runtime principal can execute DDL or mutate declarations                                          |
| rollback is current-state based                  | old binary starts because it once supported an ancestor revision                                  |

## 11. References

- PostgreSQL 18 explicit locking and advisory-lock semantics:
  <https://www.postgresql.org/docs/18/explicit-locking.html>
- PostgreSQL 18 advisory-lock functions:
  <https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS>
- PostgreSQL 18 transaction isolation:
  <https://www.postgresql.org/docs/18/transaction-iso.html>
- PostgreSQL 18 `ALTER TABLE` lock behavior:
  <https://www.postgresql.org/docs/18/sql-altertable.html>
- PostgreSQL 18 privileges:
  <https://www.postgresql.org/docs/18/ddl-priv.html>
- PostgreSQL 18 system catalogs:
  <https://www.postgresql.org/docs/18/catalogs.html>

## 12. Non-Claims

This specification does not authenticate deployment history, approve a contract
transition, guarantee zero downtime, size the database, prove backup or failover,
protect against a superuser or malicious migration owner, or make advisory locks
enforceable for non-participating clients. It does not persist or activate config
epochs.
