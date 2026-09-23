# Runtime Admission Cost Implementation Plan

Design: [runtime admission cost](runtime-admission-cost.md).
Review model and pass count follow the root AGENTS.md policy.

1. Freeze the existing principal predicate, code-owned grants, native role
   mutation tests and recorded PostgreSQL diagnostic. Keep all old designs and
   plans unchanged; route this bounded successor from the documentation index.
2. Derive immutable JSON maps from the existing table and column grants. Replace
   only the column subquery's expected-row joins with exact map membership.
   Retain observed-table cardinality, actual privilege calls, settings and UoW.
3. Add one real PostgreSQL cost witness using the actual captured statement and
   parameters. Check exact map projection and unchanged positive admission;
   retain the existing excess/missing authority and UoW falsifiers.
   Print bounded timing observations without a flaky speed-ratio assertion.
4. Reuse existing REQ-CI-CORE-015 and runtime authority routes; add only the new
   witness and both document routes; recompute their source digest. Run static
   lint/type/import, documentation, binding and exact-range selective checks
   locally. Run all behavior in native GitHub.
5. Perform one frozen independent review, repair confirmed findings, obtain
   exact-head Full Check, then squash merge. Keep unrelated roadmap items open.
6. After postmerge Full Check, release and verify the exact immutable artifact,
   update only the owned Swarm image, preserve PostgreSQL and original history
   generation, and compare fresh stage/plan observations. A deployment failure
   follows the existing exact-image rollback procedure, not a database reset.
