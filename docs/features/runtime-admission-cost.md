# Runtime Admission Cost

Status: bounded persistence optimization design

Owner: persistence, under REQ-CI-CORE-015 and REQ-CI-RUNTIME-020.
Delivery: [implementation plan](runtime-admission-cost-plan.md).

## Decision

Replace only the expected-table/column row joins in the column-admission
subquery with membership lookups in immutable code-owned JSON grant maps.
Keep the full admission predicate,
bound values, statement snapshot, per-transaction execution, compatibility
fence, isolation, cancellation and error algebra unchanged. This is neither a
cross-query cache nor permission to skip live database facts.

## Measured Cause

A bounded read-only observation of the owned PostgreSQL 18.6 development service
on source 5e5193a8 found 58 runtime-principal and 33 catalog samples among 94 active
session samples. These are sample counts, not complete elapsed attribution.
The exact principal query's timing-disabled EXPLAIN ANALYZE took 166.460 ms in
one observation. A timing-enabled diagnostic showed its expected-column function
scan returning 216 rows in 1944 loops, with 419688 discarded join combinations.
Instrumentation itself increased elapsed time; do not compare these timings
as equivalent measurements. Both plans used no JIT. A separate jit=off probe
did not improve its observed duration, so no JIT setting change is justified.

Loops count rescans, not function evaluations: PostgreSQL can rewind an existing
tuplestore when function parameters are unchanged. Materializing that CTE alone
therefore does not establish reduced decoding or remove the row-join work.
A native ordered pair measured 36.663 ms with materialization and 28.941 ms without;
that pair is not a stable regression estimate, but does not support that repair.
See [PostgreSQL Function Scan](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/executor/nodeFunctionscan.c)
and [CTE materialization](https://www.postgresql.org/docs/18/queries-with.html#QUERIES-WITH-CTE-MATERIALIZATION).

## Equivalence And Cost

For relation r, column c and privilege p, let T(r,p) mean the owner grants p
on the table, and C(r,c,p) mean an explicit column grant. The existing expected
column predicate is T(r,p) OR C(r,c,p). The new table map stores its allowed
privileges; the column map stores allowed privileges under exact relation and
column keys. Membership is therefore equivalent to the same two predicates.
Missing keys evaluate to false using explicit COALESCE; duplicate grants do
not change existence. Names remain data, never concatenated SQL or composite
keys whose delimiters could collide. The existing observed-table cardinality,
unexpected-table and grant-option checks remain unchanged.

Only the finite immutable grant definitions are projected once at module load;
live database facts D are read by the unchanged per-statement predicate. Map
storage is O(number of declared grants). The column comparison no longer joins
each observed column/privilege to the complete expected-table and column row
sets. This removes those relational cross-products, not the required actual
has_column_privilege calls. Planner and end-to-end cost remain measured rather
than inferred from expression count. Bound JSON inputs use the existing cast
mechanism; no query, pool, JIT, role or database setting changes.

Writer readiness requires preserving all independent authority operands:
direct identity, role attributes/membership, relation existence and kind,
table/column privileges and grant options, schema/type/routine/sequence/default
ACL, migration metadata and current compatibility state. Existing positive,
excess-authority, revoke/regrant and UoW tests remain mandatory native gates.
No grant, role, SQL predicate or production data changes are authorized here.

## Alternatives And Falsifiers

- A TTL/startup-only admission cache changes freshness and is rejected.
- Larger pools or another queue cannot remove repeated work in this query.
- General transaction telemetry is useful if the remaining cause is ambiguous,
  but is not necessary to prove this already observed repeated pure operation.
- Materialization alone retains the expensive row comparisons and has no
  demonstrated gain. Only the column subquery changes; the other admission
  conditions are retained verbatim.

Use the real statement and real runtime role in PostgreSQL CI. Capture its
SQLAlchemy statement and parameters instead of copying the authority predicate.
The captured maps must represent exactly the owner-granted table and column
relations, including omitted privileges. Real admission must still succeed on
the restricted positive fixture and fail on the existing altered-authority
corpus. Record repeated actual EXPLAIN observations without a noisy ratio gate.
Reconsider the optimization if the result differs, storage becomes material,
or representative before/after distributions regress. The same native role
mutation cases must still reject excess and missing authority.

This does not prove full history throughput, burst fairness, global optimality,
production capacity or complete roadmap delivery. Compare actual claim-stage
distributions after immutable deployment before claiming an end-to-end gain.
