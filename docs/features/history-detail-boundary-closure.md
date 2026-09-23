# History Detail Boundary Closure

Status: bounded corrective design.

This increment qualifies the detail capability from
[History Review Convergence](history-review-convergence.md). It does not change
retention, statistics, authorization, schema, queues or step-order admission.
The [implementation plan](history-detail-boundary-closure-plan.md) owns delivery.

## Decision

Canonicalize complete provider job details by their numeric job identity before
constructing `ArchivedAttemptDetail`. Keep duplicate detection and all existing
limits. Reuse current application, PostgreSQL and HTTP tests to distinguish
nonempty recheck forwarding, contradictory replay and successful serialization.

## Counterexample And Proof

The [GitHub attempt-jobs endpoint](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt)
specifies pagination, but no ascending job-ID guarantee. The adapter already
sorts permanent job statistics. Previously, valid detail jobs arriving in the
order `[2, 1]` failed the detail model's ordered-identity guard, silently leaving
only the independently valid statistics. This is a contract-level countermodel,
not a claim that a particular live GitHub response used that order.

For an admitted complete population `J` with unique job IDs and a permutation
`p`, define `C(J) = sort(J, provider_job_id)`:

```text
ids(C(p(J))) = ids(C(J))
multiset(C(p(J))) = multiset(J)
duplicate(J) => duplicate(C(J))
```

Sorting changes representation order only. It cannot repair a duplicate,
foreign identity, incomplete population, invalid step, oversized payload or
unstable header. Those existing guards remain authoritative. The same job
ordering as statistics allows the existing identity comparison to succeed.

## Minimality And Cost

Keep one library sort at the provider-to-domain boundary. The population is
already bounded to 2,000 jobs and detail to 262,144 canonical bytes. Sorting is
`O(n log n)` time and `O(n)` auxiliary references, with no added provider request
or SQL operation. A dictionary-based rewrite could discard duplicate evidence;
relaxing domain validation would lose canonical identity. Neither is needed.
No new production abstraction is introduced.

## Independent Oracles

| Owner       | Required observation                                                                            | Distinguishing counterexample                                                     |
|-------------|-------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| Provider    | Same canonical detail for valid job permutations, including multiple pages                      | Drop the normalization; reverse only job order while keeping IDs/steps valid      |
| Application | Nonempty detail reaches backfill, recent and repair completion                                  | Omit only the recheck detail argument                                             |
| Persistence | Recheck commits exact bytes once, charges exact quota and preserves its first-import anchor     | Drop detail, exceed only its byte allowance, or lose terminal lease after writing |
| Replay      | Retained bytes, policy, expiry, anchor, quota and revision remain unchanged                     | Supply independently valid different detail with the same encoded length          |
| HTTP        | Successful null and populated payloads preserve unequal scope, attempt, generation and no-store | Return a missing/incorrect payload or use another query identity                  |

A shared archive test factory removes duplicate builders. The HTTP expected
payload is literal; storage checks compare committed bytes with the admitted
input encoding, including a distinct equal-length replay. Existing scope
locks serialize configuration, import and cleanup. Their source inspection is
not a claim of exhaustive concurrent-runtime qualification.

## Revisit And Boundaries

Revisit if provider job order acquires an admitted business meaning, population
bounds change, or an equivalent current oracle makes a proposed test redundant.
The current step-order guard is unchanged. New detail import during a live
pilot still needs deployment, current policy and operator authority. This
increment does not prove complete history, production capacity or omission.
