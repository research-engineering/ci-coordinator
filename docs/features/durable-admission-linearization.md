# Durable Admission Linearization

Status: accepted scoped reconciliation-contract refinement and B1 design

Date: 2026-09-05

## Decision

Repair the existing PostgreSQL admission boundary. Do not add a queue, service,
generic lease framework or new schema version. The
[execution plan](evidence-led-operational-closure-implementation-plan.md#3-b1-durable-correctness)
owns scheduling. This successor replaces only the injected-clock authority and
partial-result duplicate admission clauses of
[reconciliation](../architecture/modules/reconciliation.md#5-replica-ownership-and-transactions).
All other reconciliation predicates and
[database compatibility](../architecture/cross-cutting/database-compatibility.md)
retain their existing owners. The predecessor remains unchanged.

Three behaviors become stricter: an expired worker cannot write after waiting
for a row lock, a partial result replay cannot add terminal evidence, and a
declared economics capability without its required database facts cannot start
a runtime transaction. Valid plans, effect-preserving replay, audit bytes,
provider permissions and configuration policy do not change.

## Counterexamples

For acquisition time `A`, expiry `E`, application-clock sample `t0` and row-lock
completion `t1`, the old implementation admits this schedule:

```text
A <= t0 < E <= t1
same token and generation
check(old_sample = t0) = active
write after lock completion
```

No clock skew or competing reclaimer is necessary. CAS alone does not encode
elapsed time. Similarly, `Declared(economics) != Attested(economics)`: retaining
the capability declaration while dropping an index passes the old runtime
dispatcher because it never invokes the existing economics attestor.

## Time And Atomicity

Use three explicitly different instants:

- `O`: event occurrence, retained in audit evidence; it is not write authority.
- `T`: database time obtained by a separate statement after the subject row is
  locked, used by the pure domain transition.
- `K`: PostgreSQL statement time of the conditional mutation issued while that
  same row lock remains held, used by the final authority predicate.

```text
NewEffect(s, c) =>
  RowLocked(s)
  and DomainAdmits(s, c, T)
  and CAS(s.id, revision, generation, token)
  and StoredAcquisition(s) <= K < StoredExpiry(s)
```

Acquisition selects a due unresolved row with `SKIP LOCKED`, then derives the
lease from database time. Its CAS must still find the prior generation eligible
and the newly derived lease unexpired. A lost time predicate produces no claim,
not an expired credential. No caller-supplied `now` enters persistence claim
operations. The pure reconciliation functions retain explicit time inputs.

Append, defer and terminal recording compare the locked token, generation and
time interval again in SQL. Perform the subject CAS before inserting dependent
rows; a failed CAS returns `ReconciliationClaimLost` without those effects.
Successful CAS and dependent observation/result/audit/shadow writes commit in
one existing UnitOfWork. Any exception or cancellation rolls back the complete
transaction. Exact duplicate/conflict classification stays before new-write
admission and does not acquire new mutation authority. `claim_next` and holder
persistence operations no longer accept caller `now`; the injected `Clock`
retains registration and audit-occurrence ownership, not lease admission.

For a duplicate result, terminal audit must already exist as the same event,
and every supplied shadow record must already have the same retained semantics.
An absent audit returns `ReconciliationClaimLost` with rollback required. A
tentative new shadow insert on this path causes bounded whole-UoW cleanup rollback and
the same lost-claim outcome; an evidence conflict remains an invariant error.
No new repository port or boolean write mode is needed. Existing transaction
rollback is sufficient because these writes have no nontransactional side
effects. An omitted supplied record does not delete retained evidence; replay
admission proves no added effect, not completeness of caller-supplied evidence.

After matching the result and terminal audit, evaluate the complete supplied
shadow set before returning a missing-effect outcome. A conflict takes
precedence over an absent record, independent of tuple order. The fold has only
two operands: whether any record conflicts and whether any record is new.
Conflict raises the existing invariant error; otherwise a new record returns
`ReconciliationClaimLost`; otherwise replay is duplicate. Both rejecting paths
roll back. Test both permutations of one new and one conflicting record against
unchanged persisted rows. This bounded fold avoids a second read pass or a new
batch repository abstraction while preserving the original conflict contract.

```text
EffectPreservingReplay := ResultDuplicate
  and ExistingExactTerminalAudit
  and ForEverySuppliedShadowRecord(ExistingSameSemantics)

ResultDuplicate and not EffectPreservingReplay => no committed mutation
```

`K` is an admission linearization point, not a promise that COMMIT occurs before
expiry. A successful transaction can finish later while holding its lock; a
contender cannot observe or modify its intermediate state. Existing transaction,
statement and cleanup bounds still apply. No PostgreSQL lock spans provider I/O.

PostgreSQL `statement_timestamp()` is fixed at the start of its statement, while
`clock_timestamp()` reads current database time. Embedding either timestamp in
the original blocking SELECT does not prove post-lock evaluation. The separate
post-lock sample and conditional UPDATE remove that ambiguity.
[PostgreSQL time semantics](https://www.postgresql.org/docs/18/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT).

CI economics already checks expiry again in its subsequent holder UPDATE.
Therefore its earlier SELECT timestamp is not the same admitted counterexample;
this batch does not rewrite that independent lifecycle.

## Schema Admission

Call `ci_economics_schema_matches_contract` whenever the resolved required
capabilities contain `ci-economics-evidence/v1`. Reuse its existing catalog checks and
the existing compatibility fence, restricted-principal and PUBLIC ACL checks.
Do not duplicate its schema tuples or cache admission across an unproved schema
generation. Both economics and webhook-ingestion transactions require it.

## Alternatives And Cost

| Alternative                                              | Verdict and reason                                                                                             |
|----------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| Keep application time and add a larger lease             | Rejected: an unbounded pause still crosses any finite lease.                                                   |
| Check time only after SELECT                             | Insufficient: the worker may pause before its UPDATE.                                                          |
| Rely on generation CAS                                   | Insufficient: time can expire without a generation change.                                                     |
| Repair missing terminal evidence during duplicate replay | Rejected: result equality cannot reissue an expired worker's write authority.                                  |
| Add database time and final SQL expiry admission         | Selected: closes both schedules within the current transaction owner.                                          |
| Move the domain state machine into stored procedures     | Not justified: it duplicates transition policy without a demonstrated atomicity gap after the selected repair. |
| Trust migration-time economics attestation               | Rejected: it cannot witness the current runtime schema.                                                        |

The repair adds one small database-time query per claimed reconciliation
operation and the existing economics catalog queries to applicable UoWs.
These costs are explicit correctness costs, not performance improvements.
Measure them in B3 before considering equivalent generation-scoped admission.
Pydantic can admit structured input; it cannot replace row-lock ordering,
database facts or transaction-level race witnesses.

## Writer Readiness And Proof

| Owner                            | Delta and protected observations                                                                 | Derived surfaces                                                                               | Cheapest whole-chain gate and independent validator                                                                                                     |
|----------------------------------|--------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| Reconciliation persistence       | Remove ambient clock authority; preserve domain outcomes, identity and atomic effects            | Runtime composition/adapters, SQL selection, pair repository, native tests and Proofkit routes | Static typing/import/route checks, then exact-head PostgreSQL/full CI; independent frozen-diff reviewer plus database concurrency oracles               |
| Database compatibility admission | Invoke the existing economics attestor; preserve restricted-role and compatibility-lock behavior | Runtime dispatcher and native capability-tamper witnesses                                      | Negative schema facts with declaration retained in both UoWs, then exact-head CI; direct catalog tampering independent of the expected-schema generator |

For authority, independently vary time, generation, token, revision and subject;
hold the other operands fixed. Test a blocked lock with an unchanged expired
claim, a pause after the domain sample but before CAS, reclaim ordering, valid
completion and duplicate replay. Use database-controlled fixture transitions
and observable lock barriers, not workstation wall-clock travel or correctness
assertions based only on sleeps. Pure boundary tests retain `E-epsilon`, `E`
and `E+epsilon`; PostgreSQL tests establish adapter ordering and atomicity.
Retain a positive result/audit/shadow replay, and independently omit audit,
omit original shadow, or append a new surface after terminal completion. The
whole retained state must remain unchanged on each rejected replay.

For capability admission, independently remove a constraint, index and trigger,
and weaken an ACL while keeping the declaration. A runtime repository must not
be yielded. Include an unchanged-schema positive path, restoration and existing
forward-migration witnesses. No migration source is edited.

Behavioral tests run in GitHub, not on the workstation. Before publication,
recheck existing design/plan tree entries and exact Proofkit command routes.
Passing those static gates alone does not close the runtime proof.

## Revision Conditions

Revisit this design if isolation level, row-lock scope, transaction ownership,
database clock discipline, lease policy, provider-I/O placement or duplicate
semantics changes. A measured catalog-query cost can justify a new equivalent
admission mechanism, never silent deletion of schema proof. No global optimum,
clock-failure immunity, deployment readiness or future correctness is claimed.
