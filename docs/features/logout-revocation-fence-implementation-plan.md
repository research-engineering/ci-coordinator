# Logout Revocation Fence Implementation Plan

Status: implementation and validation plan
Date: 2026-10-11

Design: [logout revocation fence](logout-revocation-fence.md).
Normative owners: [identity](../architecture/modules/control-plane-identity-and-repository-attestation.md),
[database compatibility](../architecture/cross-cutting/database-compatibility.md).
Execution remains under CI-015 in the [roadmap](../../ROADMAP.md); deployed
login/logout qualification remains under CI-062.

## Ordered Work

1. Seal a login-start anchor, the newest committed fence generation and a
   database instant read under the shared fence lock, into the existing
   browser transaction cookie; reject cookies without both.
2. Pass exactly that anchor to session insertion; check the deleted-generation
   floor, matching fence generations and the age bound under the shared lock in
   the insertion transaction.
3. Advance the authenticated issuer/subject fence in local logout, even when
   its handle is already gone, and the sid-else-subject fence in admitted
   back-channel logout under the exclusive lock, atomically with deletion,
   singleton generation increment and bounded expired-fence cleanup that raises
   the floor; replays write nothing.
4. Add expand revision `20261011_0018`, capability, exact catalog contract with
   the singleton row count, result and runtime attestation, least-privilege
   grants, profile inventory, smoke head and fixtures. Session reads keep
   identity-only requirements.
5. Run only admitted static checks locally and obtain independent review under
   [AGENTS.md](../../AGENTS.md); behavioral witnesses run in GitHub CI under the
   [verification placement rule](../architecture/cross-cutting/testing-and-proofkit.md#6-required-gates-and-execution-placement).

## Causal Acceptance

| Case | Required independent observation | Wrong implementation it kills |
| --- | --- | --- |
| F1 | Domain: code obtained before logout, callback after logout, is rejected; a later start succeeds | anchor read at callback time; logout without fence |
| F2 | Domain: login start without a durable anchor is unavailable and issues no authorization URL | fallback to an unordered instant or the application clock |
| F3 | Domain: the callback forwards the sealed anchor despite a later store anchor | anchor replaced at callback time |
| F4 | Crypto: a sealed cookie without `loginFenceGeneration`, with a negative, boolean or text generation, or with a naive `loginStartedAt` is rejected | defaulting a missing or malformed anchor |
| F5 | PostgreSQL: late insertion after local logout rejected, other subject accepted, renewed start accepted, Activity has no extra login | missing or overbroad subject fence |
| F6 | Domain and PostgreSQL: authenticate, concurrent replacement of the handle, logout, late callback is rejected; the replacement survives and only actual deletions are journaled | fence identity read from the session row at logout |
| F7 | PostgreSQL: sid, subject and both back-channel targets fence exactly their scope, including zero deleted rows | fencing only when rows were deleted; wrong target precedence |
| F8 | PostgreSQL: a replayed token moves neither the fence nor the counter | fence write before replay consumption |
| F9 | PostgreSQL: a fence whose instant precedes the anchor still rejects an older login, and one whose instant follows a newer anchor does not | ordering by database time instead of generation |
| F10 | PostgreSQL: after cleanup erased a fence under a forward clock step, logins anchored below the floor are rejected for every subject and a new start succeeds | cleanup without a deleted-generation floor |
| F11 | PostgreSQL: login start and insertion wait for an uncommitted fence writer; start reads its generation, insertion rejects | missing shared lock in either transaction |
| F12 | PostgreSQL: future and over-age anchors reject; cleanup removes exactly 128 expired rows, no younger row, and raises the floor to the largest erased generation | missing age bound; unbounded, premature or floorless cleanup |
| F13 | PostgreSQL: atomic forward 0018 with seeded singleton, replay, downgrade refusal; damaged fence catalog or missing singleton fails closed for login and logout while reads continue | omitted catalog or singleton attestor; capability not required |
| F14 | PostgreSQL: fence ACL drift fails login start and, through the global restricted-principal attestation, session reads until repaired | weakened restricted-principal admission |

## Handoff And Non-Claims

The canonical database-compatibility mutation inventory is not extended here:
its execution budget is fixed by the suite envelope, and the omitted-attestor
mutant is directly killed by F13. No deployment, provider, latency or clock
evidence is claimed; asynchronous-replication loss of committed logouts is
outside the guarantee.
