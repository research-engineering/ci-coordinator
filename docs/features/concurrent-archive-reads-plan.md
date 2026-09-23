# Concurrent Archive Reads Plan

Owner and rationale: [concurrent archive reads](concurrent-archive-reads.md).

1. Bind dataset, settings, aggregate population, job bounds and daily rows in
   one SQL statement; reuse canonical row decoding and existing aggregate
   builders. Keep UoW authority and isolation unchanged. Preserve empty,
   unavailable, mapping-change and overflow outcomes.
2. Connect the visible Refresh command to the scoped analytics read as well
   as history status, without discarding filters or bypassing stale-read guards.
3. Implement discovery-first singleton replay before queue creation, using the
   persisted producer identity, absence of any scoped run recheck, full canonical
   fact validation and final lease/CAS. Keep queued/multi-attempt, delivery and
   explicit repair/rescan paths unchanged; generic queued reuse stays deferred.
4. Add native PostgreSQL interleavings that insert between preparation and
   observation, inspect a coherent result, and independently verify committed
   rows. Include scope/generation/mapping changes, empty/bounded overflow and
   corrupt population cases. Preserve all existing statistical oracles.
5. Prove UI refresh reaches the actual analytics endpoint and recovers while
   preserving filters; test late responses and generation changes. Prove reuse
   avoids provider calls only for complete exact singleton facts, never for
   queued repair, partial, conflict, unknown or substituted operands. Add
   full-queue, corrupt-fact fallback, late lease expiry and unchanged statistics
   witnesses through actual discovery handoff rather than a manually seeded hint.
6. Synchronize documentation routing and exact Proofkit witness paths. Run
   admitted local static checks, one independent review under AGENTS.md and
   native GitHub qualification. Repair only invalidated predicates.
7. Squash the qualified exact candidate, release its immutable image and verify
   concurrent import/read/refresh on Swarm without resetting pilot target history or
   editing its workflows. Record performance distributions and external gaps
   separately; do not declare the entire roadmap complete.
