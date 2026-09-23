# Database Observation Snapshot Plan

The [design](database-observation-snapshots.md) owns the corrected relations.

1. Add final head-plus-maximum statement observation to readiness; retain
   initial head-only checks, error classification and checkpoint semantics.
2. Centralize each workbench family's existing ordering, apply its original
   bound before ranking, and compose one type-preserving positional query.
3. Change only the read-only snapshot profile to READ COMMITTED and bind the
   five projections plus metadata to the post-fence statement. Update current
   specifications without modifying historical design documents.
4. Add real PostgreSQL public-boundary interleavings for normal append in the
   former readiness interval, verified-prefix continuation and post-fence
   migration visibility. Preserve corrupt-head/hash and orphan rejection.
5. Qualify empty/mixed/all-family snapshots, ordering/ties/nulls, scope,
   limit+1 truncation, bytea/datetime preservation and bounded output/query count.
   Keep query-shape assertions distinct from capacity measurements.
6. Update exact proof routes and derived source hashes; run allowed static
   gates, then one independent frozen review and native GitHub qualification.
   Group any repairs before another remote update.

No schema migration belongs to this batch. The separately confirmed nullable
collection-CHECK defect needs its own forward capability transition and native
invalid-state witnesses. Existing deployment and production evidence do not
automatically cover these new snapshot semantics.
