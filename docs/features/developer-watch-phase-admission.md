# Developer Watch Phase Admission

Status: bounded implementation design; native qualification pending

Date: 2026-09-13

## Scope And Authority

This design owns observation-phase admission, not Compose lifecycle policy.
The protected contracts are the [developer environment requirements](../specs/ci-coordinator-developer-environment/requirements.v1.json)
for source feedback and owned cancellation, and the existing
[convergence design](developer-experience-convergence.md). The
[execution plan](developer-watch-phase-admission-plan.md) owns delivery order.
Earlier design and plan payloads remain unchanged.

Baseline: `0cd037d4ba98be6a28ec4fa22c752eed7f192862`.
The caller supplied PR 165 / Full Check `34764745065` / job `103743608451`,
15:17:51Z: build-context reported frontend container identity `shape=empty`.
That observation does not establish the entire provider causal chain.
The earlier EOF before `PROVIDER_TERM` in Full Check `34760619798` likewise
does not establish whether supervision had started or why the process exited.

## Minimum Sufficient Model

Disposition: construct a small transition model. The decision is material:
observation admission changes failure behavior and signal oracles govern the
release of mutation authority. Direct output matching alone cannot distinguish
these lifecycle phases. No new architecture decomposition is needed.

For one successful `compose ps --quiet service` call, distinguish empty output,
one canonical identity, and malformed/multiple output. Command failure is a
separate result. Absence is not an identity, a missing file, readiness, or
provider success beyond that one command. Steady-state access raises on absence.
Only an existing bounded transition wait may treat typed absence or command
failure as pending; neither is evidence of the requested effect.

An accepted restart has the same container ID and a changed start timestamp.
An accepted recreation has a changed container ID. A successful effect round
requires its source/output predicate and the entire identity vector from that
round; partial vectors cannot accumulate across rounds. A phase following an
expected rebuild may await its starting identity vector within the existing
30-second transition budget before making its own mutations. It cannot silently
replace a previously protected source-only identity.

Process phases are published session, spawn/ownership transfer, protected parent
supervision, bounded stop, and terminal receipt or ambiguity fence.
`PROVIDER_READY` proves only that the child's signal handler is installed.
A fixture barrier inside the parent's supervised stop predicate proves entry
into the cleanup-protected loop. A second barrier after native spawn but before
returning its handle exercises unresolved ownership transfer. Interrupting that
second barrier must not invent a clean process receipt or clear the fence.

## Current, Intended, And Protected Relations

Current: empty identity output is a generic `ComposeError`; some transition
waits swallow every such error, while material phase entry reads are unguarded.
Signal tests send SIGINT after child readiness without parent-phase evidence.

Intended delta: distinguish typed successful absence, narrow transition catches,
and bound identity sampling at rebuild phase handoffs. Add deterministic signal
oracles without changing production signal handling. Malformed successful
identity and foreign ownership remain failures, not pending. Command failures
are retryable only inside the existing transition budget.

Protected: ID/start-time rules, exact effect/source assertions, loopback and
ownership-label checks, process liveness, existing deadlines and cleanup,
nonce/inherited-lock ownership, actual exit status and cancellation causality,
no forced-stop admission, and durable ambiguity fencing. The fixture's 0.2-second
grace and 0.2-second kill budgets are not the production 2-second/1-second policy.

## Decision And Falsifiers

Use one absence subtype at the Compose boundary, the existing polling owner,
and fixture-only barriers. This costs a small exception/API distinction and
targeted oracle changes; it avoids a new retry lifecycle or signal state machine.
Keeping broad catches is cheaper locally but cannot distinguish malformed output
or command failure. Sleeps or larger grace periods cannot establish parent
supervision and increase CI time without closing that gap. Passing the previous
phase's identity alone avoids a read but does not observe the next phase's actual
starting state during an outstanding rebuild batch.

Native qualification on f9d4c5a supplies that additional pending phase: Compose
port returns status 1 while the observed service is restarting. A failed bounded
provider command is not malformed successful output. Preserve retryability of
command failures inside the already bounded transition waits using a distinct
`ProviderCommandFailed` type; other callers still fail immediately. Never admit
a command failure as an effect, absence or successful deletion. Persistently
failed commands exhaust the original deadline. Successful malformed output and
foreign ownership remain terminal failures. This corrects the preceding
command-failure premise without adding a retry state machine or extending time.

File observation also requires positive command completion. Docker exec failure
cannot be distinguished from `test -f` false by exit status 1 alone. The probe
therefore emits exact present/absent markers only on status 0; absent requires an
existing searchable parent and no competing entry. Failed commands remain
pending only within the transition budget, malformed markers fail, and content
is read only after a present marker. Path arguments are positional shell values,
not interpolated program text. Both commands consume the same observation
deadline. The native probe corpus includes file, missing entry, directory,
missing parent, dangling link and paths containing spaces and shell syntax.

Reopen this decision if exact-head native evidence shows another constructible
pending phase, a lost cleanup obligation, or an ownership violation requiring a
production repair. No global minimum-cost or global optimum claim is made.

Falsifiers: empty then valid does not converge; empty becomes successful file
deletion; multiple/malformed/foreign output becomes pending; failed commands
become successful effects or extend a deadline; a missing
service outlives the original deadline; stale per-service matches accumulate;
a source restart accepts replacement; a protected frontend source identity
changes; SIGINT after supervision loses the actual clean receipt; or SIGINT
during transfer clears mutation authority while child ownership is unresolved.

## Writer Readiness

| Semantic owner                                     | Planned delta                                                | Protected/derived predicates                                                                                  | Gate and independent validator                                                     |
|----------------------------------------------------|--------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| `compose.py` identity admission                    | Typed absence only for successful empty ps                   | Strict steady-state access; canonical IDs; provider errors and existing ownership checks                      | Exact-head Compose tests and static review, replayed by root                       |
| `watch_witness.py` transition observation          | Narrow pending catches and bounded rebuild handoff snapshots | Same-round effect plus complete identities, restart/recreate distinction, deadlines, restoration and liveness | Exact-head watch tests plus native source-watch material cohort, qualified by root |
| `watch_session_witness.py` and watch process tests | Distinct parent supervision and spawn-transfer barriers      | Actual terminal process receipt, nonce, inherited mutation lock, clean/forced/abandoned classification        | Exact-head subprocess witnesses, independently reviewed and run by root            |

Readiness is closed for this planned writer batch, not for runtime acceptance.
The complete operands for effect admission are the current effect predicate,
every requested service identity, the prior identity vector, transition mode,
watch process liveness, and remaining deadline. Independent falsifiers remove
or change each operand. Signal admission operands are parent phase, actual child
exit, causal cancellation, escalation, process-group quiescence, client contract,
and nonce-bound durable outcome; neither child readiness nor a marker alone
replaces them. Unknown transfer completion remains fenced.

The architecture policy/profile and bounded-process, lifecycle, and session
sources are supporting reads, not expanded write owners. Static checks do not
execute native witnesses. This batch requires fresh root target/evidence binding
and independent GitHub qualification before any behavioral closure claim.
