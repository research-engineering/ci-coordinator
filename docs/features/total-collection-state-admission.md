# Total Collection-State Admission

## Decision

The CI economics capability owns collection-state integrity. Replace the two
nullable PostgreSQL predicates for lease and state shape with their total forms,
and admit the resulting catalog as `ci-economics-evidence/v4`.
Public API, domain transitions, retry/retention policy, identifiers, stored data
and privileges are unchanged. Raw invalid rows that the domain already rejects
will now be rejected by PostgreSQL too. This is the only behavioral tightening.

## Proof And Boundaries

Let `P` be either existing predicate and `V` the set of valid domain states.
PostgreSQL accepts `P IN {TRUE, UNKNOWN}`; admission must require `P = TRUE`.
For each `v IN V`, the complete lease or valid absent lease and selected
state branch evaluate to TRUE. Therefore `P IS TRUE` preserves `V` while
rejecting the counterexamples with a missing lease owner or terminal outcome.
This is a bounded refinement claim, not complete domain/schema equivalence.

The independent catalog derives the total expectation from the historical v3
catalog, not from the migration or runtime SQLAlchemy metadata. Native witnesses
exercise positive populations and mutations before SQL admission, bypassing the
domain constructor on the negative path.

## Migration And Compatibility

The current transition protocol forbids adding and retiring a capability in
one declaration. Retained descriptors are immutable. Revision 0014 retires v3;
0015 validates existing rows, replaces both checks, then admits v4. This is the
owner-evidenced sequencing exception to the ordinary one-revision rule.
Alembic already runs the whole upgrade under one transaction and exclusive
compatibility fence, so a failed final attestation rolls both revisions back.
An explicitly targeted intermediate head does not admit economics operations.

Both migrations preserve all unrelated capability pairs. Historical migrations
and v1-v3 attestors remain unchanged. Current economics and webhook operations
require v4, so neither predecessor-only nor retired intermediate states expose
their repositories. Old economics writers cannot use the new catalog.

One bounded-existence query rejects invalid retained rows with a static
diagnostic and no raw row, token or fabricated repair. One ALTER TABLE replaces
both constraints; PostgreSQL validates the population before commit. This adds
no recurring query, cache or worker. Scan/lock time depends on retained volume.

## Alternatives And Revision Triggers

- A generic validator, trigger or extra guard constraints would duplicate the
  same authority. Replacing the two predicates is the smaller sufficient change.
- Rewriting 0003 would leave already migrated databases unchanged.
- Weakening the expand/contract protocol would enlarge an unrelated contract.
- Mixed-version availability is not claimed. Use an admitted maintenance window:
  capture recovery evidence, stop/drain only this service, migrate with the
  exact qualified image, verify schema and unchanged ACL, then start that image.
  Failure before commit retains the predecessor; after commit use forward
  repair or validated restore, never an incompatible predecessor binary.

Revisit the rollout if measured lock duration exceeds the admitted window,
invalid retained rows exist, another writer bypasses the fence, or an owner
requires uninterrupted mixed-version access. Do not enable selective omission,
reset history or tune PostgreSQL merely to complete this repair.

## Proof And Delivery

See the [implementation plan](total-collection-state-admission-plan.md).
Current requirements remain owned by the database compatibility and economics
specifications; the new implementation inventory binds their additional witnesses.

Sources: [PostgreSQL CHECK semantics](https://www.postgresql.org/docs/18/ddl-constraints.html#DDL-CONSTRAINTS-CHECK-CONSTRAINTS)
and [ALTER TABLE validation](https://www.postgresql.org/docs/18/sql-altertable.html).
