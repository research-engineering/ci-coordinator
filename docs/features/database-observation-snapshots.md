# Coherent Database Observation Snapshots

Status: feature design

## Decision

Bind each jointly interpreted observation to one PostgreSQL statement snapshot.
Keep transaction-local compatibility fences and READ COMMITTED; do not introduce
session locks, weaker consistency or retries that conceal conflicting reads.
The [implementation plan](database-observation-snapshots-plan.md) owns delivery.

## Confirmed Countermodels

Readiness currently reads its final audit head and maximum sequence separately.
An ordinary append between those reads yields different committed versions and
can falsely report corruption, discarding an already verified prefix.

Workbench currently establishes a REPEATABLE READ snapshot before its blocking
fence acquisition: both timeout configuration and advisory acquisition execute
SELECT statements. A migration can commit before the reader obtains the fence,
while subsequent reads retain the earlier snapshot. This contradicts the
post-fence admission premise in the persistence contract. PostgreSQL documents
the snapshot boundary at the first non-control statement in
[Repeatable Read](https://www.postgresql.org/docs/18/transaction-iso.html).

## Readiness Relation

For final observed head H and maximum sequence M:

```text
snapshot(H) = snapshot(M)
valid unchanged H and M = H.sequence -> verified prefix may be retained
valid advancing H -> verification in progress, retaining the verified prefix
inconsistent H or M -> existing invalid-state outcome
```

Add the scalar maximum only to the existing final head-row query. Initial
head-only checks and the unchanged-prefix fast path stay cheap. Reusing the
readiness row decoder preserves missing-head and invalid-head classifications;
the replay snapshot helper has a different exception contract. No reader row
lock is added, so ordinary audit writers remain unblocked.

## Workbench Relation

Configure and verify READ COMMITTED plus READ ONLY, acquire the existing shared
fence, and perform current capability admission. Read ledger revision, observed
statement time and all five repository sections in one subsequent statement.
The fence prevents concurrent migration; one statement provides coherent data.
Observed time denotes the statement snapshot, not an earlier transaction start.

Every existing family retains its own complete ordering and limit+1 bound.
Rank only the already limited family, under that same ordering. Join families
by unique position against a bounded position relation:

```text
for each family F: cardinality(F) <= limit + 1
for each position p: cardinality(F at p) <= 1
joined output cardinality = limit + 1
```

Thus five sections cannot create a multiplicative cross product. A null family
position denotes padding and never becomes a result row. Prefix column names
only for transport inside this query; reconstruct the original mappings without
JSON conversion or coercion of BYTEA, datetime and numeric values. Existing
domain projections still own redaction, scope and canonical integrity.

## Alternatives And Costs

READ COMMITTED with five separate reads fails coherence. Retrying on audit-head
changes cannot prove that every underlying mutation increments that head.
Session advisory locks introduce release, cancellation, connection-loss and
pool-reuse obligations without adding a required product behavior. JSON row
aggregation adds another byte/date decoding contract. These are not selected.

The bounded position approach adds null padding and ranking over at most
limit+1 rows per family. It must not move ranking ahead of the original limit
or turn a bounded top-N sort into an unbounded window operation. Native query
shape and cardinality witnesses remain required; no measured speedup or
whole-service capacity is claimed. Reconsider typed UNION or another owner-local
representation if representative plans show lower cost with equal guarantees.

## Protected Boundaries

Preserve all five sort orders, tie breaks and null ordering; section truncation;
empty and mixed populations; exact scope; raw canonical bytes; read-only mode;
current schema/ACL admission; bounded deadlines and failure classifications.
Corrupt/orphan audit state must still fail. A valid append must not lose the
verified checkpoint. No migration, durable row or active-CI authority changes.

Native witnesses must control the former race intervals independently and
distinguish before/after histories. Source equality and sleeps alone do not
prove causal ordering. Qualify post-migration admission, concurrent plan/audit
coherence and unchanged typed projections under the actual runtime principal.
