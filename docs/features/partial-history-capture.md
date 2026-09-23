# Partial History Capture

Status: feature design

## Decision

Preserve an independently admitted completed attempt even when GitHub returns
nonterminal jobs. Store its terminal job subset with the existing partial
population contract. Do not invent child conclusions, timings or CI evidence.

This extends archive provider admission under REQ-CI-RUNTIME-046. The existing
[storage information order](actions-history-storage.md#idempotency-and-conflict)
and REQ-CI-RUNTIME-047 remain unchanged. Implementation and sensitive native
witnesses are listed in the [plan](partial-history-capture-plan.md).

## Context

A completed GitHub run can coexist with queued child jobs. Deferring the whole
attempt then discards useful, independently known header facts. The existing
archive model already represents complete, partial and unavailable populations;
an additional nullable-job schema would duplicate that distinction.

The [exact-attempt API](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt)
binds run and attempt, not an assertion that every returned child is terminal.
A parent conclusion is not a child's conclusion. The active-evidence provider
keeps its separate stricter admission contract.

## Observation Model

For stable provider total N, observed identities S and retained terminal jobs T:

```text
T subset S
|S| <= N
complete iff |T| = N
known N > |T| implies partial
```

Every observed job first passes exact positive identity, run, head, optional
attempt and status/conclusion admission. A recognized active status with null
conclusion contributes only its identity to S. Discarded active display fields
cannot become statistical facts. Unknown status or contradictory conclusion
rejects the response. Terminal jobs retain all existing field validation.

Pagination and duplicate checks use S, never only T. Otherwise an all-active
page could appear empty or hide a duplicate on a later page. Every page retains
the existing size, response-binding, total and next-link checks. The final
attempt header must still equal the initial terminal header.

Empty T with positive N is partial; N zero with empty T is complete. Unknown
measurements remain absent, never numerical zero. Page and canonical-byte
bounds still stop enumeration truthfully without asserting completeness.

## Storage And Recovery

The existing completion transaction stores partial facts and the
job_population_incomplete gap atomically under the current dataset lease.
Later recent discovery or an explicit repair can offer a more informative
response. The information-order validator admits refinement only when every
previously known operand and member is preserved; it does not union incomparable
responses or erase conflicts. Refinement preserves original import clocks and
charges only the exact row and byte delta.

No new retry scheduler is introduced. Exhausted work, a permanently queued
child or missing access can leave an explicit unresolved gap. This design does
not promise eventual provider convergence or extend retention windows.

## Ownership And Alternatives

| Owner                | Responsibility                        | Protected boundary               |
|----------------------|---------------------------------------|----------------------------------|
| History decoder      | Observed identity and terminal subset | No fabricated child facts        |
| History provider     | Stable headers, pages and total       | Bounded, exact attempt traversal |
| Archive domain/store | Existing population/refinement        | Known facts, clocks, quota, CAS  |
| Active CI evidence   | Existing independent admission        | No additional omission authority |

Keeping unconditional deferral is simpler but loses valid parent facts.
Inferring terminal children from their parent violates identity-bound evidence.
Adding active jobs to permanent terminal statistics expands the schema without
adding an observation that the existing partial model cannot express. Unbounded
retries trade an honest gap for unbounded provider work. These alternatives are
not selected.

The chosen change adds a bounded set of seen IDs alongside retained jobs;
both are bounded by the existing attempt/page ceiling. No schema, pool, timeout,
permission, isolation or deployment change is required. Revisit if partial facts
cannot support truthful analytics or an owner requires active-job telemetry as
a separate product capability. No performance gain is claimed without measurement.
