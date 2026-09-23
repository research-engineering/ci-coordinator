# Historical Job Projection

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

Status: implementation design; native and deployment qualification pending

Owner: `ci_economics`, implemented at the GitHub archive admission boundary.
Delivery: [implementation plan](history-job-projection-plan.md).

## Decision

Admit only the provider fields needed by `ArchivedJobStatistics`, separately
from the stricter named runner identity used by reconciliation and active
economics. Reuse strict JSON primitives and the existing Pydantic archive
model. No new public schema, provider request, persistence change or permissive
mode is introduced.

The archive is an informational projection, not evidence authorizing omission.
Its identity, page, state and numeric constraints remain mandatory. Display
names, actors, steps and arbitrary extra provider fields are not retained and
cannot decide whether that projection is admitted.

## Counterexample And Boundary

A synthetic counterexample supplies a positive runner ID/name, a null group ID
and a provider group display name. A shared decoder that requires paired IDs
and names rejects the page even though the archive stores no runner/group
names and admits that numeric pair. This is an input-contract scenario, not
an exported private run or job receipt.

Let `P` be the archive projection, `I` its exact run/head/attempt binding,
`A` its admitted terminal job and bounded field predicates, and `N` the
discarded display-name fields:

```text
ArchiveAdmitted(x) := StrictJson(x) and I(x) and A(P(x))
P(x) = P(y) and I(x) = I(y) and only N differs
  => ArchiveAdmitted(x) = ArchiveAdmitted(y)

ArchiveAdmitted(x) !=> ReconciliationEvidenceAdmitted(x)
```

The current shared decoder violates the first relation. Removing archive
dependence on it repairs the boundary without changing the second relation.
This is a scoped counterexample and design argument, not a claim that every
GitHub payload shape is supported.

## Protected Observations

| Operand          | Admission or projection                                                                                          |
|------------------|------------------------------------------------------------------------------------------------------------------|
| Run and head     | Exact current cursor run and admitted header head                                                                |
| Optional attempt | Absent remains allowed; every present value must be the exact positive integer attempt                           |
| Job ID           | Positive JSON-safe integer; no bool, float or string coercion                                                    |
| State            | Known active state with null conclusion is deferred; otherwise require completed and a known terminal conclusion |
| Page             | Exact nonnegative total, JSON array, at most the existing 100 jobs                                               |
| Labels           | Exact JSON list of distinct bounded strings, stored in sorted order                                              |
| Runner/group ID  | Null, missing and integer zero mean unknown; positive IDs are retained independently of names                    |
| Group relation   | A known group ID still requires a known runner ID                                                                |
| Timestamps       | Strict admitted strings or null; preserve inconsistent values, never clamp or infer them                         |
| Other fields     | Discard; no display name or private detail in canonical bytes                                                    |

Pydantic remains the owner of permanent field domains and cross-field rules.
The adapter owns provider representation, exact resource binding, terminality
and zero-to-unknown normalization. Sorting already admitted labels changes
representation only and is idempotent. Unknown timings remain unknown under
the existing analytics contract; no new exact duration is manufactured.

Pagination, duplicate IDs, changing totals, two-header stability, request/body
bounds, deadlines, hard leases, retention, refinement and atomic writes remain
unchanged. Reconciliation and active-economics decoding remain byte-identical.

## Alternatives And Revision Conditions

1. Keep the shared decoder: rejected by the observed valid archive projection.
2. Relax `RunnerIdentity` globally: changes independent active-evidence policy.
3. Sanitize names in raw JSON before shared decoding: retains an irrelevant
   dependency, requires a second representation and conceals the actual boundary.
4. Add a permissive decoder flag or a second wire-model hierarchy: unnecessary
   mechanism; direct projection into the existing archive model is sufficient.

The selected route adds one local projection while removing double JSON parsing
and an inappropriate dependency. No throughput claim follows without measurement.
It is reversible without data migration: existing permanent bytes are unchanged,
and newly admitted records satisfy the same archive schema. Reverting may again
reject these provider responses; it cannot erase already admitted statistics.

Reconsider when the archive needs named runner provenance, when the permanent
model changes its identity meaning, or when a shared provider projection can
preserve both consumer contracts without permissive modes. A forged retained
identity, altered numeric value or newly exact inconsistent duration accepted
by this path would falsify the design. Historical failed work follows ordinary
hint/rescan recovery, not a manual database rewrite or quota reset.
