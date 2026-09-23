# Repository Agent Contract

Independent batch reviews use `gpt-6-astra` with `max` reasoning unless the user
explicitly overrides this selection. Normally use one reviewer; add a further
pass only for a material unresolved finding or an uncovered independent scope.
Do not silently substitute an unavailable model. This current selection takes
precedence over model names in earlier implementation plans; their proof
obligations remain unchanged. New plans reference this policy instead of
duplicating model selection.

Before architecture or decomposition work, read
`docs/architecture/cross-cutting/module-ownership-and-decomposition.md` and its
machine profile. Treat size, export count, coupling, complexity, and churn only
as review-selection signals.

A semantic architecture failure requires an applicable repository rule, a
proved forbidden co-ownership, preserved observables and hard constraints, a
stable responsibility set, no defeating required-colocation rule, a strictly
preferable complete and safe decomposition, an exact profile/base/head epoch, a
new or worsened delta, no active waiver, and conclusive typed evidence. Unknown
or conflicting evidence requires `ABSTAIN`; metrics alone cannot exceed
`REVIEW_REQUIRED`.

External review kits provide candidate methodology only. They do not replace
repository-owned policy, evidence, or merge authority.

Keep production and test candidate cohorts separate. Candidate queues are
unbounded by default; an explicit cap must report completeness and every
cap-deferred unit remains `ABSTAIN`.

Before each material decision, compare the simplest sufficient trajectory with
the proposed one under the current owner, protected observables and hard
constraints. Record why it is preferable, its cost, falsifiers and revision
conditions; do not claim a global optimum.

For a repeated disputed argument, consult
`docs/decisions/review-decision-reuse.md` and its decision register. Revalidate
the exact argument, scope, assumptions and triggers. A reference-current record
does not waive review of the file, a new counterexample or another quality
dimension. Unknown applicability requires fresh review.

Prefer admitted library/CLI capabilities over new manual mechanisms when their
contracts preserve the required behavior. Pydantic validates data boundaries;
it does not prove database authority, transaction ordering or provider effects.
