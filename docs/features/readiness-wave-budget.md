# Readiness Wave Budget

Status: bounded source decision; live admission remains conditional

Scope: ROADMAP CI-054/CI-006. Persistence owns verification; runtime
composition owns caller selection. This design adds no backlog or queue.

## Decision

`DatabaseReadinessProbe` owns one constructor-selected, read-only
`audit_batch_size`: an exact `int` in `1..4096`, excluding `bool`.
The default and public stateless helper retain `4096`. Shared connected
composition explicitly selects `1` only for `settings.mode == "non_enforcing"`;
enforcing retains `4096`. `managed` selects lifetime, never row policy.
No settings profile, environment knob, cache, worker, process pool or second
verifier is introduced. The existing ordered SQL `LIMIT` remains before row
materialization; neither post-fetch slicing nor an internal catch-up loop is
admitted.

## Protected Model

Reuse the [persistence owner](../architecture/modules/persistence.md): exact
checkpoint `q/hash`, immutable chain, live owner `G`, current worker `W`, and
its original monotonic deadline `D`. Budget `B` grants no authority.
Compatibility fence, READ COMMITTED, schema/principal/capability admission,
head/maximum checks, failure algebra, waiter cancellation, stop and cleanup
remain unchanged. Only the current live worker may publish or reset after
successful transaction exit and public close, before `D`, with unchanged
input checkpoint. A smaller suffix does not make synchronous decode preemptible.

For a fixed valid head `H`, contiguous successful timely demand waves advance
`q` by `min(B, H-q)`. Thus `ceil((H-q)/B)` waves reach that head. A warm
unchanged head loads no suffix. This is conditional finite progress, not
autonomous progress: with backlog two, appending one row before every B1 wave
keeps the gap and defeats catch-up.

## Consequences And Non-Claims

B1 bounds the cardinality of the NEW materialized suffix to one row, not the
whole heap. The five variable-column profile envelope is
`B * (1048576 + 4 * 4096)` bytes; previous checkpoint, fixed columns, driver
buffers, copies and decoded object amplification are excluded.
Recovery exposes additional `audit_verification_in_progress` / `not_ready`
waves (including `/readyz` 503) and slower backlog catch-up. Repeated
admission, transactions and checkins may increase total work. No total CPU,
RSS, event-loop latency, production capacity or all-input guarantee follows.
Before live admission, root must establish initial backlog, completed demand
source, append pressure and acceptable recovery time. Container health alone
is not that demand source. Missing premises block live, not this source step.

## Alternatives And Refutation

- Keep 4096 everywhere: cheapest fallback, but leaves the discovered large
  decode burst unresolved; reopen if the narrow B1 native gate fails.
- Global B1: rejected. Two legal small rows make every fresh stateless helper
  stop at revision one forever; its former one-call ready/revision-two
  observation would be lost. Enforcing/default callers are outside the pilot.
- B4: a burst/throughput tradeoff, not proof that B1 minimizes total work.
- SQL byte-prefix selection or process routing: require new selection or
  lifetime/serialization proof; unnecessary unless this candidate is refuted.
- Loop to ready inside a request: changes the one-wave unit and cannot hide
  the helper regression or reopen the original deadline.

## Cheap Acceptance And Direct Falsifiers

Static owner lint/type/import checks are local-light only. Native execution
belongs to root's exact-source GitHub qualification and independent review.
The writer does not run tests, collection, DB, Docker, browser or installs.

Unit witnesses cover default/positive bounds, invalid 0/-1/4097/bool/float/
string/None, read-only policy and independent probes. Real-DB witnesses retain
two-row helper ready/2, persistent B1 progress/1 then ready/2, warm no-reload,
all nine independent corruptions and all existing PR53 lifetime/fence tests.
Actual composed non-enforcing/enforcing scenarios assert 1/4096 selection.

One finite native witness uses the existing clean DB fixtures, six fixed
shapes and 32 rows: small, maximum scalar, legal 10000-node/64-depth payload,
and 1 MiB malformed wide/syntax/depth payload at sequence 32. Every legal
checkpoint/hash and SQL LIMIT/cardinality-one observation must precede ready;
malformed never becomes ready. Each wave retains the 5000 ms deadline.
Setup/seeding, wave wall/process CPU, sampled loop lag, cursor events and
settlement are separate observations, not performance pass thresholds or
server CPU. RSS and quantitative capacity remain unproved. Successful cases
emit the bounded measurement JSON outside pytest capture so native job logs
retain it; this is not a durable production telemetry channel.
The measured block temporarily stops current Coverage and restores it in
`finally`: timing is untraced but includes delegating observation hooks.
No case is removed, no coverage floor changes; covered semantic/lifetime
witnesses remain mandatory. Probes drain and engines dispose on every exit,
preserving primary errors/cancellation. Failed finite recovery requires
replanning, not a deadline extension.

Root owns module-reference, requirement/count and generated-proof updates,
final independent review and publication. This document is a new design
identity; existing designs/plans remain unchanged under addition-only policy.
