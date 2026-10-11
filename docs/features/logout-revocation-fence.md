# Logout Revocation Fence

Status: selected design; source implemented, native qualification pending
Date: 2026-10-11

Owners: [identity and attestation](../architecture/modules/control-plane-identity-and-repository-attestation.md),
[organization control plane](../architecture/cross-cutting/organization-control-plane.md) and
[database compatibility](../architecture/cross-cutting/database-compatibility.md).
Delivery: [implementation plan](logout-revocation-fence-implementation-plan.md).
The [roadmap](../../ROADMAP.md) remains the sole execution register under CI-015;
deployed qualification remains under CI-062.

## Problem And Owner Decision

The [T9 adjudication](../adoption/temporal-and-oracle-audit-2026-09-06.md)
confirmed a reachable ordering at base `08e417484a23a65978ccfab0f05af0fff6ee5062`:
`BrowserIdentityService._complete_login` awaits the provider code exchange
(`browser.py:240`) and only afterwards inserts the session (`browser.py:271`,
`control_plane_session_repository.py:134`). A local logout
(`browser.py:316-320`, repository `delete`) or an admitted back-channel logout
(`consume_and_delete`, repository line 264) can commit between them. Neither
deletion knew about the pending callback, so the late insertion created a
session after the user's logout had completed. Keycloak end-session cannot
close the gap: the local logout commits before the provider redirect, remote
logout failure cannot restore the local session, and an exchange may already
have completed.

The owner decision of 2026-10-11 is binding: evidence acquired before logout
MUST NOT create a session after logout. A process mutex is insufficient across
replicas, so the fence is durable PostgreSQL state checked atomically with the
insertion. A later supervisor ruling fixes the clock assumption: a
nondecreasing PostgreSQL wall clock is not an admitted hard constraint, so
backward clock steps and failover clock regression are in scope for safety.
Wall time may serve only liveness (age bound, retention and cleanup).

## Ordering Predicate

One transactional singleton row, `control_plane_logout_fence_generation`,
holds `current_generation` and `deleted_generation_floor`. Every fence write
increments `current_generation` and stores the new value as the fence's
`generation`. Let `G` be the generation sealed by a login start. Insertion of a
session with issuer `i`, subject `s` and Keycloak session `k` is admitted only
if

```text
deleted_generation_floor <= G
and not exists fence(i, t, g) with t in {subject:s, sid:k} and g > G
and A <= now_db < A + R
```

- `G` is `current_generation` read at login start, before the authorization
  request is issued, while holding the shared fence lock. `A` is the
  `statement_timestamp()` of the same statement. Both are sealed in the
  existing AES-GCM, domain-separated, server-key transaction cookie as
  `loginFenceGeneration` and `loginStartedAt`, so the browser cannot alter
  them. The callback passes exactly that anchor; it never re-reads either
  value after the exchange.
- A fence's `generation` is assigned inside the deletion transaction while
  holding the exclusive fence lock. An existing target row is overwritten with
  its newer generation and write time.
- `now_db` is the insertion transaction's database time. `A` and `now_db`
  bound only the login age; they never order a login against a logout.

Neither the application clock nor the unsynchronized IdP `iat` is compared
with a fence. The existing application-clock transaction TTL check remains an
independent earlier rejection.

The anchor dominates a callback-time anchor. The earliest point the relying
party can observe for any evidence in this flow is its own authorization
request. An authorization code issued before the logout but delivered after its
commit has `G < g(L)` and is rejected, while a callback-time anchor would read
a generation at or after the fence and admit it. A login whose start follows
the commit reads `G >= g(L)` and is admitted.

## Serialization And Clock Independence

One transaction-scoped advisory lock,
`hashtextextended('control-plane-logout-fence/v1', 0)`, orders all parties.
Fence writers take it exclusively before the existing Activity journal lock;
the login-start read and session insertion take it shared before that lock.
The global key is required because the login start does not yet know the
subject, and a sid-only back-channel token does not know the subject either.
Logout writers are already globally serialized by the Activity journal lock,
so the exclusive mode adds no new writer contention. Lock order is always
fence then Activity then identity, so no wait cycle exists. The lock wait is
bounded by the profile participant `lock_timeout`; a later Activity wait keeps
its existing 100 ms bound. Every read happens in a READ COMMITTED statement
issued after the lock is held.

For a login start `S` and a fence writer `W`, exactly one lock order exists:

```text
S before W: S reads G before W increments it, so g(W) > G: later insertion rejects.
W before S: S waits for W's commit and reads G >= g(W): W cannot reject S.
```

For insertion `I` and writer `W`: if `W` commits first, `I`'s later statement
reads the committed fence; otherwise `I` commits first, so the session existed
before the logout committed and `W` itself deletes or leaves it according to
the unchanged logout scope. Provider I/O never runs inside these transactions:
the login-start read commits before redirecting and the exchange completes
before insertion begins.

The counter is transactional and only committed values are visible, so these
relations hold for any database wall clock, including backward steps and a
failover to a node whose clock lags. A rolled-back writer leaves the counter
unchanged. The shared lock is not needed for safety with a transactional
counter; it keeps a login that starts during an in-flight logout of its own
subject from being refused once that logout commits.

## Identity Scope

| Logout | Fence target | Deleted sessions (unchanged) |
| --- | --- | --- |
| Local RP logout of an authenticated session | issuer + subject of the authenticated principal | only that handle |
| Back-channel token with `sid` (with or without `sub`) | issuer + sid | existing `(issuer, sid[, sub])` conjunction |
| Back-channel token with only `sub` | issuer + subject | every session of the subject |

Local logout fences the issuer and subject carried by the authenticated
principal, not the identity read from the row inside the logout transaction.
A concurrent callback of another tab may already have replaced the
authenticated handle; the logout still advances the subject fence, deletes
nothing and journals no Activity event for the absent row, so only actual
deletions are journaled. Local logout is deliberately subject-wide for the
fence. It is a superset that satisfies the owner decision under every reading
of "evidence acquired before logout": a callback of the same browser may carry
a new Keycloak `sid` when the SSO session was renewed concurrently. The
intended asymmetry is explicit: an in-flight login of the same subject on
another device that started before the logout is refused with `invalid_login`
and can be retried, but already existing sessions on other devices, including
a replacement committed before the logout, are not revoked. "Logout
everywhere" would be a separate feature. A `sid` names one Keycloak session, so
a sid-bearing back-channel token fences only that session. Expiry, revocation
for profile change and replacement write no fence. A consumed back-channel
replay writes no fence. A back-channel token that deletes zero rows still
writes its fence because that is exactly the late-insertion race.

## Bounds, Retention And Cost

`R = MAXIMUM_LOGIN_TRANSACTION_SECONDS + MAXIMUM_CLOCK_SKEW_SECONDS = 420 s`,
the existing policy maxima for the login transaction TTL and skew. A callback is
already admitted only within the configured TTL of the transaction on the
application clock; the skew allowance covers application/database clock offset
and bounded exchange time. Insertion requires `A <= now_db < A + R`. A fence
writer erases at most 128 fences with `fenced_at <= now_db - R` and raises
`deleted_generation_floor` to the largest erased generation in the same
transaction. Insertion refuses any anchor with `G < deleted_generation_floor`,
because an erased fence newer than the anchor can no longer be ruled out.
Erasure therefore never resurrects earlier evidence, whatever the clock did.

Wall time is liveness only. A forward clock step can erase a fence early; the
floor then refuses every login anchored before the erased fence, for every
subject, and the user restarts login. A backward step can make an anchor look
future or keep fences longer. Without clock steps, an anchor older than the
newest erased fence is already older than `R` and rejected by the age bound,
so the floor refuses no otherwise admissible login. Storage is one row per
distinct target fenced in the last `R`, plus a backlog drained at 128 rows per
write, plus the singleton row.

Each login start performs one short admitted database transaction without row
writes; it is behind the existing login-start rate limit. Unauthenticated
callers therefore cannot create fence rows. Logout adds one bounded delete, one
singleton UPDATE and one UPSERT to its existing transaction; insertion adds one
singleton read. Admission of a fence-dependent operation additionally confirms
that exactly one singleton row exists.

## Failure Behavior

Fence capability absence, catalog mismatch, a missing or duplicated singleton
row, lock timeout or database failure makes login start return
`session_store_unavailable` before any authorization URL is issued, and makes
insertion and logout unavailable. Logout keeps its existing failure semantics:
the deletion and its fence are one transaction, so an unavailable fence leaves
the session in place and returns the existing unavailable outcome; a session is
never deleted without its fence. Reads of an existing session require only the
identity capability, so fence-specific catalog and singleton failures do not
suspend authentication.

Runtime ACL drift is different. Every admitted operation runs the existing
restricted-principal attestation over the whole runtime ACL, including the
fence relations, and this design does not weaken it. Missing or excess fence
grants therefore also make session reads unavailable until the grants are
repaired.

A fenced insertion is a rejection mapped to the existing `invalid_login`
response and the anonymous `login_rejected` diagnostic; no Activity event
class, wire field or cookie attribute changes. Transaction cookies sealed
before this release lack the generation and are rejected; the user restarts
login.

## Persistence

Expand revision `20261011_0018` adds `control_plane_logout_fences`
(`issuer`, `target_kind`, `target_value`, `generation`, `fenced_at`), the
singleton `control_plane_logout_fence_generation` seeded with generation and
floor zero, and capability `control-plane-logout-fence/v1` to the exact
renewal head. Constraints bound generations to the JSON-safe integer range and
keep `0 <= deleted_generation_floor <= current_generation`. PUBLIC is revoked
from both tables and their row types. The runtime principal receives fence
SELECT, INSERT and DELETE plus column UPDATE of `generation` and `fenced_at`,
and singleton SELECT plus column UPDATE of `current_generation` and
`deleted_generation_floor`; it cannot insert or delete the singleton. The exact
catalog contract plus the singleton row count is shared by migration result
attestation and runtime admission. Login start, insertion and logout require
the new capability; session reads do not. Apply the migration and the drained
runtime-principal provisioning before rolling out the binary.

## Alternatives

| Alternative | Decision |
| --- | --- |
| IdP-only sid revocation (end-session, back-channel) | Rejected: exchange can finish before logout; remote logout can fail; local logout precedes it. |
| Process mutex or in-memory generation | Rejected by T9: not durable and not shared by replicas. |
| Reuse Activity `logout`/`revoked` events | Rejected: couples revocation authority to a privacy-retained journal, and a zero-row back-channel logout has no event. |
| Fence identity read from the session row at logout | Rejected: a concurrent replacement can delete the authenticated handle first, so the logout would write no fence. |
| Database wall-clock instants as the order (first selection) | Rejected after the clock ruling: a backward step lets a first fence carry an instant older than an earlier anchor. |
| PostgreSQL sequence as the generation | Rejected: `nextval` is non-transactional and WAL-logged ahead without flush, so a crash after a rolled-back writer can reissue a value an anchor already observed; it also needs sequence grants outside the catalog contract. |
| Per-subject generation counter | Rejected: the subject is unknown at login start, so it degenerates to the global counter. |
| Insert-only tombstone per logout | Viable; rejected because the UPSERT keeps one bounded row per target at the cost of one column UPDATE grant. |
| Transaction-id snapshot visibility | Exact commit order without clock trust, but adds snapshot text to the cookie and wraparound handling; the counter is simpler under the existing global lock. |
| Seal the timeline id to detect promotion | Rejected for this change: it narrows but cannot close asynchronous-replication loss, which stays a stated non-claim. |
| Retention without a floor | Rejected: a forward clock step at cleanup followed by regression would erase a still-relevant fence. |
| Selected: transactional singleton generation under the global lock, with a deleted-generation floor and wall time for age and cleanup | Smallest mechanism whose safety needs no clock assumption; reuses the existing lock, sealed cookie and migration conventions. |

## Privacy

Fence rows hold only issuer, subject or Keycloak sid, which sessions already
retain, a generation and a timestamp. The singleton holds two counters. They
hold no handle, digest, token, display data or request metadata, are not
exported through Activity, and fence rows expire after `R`.

## Falsifiers And Revision Conditions

The design is invalid if a callback started before a committed logout of its
subject or sid inserts a session, including when the logout's fence carries an
earlier database instant than the login start or when the fence was erased by
cleanup; if a local logout whose authenticated handle was concurrently
replaced writes no subject fence; if a login started after that commit is
rejected solely by that fence; if another subject or sid is fenced; if a
replayed back-channel token advances a fence or the counter; if an insertion
or login-start read completes while an uncommitted fence writer holds the lock
without observing it; if cleanup removes a fence younger than `R`, more than
128 rows per write, or without raising the floor to its generation; or if a
damaged fence catalog or singleton still admits session creation.

Revisit when the login TTL or skew maxima change, when an owner requires logout
of every device, when multiple identity realms share the runtime, when the
deployment adopts synchronous replication or another failover model that
changes the replication non-claim, or when measured login-start latency, lock
contention or floor-induced login refusals violate an owner budget.

## Non-Claims

No deployed Keycloak, replica, clock, latency or capacity evidence is claimed.
Native PostgreSQL, unit and mutation witnesses run only in GitHub CI.

Loss of a committed logout is outside any achievable guarantee. If
asynchronous-replication failover or a restore loses committed fence writes,
the promoted or restored database has neither those fences nor their counter
advance. A login anchored on the lost history may then compare as later than a
subsequent fence and be admitted. The design guarantees ordering only over
durably retained committed history.

This design does not revoke already issued sessions on other devices, does not
make revocation immediate for existing sessions, and does not change token,
cookie attribute, CSRF or role semantics.
