# Cleanup Fixture Preparation

Status: prior fixture admitted; measured write follow-up awaits exact-head CI

Execution: [preparation plan](cleanup-fixture-preparation-plan.md).
Baseline: `dc4fadd145b31754de2d857d37c94c2bff22b9e9`.
Owners: the cleanup-capacity test and its private fixture support; production
collection, report, budget, compatibility and cleanup contracts are unchanged.

## Minimum Sufficient Model

Disposition: reuseOwnerModel. The material dimension is fixture transaction
ordering, not a production algorithm change. Use the existing UoW state machine
and source/claim/snapshot/report/signal relations, with the native test as an
oracle consumer rather than authority to redefine cleanup.

As-is, each of 101 attempts performs four real store operations, each with its
own UoW admission and commit: register, claim, snapshot and report. Intended:
one UoW per attempt runs those same repository methods, checks the same success
outcomes and commits once. Thus the construction path has 101 rather than 404
participant admissions; policy setup, full readback, expansion and epoch shift
keep their own existing transactions. No runtime attestation is cached or skipped.

The exact protected relation is equality of constructed source identity,
canonical evidence/report operands, captured lifecycle, dependent signals and
retention relationships at the cleanup boundary. Database acquisition/receipt
timestamps and random lease tokens are observations, not fixed byte-equality
operands. There is no provider I/O inside the construction transaction: the
snapshot is already a finite local fixture. This does not authorize combining
production claim acquisition with network collection.

## Independent Fixture Operands

| Operand            | Independent source and protected meaning                                                                                                                                                                                               |
|--------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Population         | Runs 1000..1100 in scope 101/202, attempt 1; exactly 100 expired parents with 2000 jobs each, plus one live one-job parent.                                                                                                            |
| Provider source    | Existing `subject`/`snapshot` factories, one database `now`, API version `2026-03-10`, evidence digest `a` repeated 64 times. Source ID and snapshot subject remain equal.                                                             |
| Job evidence       | Existing one-job prototype, its attempt/head, timing, runner, labels, semantic hash and canonical body; existing expansion generates IDs 1..2000 with independent digest reconstruction and sampled codec equality.                    |
| Claim              | Real repository acquisition using worker `1` repeated 64 times; exact returned source and live holder CAS, with no mocked result.                                                                                                      |
| Report and signal  | Existing report factory bound to the source/job/time; unchanged budget policy and origin hashes `c`/`d` repeated 64 times. Exactly one report and matching signal per parent, with source/report/policy digest and retention equality. |
| Age                | Existing privileged 90-day-plus-one-hour shift for the first 100 parents only, after construction commit; unchanged unexpired final source.                                                                                            |
| Workload admission | Existing origin trigger mode at job insertion and runtime cleanup; runtime-principal UoW and its compatibility/schema/ACL admission remain real.                                                                                       |
| Cleanup oracle     | Independently captured selection SQL and populated EXPLAIN, 100 selected rows, 301 statements, actual surviving child tables, 100 tombstones and no repeat/purge.                                                                      |
| Cost               | `perf_counter_ns` for preparation, committed per-attempt seed calls and committed cleanup; no cleanup-derived expectation or wall/monotonic clock mixing.                                                                              |

No operand is derived from the cleanup result. The original full decoded
2000-job positive control, successful pool cleanup, 180-second total setup
limit and profile-owned expiry deadline remain mandatory.

`committedSeedNanoseconds` sums successful helper entry-to-return intervals:
UoW admission, four repository calls, commit and pool return. It is a subset of
the original total preparation interval, not a measurement of commit alone.
The unchanged cleanup interval begins after its UoW admission and includes its
commit. These distinct measurement boundaries must not be treated as identical.

## Bounds And Alternatives

`PostgresUnitOfWork.__aenter__` configures READ COMMITTED, obtains the shared
compatibility fence, admits capabilities and only then exposes repositories.
Its participant profile keeps 5-second lock, 30-second statement and 60-second
transaction timeouts; collection leases are 300 seconds. Batch size is exactly
one one-job attempt, four existing operations. Commit and connection return
precede privileged expansion/epoch shifting, so no admin connection waits for
this helper's uncommitted parents. Nothing raises or disables a deadline.

The selected route removes three UoW admissions/commits per parent while leaving
all domain writes and admin expansion unchanged. Its cost is a somewhat longer
single parent transaction and a small same-context rollback witness. Keeping
four UoWs is simpler locally but retains the identified redundant admission
path. Batching many parents adds lease/transaction-budget exposure and requires
new coordination with admin expansion; raw row cloning or a seeding DSL adds
unneeded canonical and trigger authority. Neither is needed for this first step.

Reopen only if native source-equivalence fails, the one-parent transaction
approaches its unchanged limit, or measured preparation remains dominated by
other work. Fewer admissions is a structural claim, not measured acceleration.
Root's PR169 `cb9d8909` run `34773384387`, job `103767167523`, reported 161.55s
total and 1190984872 committed-cleanup nanoseconds. That supplied observation
has another head/runner context and is not causal profiling or new-head proof.

## Writer Readiness And Falsifiers

| Changed owner                      | Delta / protected surfaces                                                                                                                                                                     | Gate and independent validator                                                                                                                              |
|------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `_observation_cleanup_support.py`  | One narrowly scoped constructor using real UoW/repositories; expansion code unchanged. Preserve identity, success checks, atomic failure and pool release.                                     | New native seeded readback, snapshot/report mismatch rollback after observed writes, one shared-fence acquisition; root source replay and GitHub execution. |
| `test_observation_cleanup_cost.py` | Replace only construction calls, record committed seeding duration, add focused constructor witness. Preserve every population, cleanup, privilege, expiry, EXPLAIN and post-delete assertion. | Original full-capacity witness plus new small witness in the same native file; root exact-head comparison.                                                  |

Falsifiers: wrong snapshot subject after registration/claim; wrong report attempt
after snapshot insertion; any surviving row after failure; missing signal or
changed source/report/job digest; multiple admissions per seed; admin expansion
before commit; decreased corpus, changed selection or weakened cleanup deadline.
Production sources, shared helper cohorts, registries and existing designs are
outside this delta. Runtime speedup and exhaustive causal attribution remain
unresolved until native qualification on the integrated head.

## Follow-up: Measured Expansion Write

The exact-master serial receipt at `640a463` measured 88.97 seconds of
preparation, including 12.47 seconds of committed seed calls. Two local
Docker/PostgreSQL observations on the later source separated the remaining
work: retention shifts across all 100 expired subjects took 0.52 and 0.45
seconds, while expansion took 22.57 and 21.58 seconds. In the second run,
construction of all canonical bodies and hashes took 2.25 seconds; the
expansion transaction took 19.29 seconds. That interval includes connection
acquisition, header and trigger-mode changes, row transfer, commit and pool
return, not only insertion. These exploratory local observations derive from
`master@0ed8dec` with uncommitted timing instrumentation; no frozen before
patch receipt exists. They identify a candidate, not a qualified or portable
speedup or production-capacity claim.

Keep the same 200,000 constructed job rows and use
[psycopg's row-wise `COPY`](https://www.psycopg.org/psycopg3/docs/basic/copy.html)
only for the privileged fixture insertion, on the SQLAlchemy-owned raw driver
connection inside the existing transaction. SQLAlchemy documents this
[raw-connection boundary](https://docs.sqlalchemy.org/en/20/faq/connections.html).
The table and column identifiers
come from the SQLAlchemy table and the existing row mapping. Restore normal
trigger mode before insertion as before. For each source, the input row list,
column order, transaction, and final queryable relation stay the same; only
the PostgreSQL transfer protocol changes. The one fully decoded 2,000-job
sample, counts, live survivor, canonical digest, rollback and cleanup oracle
remain independent checks. A different row, bytea adaptation, trigger state,
transaction boundary, or full-read result falsifies the equivalence.

The rejected alternative is batching retention shifts: their measured total
is below one second and changing their intermediate transaction order buys
little while adding a new proof obligation. Do not move this fixture-only
`COPY` into the runtime persistence adapter without a separate owner decision.

One same-host before/after pair retained all test assertions: preparation
31.60 to 17.42 seconds, expansion transaction 19.29 to 4.00 seconds, and complete
test wall time 36.43 to 22.54 seconds. Construction remained 2.25 versus
2.00 seconds. These are individual observations with Docker/cache variance;
the exact-head hosted result must be measured before claiming a portable
speedup, and this fixture optimization says nothing about production cleanup
capacity.
